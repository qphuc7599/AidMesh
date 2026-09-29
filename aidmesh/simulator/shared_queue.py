"""Causal multi-class DTN simulator with one shared finite-capacity queue.

Unlike :mod:`aidmesh.simulator.dtn`, this engine permits protocol messages to be
created by earlier deliveries.  Request, quorum, right-transfer, receipt, and
recovery bundles therefore consume the same contact capacity and node buffers.
The implementation is deterministic: losses, queue ordering, and copy splitting
are functions of the immutable bundle/contact identifiers.
"""

from __future__ import annotations

import heapq
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any

from aidmesh.common.canonical import stable_hash
from aidmesh.simulator.dtn import Contact, RoutingPolicy


class MessageClass(StrEnum):
    REQUEST = "request"
    QUORUM_REQUEST = "quorum_request"
    QUORUM_SHARE = "quorum_share"
    RIGHT_TRANSFER = "right_transfer"
    RIGHT_PULL = "right_pull"
    RIGHT_DECISION_REQUEST = "right_decision_request"
    RIGHT_DECISION_SHARE = "right_decision_share"
    PERIODIC_SYNC = "periodic_sync"
    RECEIPT = "receipt"
    RECOVERY = "recovery"
    LOCAL_TIMER = "local_timer"


DEFAULT_CLASS_PRIORITY: dict[MessageClass, int] = {
    MessageClass.QUORUM_SHARE: 0,
    MessageClass.QUORUM_REQUEST: 1,
    MessageClass.RIGHT_DECISION_SHARE: 2,
    MessageClass.RIGHT_DECISION_REQUEST: 3,
    MessageClass.RIGHT_TRANSFER: 4,
    MessageClass.RIGHT_PULL: 4,
    MessageClass.PERIODIC_SYNC: 5,
    MessageClass.REQUEST: 6,
    MessageClass.RECEIPT: 7,
    MessageClass.RECOVERY: 8,
    MessageClass.LOCAL_TIMER: -1,
}


@dataclass(frozen=True)
class SharedBundleSpec:
    bundle_id: str
    message_class: MessageClass
    source: str
    destination: str
    created_us: int
    ttl_us: int
    size_bytes: int
    risk_score: float = 0.0
    correlation_id: str = ""
    payload: tuple[tuple[str, str], ...] = ()

    @property
    def expires_us(self) -> int:
        return self.created_us + self.ttl_us

    def payload_dict(self) -> dict[str, str]:
        return dict(self.payload)


@dataclass(frozen=True)
class SharedDeliveryRecord:
    bundle_id: str
    message_class: str
    source: str
    destination: str
    created_us: int
    delivered_us: int
    delay_us: int
    hops: int
    bytes_forwarded: int
    correlation_id: str


@dataclass(frozen=True)
class SharedNetworkSnapshot:
    now_us: int
    buffered_bytes: int
    buffered_bundles: int
    buffered_bytes_by_class: Mapping[str, int]
    delivered_by_class: Mapping[str, int]
    dropped_by_class: Mapping[str, int]


@dataclass(frozen=True)
class SharedDtnResult:
    bundles_created: int
    delivered: int
    expired: int
    dropped: int
    forwarded_bytes: int
    peak_buffer_bytes: int
    peak_buffer_bundles: int
    created_by_class: Mapping[str, int]
    delivered_by_class: Mapping[str, int]
    dropped_by_class: Mapping[str, int]
    forwarded_bytes_by_class: Mapping[str, int]
    peak_buffer_bytes_by_node: Mapping[str, int]
    contact_used_bytes: Mapping[str, int]
    contact_used_bytes_by_class: Mapping[str, Mapping[str, int]]
    contact_capacity_bytes: Mapping[str, int]
    event_checksum: str
    deliveries: tuple[SharedDeliveryRecord, ...]

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["deliveries"] = [asdict(item) for item in self.deliveries]
        return result


@dataclass
class _CopyState:
    node_id: str
    bundle_id: str
    received_us: int
    hops: int
    copies: int
    bytes_forwarded: int


DeliveryCallback = Callable[
    [SharedDeliveryRecord, SharedBundleSpec, SharedNetworkSnapshot],
    Iterable[SharedBundleSpec],
]


