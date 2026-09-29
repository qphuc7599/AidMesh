"""Receipt batching with fixed public shape and encrypted audit detail.

The module models a public settlement release boundary.  It deliberately makes
the public view smaller than the private receipt intent: public observers get a
batch commitment and generalized metadata, while an audit authority with the
AES-GCM key can recover the private receipt detail.  This is an experimental
mechanism, not a claim that gateway or site observers become anonymous.
"""

from __future__ import annotations

import base64
import json
import os
from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from aidmesh.common.canonical import stable_hash
from aidmesh.config import canonical_json

ALLOWED_HIDDEN_FIELDS = frozenset({"risk_cell", "category", "path", "fallback_tag"})


@dataclass(frozen=True)
class ReceiptIntent:
    """Private receipt metadata available to the batch authority before release."""

    receipt_id: str
    released_us: int
    deadline_us: int
    payload_bytes: int
    copies: int
    epoch: int
    risk_cell: str
    category: str
    path: str
    fallback_tag: str | None = None

    def __post_init__(self) -> None:
        if not self.receipt_id:
            raise ValueError("receipt_id is required")
        if self.released_us < 0 or self.deadline_us < self.released_us:
            raise ValueError("receipt timing is invalid")
        if self.payload_bytes <= 0 or self.copies <= 0:
            raise ValueError("payload_bytes and copies must be positive")
        if self.epoch < 1:
            raise ValueError("epoch must be positive")
        if not self.risk_cell or not self.category or not self.path:
            raise ValueError("risk_cell, category and path are required")


@dataclass(frozen=True)
class BatchPolicy:
    """Fixed public disclosure and padding rules for one protocol version."""

    slot_us: int
    pad_bytes: int
    dummy_min_batch: int
    hide_fields: frozenset[str]
    shuffle_seed: int
    gateway_bucket_us: int = 3_600_000_000
    cover_empty_slots: bool = False
    cover_boundary_slots: int = 0

    def __post_init__(self) -> None:
        if self.slot_us <= 0 or self.pad_bytes <= 0 or self.gateway_bucket_us <= 0:
            raise ValueError("time slots and pad_bytes must be positive")
        if self.dummy_min_batch < 0:
            raise ValueError("dummy_min_batch cannot be negative")
        if self.cover_boundary_slots < 0:
            raise ValueError("cover_boundary_slots cannot be negative")
        if (self.cover_empty_slots or self.cover_boundary_slots) and self.dummy_min_batch < 1:
            raise ValueError("cover traffic requires dummy_min_batch >= 1")
        unknown = self.hide_fields - ALLOWED_HIDDEN_FIELDS
        if unknown:
            raise ValueError(f"unknown disclosure fields: {sorted(unknown)}")

    @property
    def policy_id(self) -> str:
        return stable_hash(asdict(self), domain="aidmesh/privacy/batch-policy/v1")


@dataclass(frozen=True)
class PublicReceiptView:
    batch_id: str
    public_receipt_id: str
    slot_start_us: int
    released_us: int
    padded_size: int
    epoch: int
    risk_cell: str
    category: str
    path: str
    fallback_tag: str | None
    is_dummy: bool
    leaf_commitment: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BatchManifest:
    batch_id: str
    policy_id: str
    slot_start_us: int
    release_us: int
    actual_receipts: int
    dummy_receipts: int
    public_receipts: int
    padded_size: int
    batch_root: str
    cover_only: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EncryptedAuditRecord:
    batch_id: str
    receipt_id: str
    nonce_b64: str
    ciphertext_b64: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DelayCost:
    receipt_id: str
    batch_id: str
    added_delay_us: int
    deadline_missed_after_batch: bool
    transport_overhead_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BatchResult:
    policy: BatchPolicy
    public_views: tuple[PublicReceiptView, ...]
    manifests: tuple[BatchManifest, ...]
    audit_records: tuple[EncryptedAuditRecord, ...]
    delays: tuple[DelayCost, ...]


