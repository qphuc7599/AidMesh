"""Temporal-contact DTN simulator for settlement bundle delivery.

This engine models receipt bundles moving over time-varying contacts with finite
contact capacity, per-node buffers, TTL expiry and routing policies.  It is not
a replacement for field traces or The ONE, but it gives the paper artifact a
deterministic, inspectable DTN layer instead of a pure delivery-probability stub.
"""

from __future__ import annotations

import csv
import math
import random
from collections import defaultdict
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from aidmesh.common.canonical import stable_hash

HOUR_US = 3_600_000_000


class RoutingPolicy(StrEnum):
    FIFO = "fifo_single_copy"
    EPIDEMIC = "epidemic"
    SPRAY_WAIT = "binary_spray_wait"
    RISK_PRIORITY = "risk_priority_spray"


@dataclass(frozen=True)
class Contact:
    start_us: int
    end_us: int
    node_a: str
    node_b: str
    bytes_per_second: int

    @property
    def capacity_bytes(self) -> int:
        duration_us = max(0, self.end_us - self.start_us)
        return int((duration_us / 1_000_000) * self.bytes_per_second)


@dataclass(frozen=True)
class BundleSpec:
    bundle_id: str
    source: str
    destination: str
    created_us: int
    ttl_us: int
    size_bytes: int
    risk_score: float

    @property
    def expires_us(self) -> int:
        return self.created_us + self.ttl_us


@dataclass(frozen=True)
class DeliveryRecord:
    bundle_id: str
    delivered_us: int
    delay_us: int
    hops: int
    bytes_forwarded: int


@dataclass(frozen=True)
class DtnResult:
    policy: str
    bundles: int
    delivered: int
    expired: int
    dropped: int
    forwarded_bytes: int
    duplicate_copies: int
    delivery_ratio: float
    risk_weighted_delivery_ratio: float
    mean_delivered_risk_score: float | None
    mean_delay_hours: float | None
    p95_delay_hours: float | None
    event_checksum: str
    deliveries: tuple[DeliveryRecord, ...]

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["deliveries"] = [asdict(item) for item in self.deliveries]
        return value


@dataclass
class _CopyState:
    node_id: str
    bundle_id: str
    received_us: int
    hops: int
    copies: int
    bytes_forwarded: int