class SharedTemporalContactSimulator:
    """Run causal protocol traffic over one shared bidirectional contact budget.

    ``on_delivery`` may emit new bundles.  A bundle whose source and destination
    are equal represents a local timer or local state transition; it consumes no
    network capacity but is delivered at its declared creation time.  All other
    messages share buffers and each contact's *single* byte budget across both
    directions.
    """

    def __init__(
        self,
        *,
        contacts: tuple[Contact, ...],
        bundles: tuple[SharedBundleSpec, ...],
        buffer_bytes: int,
        copy_budget: int = 4,
        routing_policy: RoutingPolicy = RoutingPolicy.SPRAY_WAIT,
        loss_probability: float = 0.0,
        class_loss_probability: Mapping[MessageClass, float] | None = None,
        class_priority: Mapping[MessageClass, int] | None = None,
        on_delivery: DeliveryCallback | None = None,
    ) -> None:
        if buffer_bytes <= 0:
            raise ValueError("buffer_bytes must be positive")
        if copy_budget <= 0:
            raise ValueError("copy_budget must be positive")
        if not 0.0 <= loss_probability <= 1.0:
            raise ValueError("loss_probability must be in [0, 1]")
        self.contacts = tuple(
            sorted(
                contacts,
                key=lambda item: (item.start_us, item.end_us, item.node_a, item.node_b),
            )
        )
        self.initial_bundles = tuple(
            sorted(bundles, key=lambda item: (item.created_us, item.bundle_id))
        )
        self.buffer_bytes = buffer_bytes
        self.copy_budget = copy_budget
        self.routing_policy = RoutingPolicy(routing_policy)
        self.loss_probability = loss_probability
        self.class_loss_probability = dict(class_loss_probability or {})
        if any(
            not 0.0 <= probability <= 1.0
            for probability in self.class_loss_probability.values()
        ):
            raise ValueError("class loss probabilities must be in [0, 1]")
        self.class_priority = dict(DEFAULT_CLASS_PRIORITY)
        if class_priority:
            self.class_priority.update(class_priority)
        self.on_delivery = on_delivery

    def run(self) -> SharedDtnResult:
        specs: dict[str, SharedBundleSpec] = {}
        pending: list[tuple[int, str]] = []
        buffers: dict[str, dict[str, _CopyState]] = defaultdict(dict)
        buffered_bytes_at_node: Counter[str] = Counter()
        buffered_bytes_by_class: Counter[str] = Counter()
        copies_by_bundle: dict[str, set[str]] = defaultdict(set)
        copy_expirations: list[tuple[int, str, str]] = []
        delivered_pending_cleanup: set[str] = set()
        total_buffered_bytes = 0
        total_buffered_bundles = 0
        deliveries: dict[str, SharedDeliveryRecord] = {}
        created_by_class: Counter[str] = Counter()
        delivered_by_class: Counter[str] = Counter()
        dropped_by_class: Counter[str] = Counter()
        forwarded_by_class: Counter[str] = Counter()
        peak_by_node: Counter[str] = Counter()
        peak_buffer_bytes = 0
        peak_buffer_bundles = 0
        contact_used: dict[str, int] = {}
        contact_used_by_class: dict[str, Counter[str]] = {}
        contact_capacity: dict[str, int] = {}

        def contact_key(index: int, contact: Contact) -> str:
            return (
                f"{index}:{contact.start_us}:{contact.end_us}:"
                f"{contact.node_a}:{contact.node_b}"
            )

        def add_bundle(bundle: SharedBundleSpec, *, not_before_us: int = 0) -> None:
            if not bundle.bundle_id or bundle.bundle_id in specs:
                raise ValueError(f"duplicate or empty bundle_id: {bundle.bundle_id!r}")
            if not bundle.source or not bundle.destination:
                raise ValueError("bundle endpoints must be non-empty")
            if bundle.created_us < not_before_us:
                raise ValueError("callback emitted a bundle before its triggering delivery")
            if bundle.ttl_us <= 0 or bundle.size_bytes <= 0:
                raise ValueError("bundle ttl and size must be positive")
            if not 0.0 <= bundle.risk_score <= 1.0:
                raise ValueError("risk_score must be in [0, 1]")
            specs[bundle.bundle_id] = bundle
            heapq.heappush(pending, (bundle.created_us, bundle.bundle_id))
            created_by_class[bundle.message_class.value] += 1

        for bundle in self.initial_bundles:
            add_bundle(bundle)

        def store_copy(node: str, copy: _CopyState) -> None:
            nonlocal total_buffered_bytes, total_buffered_bundles
            if copy.bundle_id in buffers[node]:
                return
            buffers[node][copy.bundle_id] = copy
            size = specs[copy.bundle_id].size_bytes
            message_class = specs[copy.bundle_id].message_class.value
            buffered_bytes_at_node[node] += size
            buffered_bytes_by_class[message_class] += size
            total_buffered_bytes += size
            total_buffered_bundles += 1
            copies_by_bundle[copy.bundle_id].add(node)
            heapq.heappush(
                copy_expirations,
                (specs[copy.bundle_id].expires_us, node, copy.bundle_id),
            )

        def remove_copy(node: str, bundle_id: str) -> None:
            nonlocal total_buffered_bytes, total_buffered_bundles
            copy = buffers[node].pop(bundle_id, None)
            if copy is None:
                return
            size = specs[bundle_id].size_bytes
            message_class = specs[bundle_id].message_class.value
            buffered_bytes_at_node[node] -= size
            buffered_bytes_by_class[message_class] -= size
            total_buffered_bytes -= size
            total_buffered_bundles -= 1
            copies_by_bundle[bundle_id].discard(node)

        def snapshot(now_us: int) -> SharedNetworkSnapshot:
            return SharedNetworkSnapshot(
                now_us=now_us,
                buffered_bytes=total_buffered_bytes,
                buffered_bundles=total_buffered_bundles,
                buffered_bytes_by_class={
                    key: value for key, value in buffered_bytes_by_class.items() if value
                },
                delivered_by_class=dict(delivered_by_class),
                dropped_by_class=dict(dropped_by_class),
            )

        def deliver_local_or_destination(
            bundle: SharedBundleSpec,
            delivered_us: int,
            *,
            hops: int,
            bytes_forwarded: int,
        ) -> None:
            if bundle.bundle_id in deliveries:
                return
            record = SharedDeliveryRecord(
                bundle_id=bundle.bundle_id,
                message_class=bundle.message_class.value,
                source=bundle.source,
                destination=bundle.destination,
                created_us=bundle.created_us,
                delivered_us=delivered_us,
                delay_us=delivered_us - bundle.created_us,
                hops=hops,
                bytes_forwarded=bytes_forwarded,
                correlation_id=bundle.correlation_id,
            )
            deliveries[bundle.bundle_id] = record
            delivered_pending_cleanup.add(bundle.bundle_id)
            delivered_by_class[bundle.message_class.value] += 1
            if self.on_delivery is not None:
                emitted = tuple(self.on_delivery(record, bundle, snapshot(delivered_us)))
                for child in emitted:
                    add_bundle(child, not_before_us=delivered_us)

        def enforce_buffer(node: str, now_us: int) -> None:
            nonlocal peak_buffer_bytes, peak_buffer_bundles
            node_buffer = buffers[node]
            while buffered_bytes_at_node[node] > self.buffer_bytes and node_buffer:
                victim = max(
                    node_buffer.values(),
                    key=lambda copy: self._eviction_key(specs[copy.bundle_id], copy, now_us),
                )
                victim_spec = specs[victim.bundle_id]
                remove_copy(node, victim.bundle_id)
                dropped_by_class[victim_spec.message_class.value] += 1
            current_bytes = buffered_bytes_at_node[node]
            peak_by_node[node] = max(peak_by_node[node], current_bytes)
            peak_buffer_bytes = max(peak_buffer_bytes, total_buffered_bytes)
            peak_buffer_bundles = max(peak_buffer_bundles, total_buffered_bundles)

        def admit_until(now_us: int) -> None:
            # Callbacks may schedule another local transition at the same time.
            while pending and pending[0][0] <= now_us:
                _, bundle_id = heapq.heappop(pending)
                bundle = specs[bundle_id]
                if bundle.bundle_id in deliveries:
                    continue
                if bundle.created_us > bundle.expires_us:
                    continue
                if bundle.source == bundle.destination:
                    deliver_local_or_destination(
                        bundle,
                        bundle.created_us,
                        hops=0,
                        bytes_forwarded=0,
                    )
                    continue
                store_copy(
                    bundle.source,
                    _CopyState(
                        node_id=bundle.source,
                        bundle_id=bundle.bundle_id,
                        received_us=bundle.created_us,
                        hops=0,
                        copies=(
                            self.copy_budget
                            if self.routing_policy
                            in {RoutingPolicy.SPRAY_WAIT, RoutingPolicy.RISK_PRIORITY}
                            else 1
                        ),
                        bytes_forwarded=0,
                    ),
                )
                enforce_buffer(bundle.source, bundle.created_us)

        def expire(now_us: int) -> None:
            for bundle_id in tuple(delivered_pending_cleanup):
                for node in tuple(copies_by_bundle.get(bundle_id, ())):
                    remove_copy(node, bundle_id)
                delivered_pending_cleanup.discard(bundle_id)
            touched_nodes: set[str] = set()
            while copy_expirations and copy_expirations[0][0] < now_us:
                _, node, bundle_id = heapq.heappop(copy_expirations)
                if bundle_id not in buffers[node]:
                    continue
                remove_copy(node, bundle_id)
                touched_nodes.add(node)
            for node in touched_nodes:
                enforce_buffer(node, now_us)

        for index, contact in enumerate(self.contacts):
            admit_until(contact.start_us)
            expire(contact.start_us)
            key = contact_key(index, contact)
            contact_capacity[key] = contact.capacity_bytes
            candidates: list[tuple[tuple[Any, ...], str, str, _CopyState]] = []
            for sender, receiver in (
                (contact.node_a, contact.node_b),
                (contact.node_b, contact.node_a),
            ):
                for copy in buffers[sender].values():
                    if copy.received_us > contact.start_us:
                        continue
                    spec = specs[copy.bundle_id]
                    if copy.bundle_id in deliveries or spec.expires_us < contact.end_us:
                        continue
                    if copy.bundle_id in buffers[receiver]:
                        continue
                    if not self._policy_allows_forward(copy, spec, receiver):
                        continue
                    if (
                        self.routing_policy == RoutingPolicy.EPIDEMIC
                        and receiver != spec.destination
                        and len(copies_by_bundle[copy.bundle_id]) >= self.copy_budget
                    ):
                        continue
                    candidates.append(
                        (self._queue_key(spec, copy, contact.start_us), sender, receiver, copy)
                    )
            used = 0
            used_by_class: Counter[str] = Counter()
            for _, sender, receiver, copy in sorted(candidates):
                if copy.bundle_id not in buffers[sender] or copy.bundle_id in deliveries:
                    continue
                spec = specs[copy.bundle_id]
                if used + spec.size_bytes > contact.capacity_bytes:
                    continue
                loss_draw = int(
                    stable_hash(
                        {
                            "contact": key,
                            "bundle": spec.bundle_id,
                            "sender": sender,
                            "receiver": receiver,
                        },
                        domain="aidmesh/shared-dtn/loss/v1",
                    )[:16],
                    16,
                ) / float(16**16)
                used += spec.size_bytes
                used_by_class[spec.message_class.value] += spec.size_bytes
                forwarded_by_class[spec.message_class.value] += spec.size_bytes
                effective_loss = self.class_loss_probability.get(
                    spec.message_class, self.loss_probability
                )
                if loss_draw < effective_loss:
                    dropped_by_class[spec.message_class.value] += 1
                    continue
                if receiver == spec.destination:
                    deliver_local_or_destination(
                        spec,
                        contact.end_us,
                        hops=copy.hops + 1,
                        bytes_forwarded=copy.bytes_forwarded + spec.size_bytes,
                    )
                else:
                    receiver_copies = self._receiver_copies(copy)
                    store_copy(
                        receiver,
                        _CopyState(
                            node_id=receiver,
                            bundle_id=spec.bundle_id,
                            received_us=contact.end_us,
                            hops=copy.hops + 1,
                            copies=receiver_copies,
                            bytes_forwarded=copy.bytes_forwarded + spec.size_bytes,
                        ),
                    )
                    if self.routing_policy == RoutingPolicy.FIFO:
                        remove_copy(sender, copy.bundle_id)
                    elif self.routing_policy in {
                        RoutingPolicy.SPRAY_WAIT,
                        RoutingPolicy.RISK_PRIORITY,
                    }:
                        copy.copies = max(1, copy.copies - receiver_copies)
            contact_used[key] = used
            contact_used_by_class[key] = used_by_class
            enforce_buffer(contact.node_a, contact.end_us)
            enforce_buffer(contact.node_b, contact.end_us)

        horizon_us = max(
            [contact.end_us for contact in self.contacts]
            + [bundle.expires_us for bundle in self.initial_bundles]
            + [0]
        )
        admit_until(horizon_us)
        expire(horizon_us + 1)
        expired = sum(
            bundle_id not in deliveries and spec.expires_us <= horizon_us
            for bundle_id, spec in specs.items()
        )
        records = tuple(sorted(deliveries.values(), key=lambda item: item.bundle_id))
        payload = {
            "deliveries": records,
            "dropped_by_class": dict(dropped_by_class),
            "forwarded_by_class": dict(forwarded_by_class),
            "contact_used": contact_used,
        }
        return SharedDtnResult(
            bundles_created=len(specs),
            delivered=len(records),
            expired=expired,
            dropped=sum(dropped_by_class.values()),
            forwarded_bytes=sum(forwarded_by_class.values()),
            peak_buffer_bytes=peak_buffer_bytes,
            peak_buffer_bundles=peak_buffer_bundles,
            created_by_class=dict(created_by_class),
            delivered_by_class=dict(delivered_by_class),
            dropped_by_class=dict(dropped_by_class),
            forwarded_bytes_by_class=dict(forwarded_by_class),
            peak_buffer_bytes_by_node=dict(peak_by_node),
            contact_used_bytes=contact_used,
            contact_used_bytes_by_class={
                key: dict(value) for key, value in contact_used_by_class.items()
            },
            contact_capacity_bytes=contact_capacity,
            event_checksum=stable_hash(payload, domain="aidmesh/shared-dtn/result/v1"),
            deliveries=records,
        )

    def _queue_key(
        self, spec: SharedBundleSpec, copy: _CopyState, now_us: int
    ) -> tuple[Any, ...]:
        if self.routing_policy in {RoutingPolicy.FIFO, RoutingPolicy.EPIDEMIC}:
            return (copy.received_us, spec.bundle_id)
        if self.routing_policy == RoutingPolicy.RISK_PRIORITY:
            return (
                -spec.risk_score,
                self.class_priority[spec.message_class],
                spec.expires_us - now_us,
                copy.received_us,
                spec.bundle_id,
            )
        return (
            self.class_priority[spec.message_class],
            spec.expires_us - now_us,
            -spec.risk_score,
            copy.received_us,
            spec.bundle_id,
        )

    def _eviction_key(
        self, spec: SharedBundleSpec, copy: _CopyState, now_us: int
    ) -> tuple[Any, ...]:
        if self.routing_policy in {RoutingPolicy.FIFO, RoutingPolicy.EPIDEMIC}:
            return (-copy.received_us, spec.bundle_id)
        if self.routing_policy == RoutingPolicy.RISK_PRIORITY:
            return (
                -spec.risk_score,
                self.class_priority[spec.message_class],
                spec.expires_us - now_us,
                -copy.received_us,
                spec.bundle_id,
            )
        # Larger key is evicted: low-priority, low-risk, long-deadline traffic first.
        return (
            self.class_priority[spec.message_class],
            -spec.risk_score,
            spec.expires_us - now_us,
            -copy.received_us,
            spec.bundle_id,
        )

    def _policy_allows_forward(
        self,
        copy: _CopyState,
        spec: SharedBundleSpec,
        receiver: str,
    ) -> bool:
        if receiver == spec.destination:
            return True
        if self.routing_policy in {RoutingPolicy.FIFO, RoutingPolicy.EPIDEMIC}:
            return True
        return copy.copies > 1

    def _receiver_copies(self, copy: _CopyState) -> int:
        if self.routing_policy == RoutingPolicy.FIFO:
            return copy.copies
        if self.routing_policy == RoutingPolicy.EPIDEMIC:
            return 1
        return max(1, copy.copies // 2)


def aggregate_contact_residual_units(
    result: SharedDtnResult,
    *,
    transfer_size_bytes: int,
    replace_class: MessageClass | None = None,
) -> dict[str, int]:
    """Convert residual contact bytes into integral right-transfer capacities.

    When ``replace_class`` is supplied, bytes used by that class are restored
    before capacity is discretized.  This supports a fair clairvoyant oracle:
    request/quorum/receipt traffic remains fixed, while the online transfer
    schedule is replaced by the oracle's time-expanded transfer flow.
    """

    if transfer_size_bytes <= 0:
        raise ValueError("transfer_size_bytes must be positive")
    residual: dict[str, int] = {}
    for key, used in result.contact_used_bytes.items():
        restored = 0
        if replace_class is not None:
            restored = result.contact_used_bytes_by_class.get(key, {}).get(
                replace_class.value, 0
            )
        residual[key] = (
            max(0, result.contact_capacity_bytes[key] - used + restored)
            // transfer_size_bytes
        )
    return residual