def generate_audit_key() -> bytes:
    """Generate an AES-256 audit key; its lifecycle is outside this artifact."""

    return AESGCM.generate_key(bit_length=256)


def _require_audit_key(audit_key: bytes) -> None:
    if len(audit_key) not in {16, 24, 32}:
        raise ValueError("audit_key must be an AES key with 16, 24 or 32 bytes")


def _merkle_root(leaves: list[str]) -> str:
    if not leaves:
        raise ValueError("cannot build a batch root with no leaves")
    level = list(leaves)
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        level = [
            stable_hash({"left": left, "right": right}, domain="aidmesh/privacy/batch-node/v1")
            for left, right in zip(level[::2], level[1::2], strict=True)
        ]
    return level[0]


def _shuffled(
    items: list[tuple[str, ReceiptIntent | None, bool]],
    *,
    batch_id: str,
    seed: int,
) -> list[tuple[str, ReceiptIntent | None, bool]]:
    return sorted(
        items,
        key=lambda item: stable_hash(
            {"seed": seed, "batch_id": batch_id, "item": item[0]},
            domain="aidmesh/privacy/batch-shuffle/v1",
        ),
    )


def _public_value(value: str | None, *, field: str, policy: BatchPolicy) -> str | None:
    if field in policy.hide_fields:
        return None if field == "fallback_tag" else "hidden"
    return value


def _encrypt_audit_record(
    intent: ReceiptIntent,
    *,
    batch_id: str,
    audit_key: bytes,
) -> EncryptedAuditRecord:
    nonce = os.urandom(12)
    plaintext = canonical_json({"receipt": asdict(intent), "batch_id": batch_id})
    ciphertext = AESGCM(audit_key).encrypt(nonce, plaintext, batch_id.encode("utf-8"))
    return EncryptedAuditRecord(
        batch_id=batch_id,
        receipt_id=intent.receipt_id,
        nonce_b64=base64.b64encode(nonce).decode("ascii"),
        ciphertext_b64=base64.b64encode(ciphertext).decode("ascii"),
    )


def decrypt_audit_record(record: EncryptedAuditRecord, audit_key: bytes) -> dict[str, Any]:
    """Decrypt one audit record; only the audit authority should possess the key."""

    _require_audit_key(audit_key)
    plaintext = AESGCM(audit_key).decrypt(
        base64.b64decode(record.nonce_b64),
        base64.b64decode(record.ciphertext_b64),
        record.batch_id.encode("utf-8"),
    )
    return dict(json.loads(plaintext.decode("utf-8")))