def generate_synthetic_contacts(
    *,
    seed: int,
    sites: int,
    relays: int,
    horizon_us: int,
    partition_us: int,
    contact_interval_us: int = 30 * 60 * 1_000_000,
    contact_duration_us: int = 6 * 60 * 1_000_000,
    bytes_per_second: int = 24_000,
) -> tuple[Contact, ...]:
    rng = random.Random(seed)
    contacts: list[Contact] = []
    relay_nodes = [f"relay-{index}" for index in range(relays)]
    for tick in range(0, horizon_us, contact_interval_us):
        for site in range(sites):
            site_id = f"site-{site}"
            relay = relay_nodes[(site + tick // contact_interval_us) % max(1, relays)]
            post_partition = tick >= partition_us
            p_site_relay = 0.45 if post_partition else 0.75
            if rng.random() < p_site_relay:
                jitter = rng.randrange(0, max(1, contact_interval_us // 4))
                start = min(horizon_us, tick + jitter)
                contacts.append(
                    Contact(
                        start,
                        min(horizon_us, start + contact_duration_us),
                        site_id,
                        relay,
                        bytes_per_second,
                    )
                )
            if not post_partition and rng.random() < 0.65:
                start = min(horizon_us, tick + rng.randrange(0, max(1, contact_interval_us // 5)))
                contacts.append(
                    Contact(
                        start,
                        min(horizon_us, start + contact_duration_us),
                        site_id,
                        "contract",
                        bytes_per_second * 2,
                    )
                )
        for relay in relay_nodes:
            p_relay_contract = 0.35 if tick >= partition_us else 0.8
            if rng.random() < p_relay_contract:
                start = min(horizon_us, tick + rng.randrange(0, max(1, contact_interval_us // 3)))
                contacts.append(
                    Contact(
                        start,
                        min(horizon_us, start + contact_duration_us),
                        relay,
                        "contract",
                        bytes_per_second * 3,
                    )
                )
        if sites > 1 and rng.random() < 0.25:
            left = rng.randrange(sites)
            right = (left + 1 + rng.randrange(sites - 1)) % sites
            start = min(horizon_us, tick + rng.randrange(0, max(1, contact_interval_us // 2)))
            contacts.append(
                Contact(
                    start,
                    min(horizon_us, start + contact_duration_us),
                    f"site-{left}",
                    f"site-{right}",
                    bytes_per_second // 2,
                )
            )
    return tuple(sorted(contacts, key=lambda item: (item.start_us, item.node_a, item.node_b)))


def generate_receipt_bundles(
    *,
    seed: int,
    count: int,
    sites: int,
    horizon_us: int,
    ttl_us: int,
    payload_bytes: int = 1024,
) -> tuple[BundleSpec, ...]:
    rng = random.Random(seed)
    bundles: list[BundleSpec] = []
    creation_window = max(1, int(horizon_us * 0.7))
    for index in range(count):
        site = rng.randrange(sites)
        created = rng.randrange(0, creation_window)
        risk_score = round(0.15 + 0.85 * rng.random(), 6)
        bundles.append(
            BundleSpec(
                bundle_id=f"bundle-{seed}-{index:05d}",
                source=f"site-{site}",
                destination="contract",
                created_us=created,
                ttl_us=ttl_us,
                size_bytes=payload_bytes,
                risk_score=risk_score,
            )
        )
    return tuple(sorted(bundles, key=lambda item: (item.created_us, item.bundle_id)))


def load_contacts_csv(path: Path) -> tuple[Contact, ...]:
    """Load external contacts from a normalized CSV trace.

    Required columns: start_us, end_us, node_a, node_b, bytes_per_second.
    """

    contacts: list[Contact] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"start_us", "end_us", "node_a", "node_b", "bytes_per_second"}
        missing = required - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"contact trace missing columns: {sorted(missing)}")
        for row_number, row in enumerate(reader, start=2):
            contact = Contact(
                start_us=int(row["start_us"]),
                end_us=int(row["end_us"]),
                node_a=str(row["node_a"]),
                node_b=str(row["node_b"]),
                bytes_per_second=int(row["bytes_per_second"]),
            )
            if contact.start_us < 0 or contact.end_us <= contact.start_us:
                raise ValueError(f"invalid contact time interval at row {row_number}")
            if not contact.node_a or not contact.node_b or contact.node_a == contact.node_b:
                raise ValueError(f"invalid contact endpoints at row {row_number}")
            if contact.bytes_per_second <= 0:
                raise ValueError(f"invalid contact bandwidth at row {row_number}")
            contacts.append(contact)
    return tuple(sorted(contacts, key=lambda item: (item.start_us, item.node_a, item.node_b)))


def load_bundles_csv(path: Path) -> tuple[BundleSpec, ...]:
    """Load receipt bundle workload from CSV.

    Required columns: bundle_id, source, destination, created_us, ttl_us,
    size_bytes, risk_score.
    """

    bundles: list[BundleSpec] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {
            "bundle_id",
            "source",
            "destination",
            "created_us",
            "ttl_us",
            "size_bytes",
            "risk_score",
        }
        missing = required - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"bundle workload missing columns: {sorted(missing)}")
        seen: set[str] = set()
        for row_number, row in enumerate(reader, start=2):
            bundle = BundleSpec(
                bundle_id=str(row["bundle_id"]),
                source=str(row["source"]),
                destination=str(row["destination"]),
                created_us=int(row["created_us"]),
                ttl_us=int(row["ttl_us"]),
                size_bytes=int(row["size_bytes"]),
                risk_score=float(row["risk_score"]),
            )
            if not bundle.bundle_id or bundle.bundle_id in seen:
                raise ValueError(f"duplicate or empty bundle_id at row {row_number}")
            if not bundle.source or not bundle.destination:
                raise ValueError(f"invalid bundle endpoints at row {row_number}")
            if bundle.created_us < 0 or bundle.ttl_us <= 0 or bundle.size_bytes <= 0:
                raise ValueError(f"invalid bundle timing/size at row {row_number}")
            if not 0 <= bundle.risk_score <= 1:
                raise ValueError(f"risk_score outside [0,1] at row {row_number}")
            seen.add(bundle.bundle_id)
            bundles.append(bundle)
    return tuple(sorted(bundles, key=lambda item: (item.created_us, item.bundle_id)))


class TemporalContactSimulator:
    def __init__(
        self,
        *,
        contacts: tuple[Contact, ...],
        bundles: tuple[BundleSpec, ...],
        policy: RoutingPolicy,
        buffer_bytes: int,
        copy_budget: int = 4,
    ) -> None:
        if buffer_bytes <= 0:
            raise ValueError("buffer_bytes must be positive")
        if copy_budget <= 0:
            raise ValueError("copy_budget must be positive")
        self.contacts = tuple(
            sorted(
                contacts,
                key=lambda item: (item.start_us, item.end_us, item.node_a, item.node_b),
            )
        )
        self.bundles = tuple(sorted(bundles, key=lambda item: (item.created_us, item.bundle_id)))
        self.policy = policy
        self.buffer_bytes = buffer_bytes
        self.copy_budget = copy_budget
        self._bundle_by_id = {bundle.bundle_id: bundle for bundle in self.bundles}

    def run(self) -> DtnResult:
        buffers: dict[str, dict[str, _CopyState]] = defaultdict(dict)
        deliveries: dict[str, DeliveryRecord] = {}
        pending_index = 0
        dropped = 0
        forwarded_bytes = 0

        def admit_created(now_us: int) -> None:
            nonlocal pending_index, dropped
            while (
                pending_index < len(self.bundles)
                and self.bundles[pending_index].created_us <= now_us
            ):
                bundle = self.bundles[pending_index]
                pending_index += 1
                if bundle.source == bundle.destination:
                    deliveries[bundle.bundle_id] = DeliveryRecord(
                        bundle.bundle_id,
                        bundle.created_us,
                        0,
                        0,
                        0,
                    )
                    continue
                buffers[bundle.source][bundle.bundle_id] = _CopyState(
                    bundle.source,
                    bundle.bundle_id,
                    bundle.created_us,
                    0,
                    self.copy_budget,
                    0,
                )
                dropped += self._enforce_buffer(buffers[bundle.source], now_us)

        def expire(now_us: int) -> None:
            for node_buffer in buffers.values():
                for bundle_id in list(node_buffer):
                    bundle = self._bundle_by_id[bundle_id]
                    if now_us > bundle.expires_us or bundle_id in deliveries:
                        del node_buffer[bundle_id]

        for contact in self.contacts:
            admit_created(contact.start_us)
            expire(contact.start_us)
            if contact.capacity_bytes <= 0:
                continue
            forwarded_bytes += self._transmit(
                buffers, deliveries, contact, contact.node_a, contact.node_b
            )
            forwarded_bytes += self._transmit(
                buffers, deliveries, contact, contact.node_b, contact.node_a
            )
            dropped += self._enforce_buffer(buffers[contact.node_a], contact.end_us)
            dropped += self._enforce_buffer(buffers[contact.node_b], contact.end_us)

        horizon_us = max(
            [contact.end_us for contact in self.contacts]
            + [bundle.created_us for bundle in self.bundles]
            + [0]
        )
        admit_created(horizon_us)
        expire(horizon_us)
        delivered_records = tuple(sorted(deliveries.values(), key=lambda item: item.bundle_id))
        delivered_ids = {record.bundle_id for record in delivered_records}
        total_risk = sum(bundle.risk_score for bundle in self.bundles)
        delivered_risk = sum(
            bundle.risk_score for bundle in self.bundles if bundle.bundle_id in delivered_ids
        )
        expired = sum(
            bundle.bundle_id not in deliveries and bundle.expires_us <= horizon_us
            for bundle in self.bundles
        )
        delays = sorted(record.delay_us / HOUR_US for record in delivered_records)
        total_copies = sum(len(buffer) for buffer in buffers.values())
        unique_buffered = {bundle_id for buffer in buffers.values() for bundle_id in buffer}
        result_payload = {
            "policy": self.policy.value,
            "deliveries": delivered_records,
            "forwarded_bytes": forwarded_bytes,
            "dropped": dropped,
        }
        return DtnResult(
            policy=self.policy.value,
            bundles=len(self.bundles),
            delivered=len(delivered_records),
            expired=expired,
            dropped=dropped,
            forwarded_bytes=forwarded_bytes,
            duplicate_copies=max(0, total_copies - len(unique_buffered)),
            delivery_ratio=len(delivered_records) / len(self.bundles) if self.bundles else 0.0,
            risk_weighted_delivery_ratio=(delivered_risk / total_risk if total_risk else 0.0),
            mean_delivered_risk_score=(
                delivered_risk / len(delivered_records) if delivered_records else None
            ),
            mean_delay_hours=sum(delays) / len(delays) if delays else None,
            p95_delay_hours=_quantile(delays, 0.95) if delays else None,
            event_checksum=stable_hash(result_payload, domain="aidmesh/dtn/result/v1"),
            deliveries=delivered_records,
        )

    def _transmit(
        self,
        buffers: dict[str, dict[str, _CopyState]],
        deliveries: dict[str, DeliveryRecord],
        contact: Contact,
        sender: str,
        receiver: str,
    ) -> int:
        capacity = contact.capacity_bytes
        used = 0
        for copy in self._ranked_candidates(buffers[sender].values(), contact.start_us):
            # A bundle received at the end of another overlapping/same-start
            # contact cannot be forwarded retroactively at this contact's start.
            if copy.received_us > contact.start_us:
                continue
            if copy.bundle_id in deliveries:
                continue
            bundle = self._bundle_by_id[copy.bundle_id]
            if bundle.expires_us < contact.end_us:
                continue
            if copy.bundle_id in buffers[receiver]:
                continue
            if not self._policy_allows_forward(copy, bundle, receiver):
                continue
            if used + bundle.size_bytes > capacity:
                continue
            if receiver == bundle.destination:
                deliveries[bundle.bundle_id] = DeliveryRecord(
                    bundle.bundle_id,
                    contact.end_us,
                    contact.end_us - bundle.created_us,
                    copy.hops + 1,
                    copy.bytes_forwarded + bundle.size_bytes,
                )
                if self.policy == RoutingPolicy.FIFO:
                    buffers[sender].pop(copy.bundle_id, None)
            else:
                forwarded = _CopyState(
                    receiver,
                    copy.bundle_id,
                    contact.end_us,
                    copy.hops + 1,
                    self._receiver_copies(copy),
                    copy.bytes_forwarded + bundle.size_bytes,
                )
                buffers[receiver][copy.bundle_id] = forwarded
                if self.policy == RoutingPolicy.FIFO:
                    buffers[sender].pop(copy.bundle_id, None)
                elif self.policy in {RoutingPolicy.SPRAY_WAIT, RoutingPolicy.RISK_PRIORITY}:
                    copy.copies = max(1, copy.copies - forwarded.copies)
            used += bundle.size_bytes
        return used

    def _ranked_candidates(self, copies: Any, now_us: int) -> list[_CopyState]:
        values = list(copies)
        if self.policy == RoutingPolicy.RISK_PRIORITY:
            return sorted(
                values,
                key=lambda item: (
                    -self._bundle_by_id[item.bundle_id].risk_score,
                    self._bundle_by_id[item.bundle_id].expires_us - now_us,
                    item.received_us,
                    item.bundle_id,
                ),
            )
        return sorted(values, key=lambda item: (item.received_us, item.bundle_id))

    def _policy_allows_forward(self, copy: _CopyState, bundle: BundleSpec, receiver: str) -> bool:
        if receiver == bundle.destination:
            return True
        if self.policy == RoutingPolicy.EPIDEMIC:
            return True
        if self.policy == RoutingPolicy.FIFO:
            return True
        return copy.copies > 1

    def _receiver_copies(self, copy: _CopyState) -> int:
        if self.policy == RoutingPolicy.EPIDEMIC:
            return 1
        if self.policy == RoutingPolicy.FIFO:
            return copy.copies
        return max(1, copy.copies // 2)

    def _enforce_buffer(self, node_buffer: dict[str, _CopyState], now_us: int) -> int:
        dropped = 0
        while self._buffer_size(node_buffer) > self.buffer_bytes and node_buffer:
            victim = self._eviction_victim(node_buffer.values(), now_us)
            del node_buffer[victim.bundle_id]
            dropped += 1
        return dropped

    def _eviction_victim(self, copies: Any, now_us: int) -> _CopyState:
        if self.policy == RoutingPolicy.RISK_PRIORITY:
            return max(
                copies,
                key=lambda item: (
                    -self._bundle_by_id[item.bundle_id].expires_us + now_us,
                    -self._bundle_by_id[item.bundle_id].risk_score,
                    item.received_us,
                ),
            )
        return min(copies, key=lambda item: (item.received_us, item.bundle_id))

    def _buffer_size(self, node_buffer: dict[str, _CopyState]) -> int:
        return sum(self._bundle_by_id[bundle_id].size_bytes for bundle_id in node_buffer)


def _quantile(values: list[float], q: float) -> float:
    if not values:
        raise ValueError("values cannot be empty")
    if len(values) == 1:
        return values[0]
    index = (len(values) - 1) * q
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return values[int(index)]
    weight = index - lower
    return values[lower] * (1 - weight) + values[upper] * weight