def batch_receipts(
    intents: list[ReceiptIntent],
    *,
    policy: BatchPolicy,
    audit_key: bytes,
) -> BatchResult:
    """Flush receipt intents at slot boundaries with fixed public record size.

    Dummies are public commitments only.  They never create encrypted audit
    records, inventory releases, or settlement credits.
    """

    _require_audit_key(audit_key)
    if len({intent.receipt_id for intent in intents}) != len(intents):
        raise ValueError("receipt_id values must be unique within a batch run")
    groups: dict[int, list[ReceiptIntent]] = defaultdict(list)
    for intent in intents:
        if intent.payload_bytes > policy.pad_bytes:
            raise ValueError("payload exceeds fixed public pad_bytes")
        groups[(intent.released_us // policy.slot_us) * policy.slot_us].append(intent)
    if groups and (policy.cover_empty_slots or policy.cover_boundary_slots):
        observed_first = min(groups)
        observed_last = max(groups)
        first = observed_first - policy.cover_boundary_slots * policy.slot_us
        last = observed_last + policy.cover_boundary_slots * policy.slot_us
        if policy.cover_empty_slots:
            for slot_start in range(first, last + policy.slot_us, policy.slot_us):
                groups.setdefault(slot_start, [])
        else:
            for offset in range(1, policy.cover_boundary_slots + 1):
                groups.setdefault(observed_first - offset * policy.slot_us, [])
                groups.setdefault(observed_last + offset * policy.slot_us, [])

    views: list[PublicReceiptView] = []
    manifests: list[BatchManifest] = []
    audit_records: list[EncryptedAuditRecord] = []
    delays: list[DelayCost] = []
    for slot_start, slot_intents in sorted(groups.items()):
        ordered_intents = sorted(slot_intents, key=lambda item: item.receipt_id)
        batch_id = stable_hash(
            {
                "policy_id": policy.policy_id,
                "slot_start_us": slot_start,
                "receipt_ids": [item.receipt_id for item in ordered_intents],
            },
            domain="aidmesh/privacy/batch-id/v1",
        )
        entries: list[tuple[str, ReceiptIntent | None, bool]] = [
            (intent.receipt_id, intent, False) for intent in ordered_intents
        ]
        dummy_count = max(0, policy.dummy_min_batch - len(entries))
        for ordinal in range(dummy_count):
            dummy_id = stable_hash(
                {"batch_id": batch_id, "ordinal": ordinal},
                domain="aidmesh/privacy/dummy-receipt/v1",
            )
            entries.append((dummy_id, None, True))
        shuffled = _shuffled(entries, batch_id=batch_id, seed=policy.shuffle_seed)
        leaves = [
            stable_hash(
                {
                    "batch_id": batch_id,
                    "ordinal": ordinal,
                    "source_id": source_id,
                    "dummy": is_dummy,
                },
                domain="aidmesh/privacy/batch-leaf/v1",
            )
            for ordinal, (source_id, _intent, is_dummy) in enumerate(shuffled)
        ]
        release_us = slot_start + policy.slot_us
        for ordinal, ((source_id, intent, is_dummy), leaf) in enumerate(
            zip(shuffled, leaves, strict=True)
        ):
            if intent is None:
                epoch, risk_cell, category, path, fallback_tag = (
                    1,
                    "hidden",
                    "hidden",
                    "hidden",
                    None,
                )
            else:
                epoch = intent.epoch
                risk_cell = (
                    _public_value(intent.risk_cell, field="risk_cell", policy=policy) or "hidden"
                )
                category = (
                    _public_value(intent.category, field="category", policy=policy) or "hidden"
                )
                path = _public_value(intent.path, field="path", policy=policy) or "hidden"
                fallback_tag = _public_value(
                    intent.fallback_tag,
                    field="fallback_tag",
                    policy=policy,
                )
            views.append(
                PublicReceiptView(
                    batch_id=batch_id,
                    public_receipt_id=stable_hash(
                        {"batch_id": batch_id, "ordinal": ordinal, "source_id": source_id},
                        domain="aidmesh/privacy/public-receipt/v1",
                    ),
                    slot_start_us=slot_start,
                    released_us=release_us,
                    padded_size=policy.pad_bytes,
                    epoch=epoch,
                    risk_cell=risk_cell,
                    category=category,
                    path=path,
                    fallback_tag=fallback_tag,
                    is_dummy=is_dummy,
                    leaf_commitment=leaf,
                )
            )
            if intent is not None:
                audit_records.append(
                    _encrypt_audit_record(intent, batch_id=batch_id, audit_key=audit_key)
                )
                delays.append(
                    DelayCost(
                        receipt_id=intent.receipt_id,
                        batch_id=batch_id,
                        added_delay_us=release_us - intent.released_us,
                        deadline_missed_after_batch=release_us > intent.deadline_us,
                        transport_overhead_bytes=(policy.pad_bytes - intent.payload_bytes)
                        * intent.copies,
                    )
                )
        manifests.append(
            BatchManifest(
                batch_id=batch_id,
                policy_id=policy.policy_id,
                slot_start_us=slot_start,
                release_us=release_us,
                actual_receipts=len(ordered_intents),
                dummy_receipts=dummy_count,
                public_receipts=len(entries),
                padded_size=policy.pad_bytes,
                batch_root=_merkle_root(leaves),
                cover_only=not ordered_intents,
            )
        )
    return BatchResult(
        policy=policy,
        public_views=tuple(views),
        manifests=tuple(manifests),
        audit_records=tuple(audit_records),
        delays=tuple(delays),
    )
