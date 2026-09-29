"""Canonical immutable messages exchanged by the protocol state machines."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TYPE_CHECKING

from aidmesh.common.canonical import stable_hash
from aidmesh.common.crypto import MessageSigner, Proof
from aidmesh.common.errors import ValidationError

from .campaign import SafetyDomain

if TYPE_CHECKING:
    from .risk import DebitRecord


def _required(value: str, label: str) -> None:
    if not value:
        raise ValidationError(f"{label} is required")


class RedemptionPath(StrEnum):
    QUORUM = "quorum"
    FALLBACK = "fallback"


@dataclass(frozen=True)
class Voucher:
    domain: SafetyDomain
    risk_cell: str
    category: str
    value: int
    inventory_units: int
    nullifier: str
    commitment: str
    proof: Proof

    def __post_init__(self) -> None:
        for value, label in (
            (self.risk_cell, "risk_cell"),
            (self.category, "category"),
            (self.nullifier, "nullifier"),
            (self.commitment, "commitment"),
        ):
            _required(value, label)
        if self.value <= 0 or self.inventory_units <= 0:
            raise ValidationError("voucher value and inventory units must be positive")

    @property
    def digest(self) -> str:
        return stable_hash(self, domain="aidmesh/voucher/v1")


def release_intent_id_for(
    voucher: Voucher, site_id: str, path: RedemptionPath
) -> str:
    """Return the stable idempotency key for one irreversible site handoff."""

    return stable_hash(
        {
            "protocol_version": 1,
            "domain": voucher.domain,
            "nullifier": voucher.nullifier,
            "site_id": site_id,
            "path": path,
        },
        domain="aidmesh/release-intent-id/v1",
    )


def receipt_skeleton_hash_for(
    voucher: Voucher,
    site_id: str,
    path: RedemptionPath,
    release_intent_id: str,
) -> str:
    """Commit fields known before witnesses authorize a physical release."""

    return stable_hash(
        {
            "protocol_version": 1,
            "voucher_digest": voucher.digest,
            "domain": voucher.domain,
            "nullifier": voucher.nullifier,
            "site_id": site_id,
            "category": voucher.category,
            "value": voucher.value,
            "inventory_units": voucher.inventory_units,
            "path": path,
            "release_intent_id": release_intent_id,
        },
        domain="aidmesh/receipt-skeleton/v1",
    )


@dataclass(frozen=True)
class AuthorizationIntentV1:
    protocol_version: int
    domain: SafetyDomain
    voucher_digest: str
    nullifier: str
    risk_cell: str
    site_id: str
    category: str
    value: int
    inventory_units: int
    release_intent_id: str
    receipt_skeleton_hash: str

    def __post_init__(self) -> None:
        for value, label in (
            (self.voucher_digest, "voucher_digest"),
            (self.nullifier, "nullifier"),
            (self.risk_cell, "risk_cell"),
            (self.site_id, "site_id"),
            (self.category, "category"),
            (self.release_intent_id, "release_intent_id"),
            (self.receipt_skeleton_hash, "receipt_skeleton_hash"),
        ):
            _required(value, label)
        if self.protocol_version != 1:
            raise ValidationError("unsupported authorization intent version")
        if self.value <= 0 or self.inventory_units <= 0:
            raise ValidationError("request value and inventory units must be positive")

    @property
    def digest(self) -> str:
        return stable_hash(self, domain="aidmesh/authorization-intent/v1")


# Compatibility name used by the witness and quorum modules.  The canonical wire
# object is AuthorizationIntentV1, not a post-release receipt.
WitnessRequest = AuthorizationIntentV1


def witness_request_for(voucher: Voucher, site_id: str) -> WitnessRequest:
    """Construct the canonical pre-handoff authorization intent."""

    path = RedemptionPath.QUORUM
    release_intent_id = release_intent_id_for(voucher, site_id, path)
    return AuthorizationIntentV1(
        protocol_version=1,
        domain=voucher.domain,
        voucher_digest=voucher.digest,
        nullifier=voucher.nullifier,
        risk_cell=voucher.risk_cell,
        site_id=site_id,
        category=voucher.category,
        value=voucher.value,
        inventory_units=voucher.inventory_units,
        release_intent_id=release_intent_id,
        receipt_skeleton_hash=receipt_skeleton_hash_for(
            voucher,
            site_id,
            path,
            release_intent_id,
        ),
    )


@dataclass(frozen=True)
class WitnessShare:
    signer_id: str
    domain: SafetyDomain
    nullifier: str
    request_digest: str
    signature: str

    def __post_init__(self) -> None:
        for value, label in (
            (self.signer_id, "signer_id"),
            (self.nullifier, "nullifier"),
            (self.request_digest, "request_digest"),
            (self.signature, "signature"),
        ):
            _required(value, label)


@dataclass(frozen=True)
class QuorumCertificate:
    domain: SafetyDomain
    nullifier: str
    request_digest: str
    shares: tuple[WitnessShare, ...]
    release_intent_id: str = ""
    receipt_skeleton_hash: str = ""

    def __post_init__(self) -> None:
        _required(self.nullifier, "nullifier")
        _required(self.request_digest, "request_digest")
        _required(self.release_intent_id, "release_intent_id")
        _required(self.receipt_skeleton_hash, "receipt_skeleton_hash")
        object.__setattr__(self, "shares", tuple(sorted(self.shares, key=lambda s: s.signer_id)))

    @property
    def signer_ids(self) -> tuple[str, ...]:
        return tuple(share.signer_id for share in self.shares)

    @property
    def digest(self) -> str:
        return stable_hash(self, domain="aidmesh/quorum-certificate/v1")


@dataclass(frozen=True)
class FallbackEvidence:
    domain: SafetyDomain
    nullifier: str
    fallback_window: int
    link_tag_hash: str
    debit_id: str
    debit_amount: int
    balance_before: int
    balance_after: int
    site_nonce: int
    previous_chain_hash: str
    chain_hash: str

    @classmethod
    def from_debit(
        cls, *, debit: DebitRecord, fallback_window: int, link_tag: str
    ) -> FallbackEvidence:
        if fallback_window < 0:
            raise ValidationError("fallback window cannot be negative")
        return cls(
            domain=debit.domain,
            nullifier=debit.nullifier,
            fallback_window=fallback_window,
            link_tag_hash=stable_hash(
                {"window": fallback_window, "tag": link_tag},
                domain="aidmesh/fallback-link-tag/v1",
            ),
            debit_id=debit.debit_id,
            debit_amount=debit.amount,
            balance_before=debit.balance_before,
            balance_after=debit.balance_after,
            site_nonce=debit.site_nonce,
            previous_chain_hash=debit.previous_chain_hash,
            chain_hash=debit.chain_hash,
        )


type PathEvidence = QuorumCertificate | FallbackEvidence


@dataclass(frozen=True)
class Receipt:
    voucher: Voucher
    site_id: str
    path: RedemptionPath
    evidence: PathEvidence
    released_at: int
    release_intent_id: str = ""
    receipt_skeleton_hash: str = ""
    site_signature: str = ""

    def __post_init__(self) -> None:
        _required(self.site_id, "site_id")
        if self.released_at < 0:
            raise ValidationError("released_at cannot be negative")
        if self.evidence.domain != self.voucher.domain:
            raise ValidationError("path evidence is bound to another safety domain")
        if self.evidence.nullifier != self.voucher.nullifier:
            raise ValidationError("path evidence is bound to another nullifier")
        if self.path is RedemptionPath.QUORUM and not isinstance(self.evidence, QuorumCertificate):
            raise ValidationError("quorum receipt needs a quorum certificate")
        if self.path is RedemptionPath.FALLBACK and not isinstance(self.evidence, FallbackEvidence):
            raise ValidationError("fallback receipt needs fallback evidence")
        expected_intent = release_intent_id_for(self.voucher, self.site_id, self.path)
        expected_skeleton = receipt_skeleton_hash_for(
            self.voucher,
            self.site_id,
            self.path,
            expected_intent,
        )
        if not self.release_intent_id:
            object.__setattr__(self, "release_intent_id", expected_intent)
        if not self.receipt_skeleton_hash:
            object.__setattr__(self, "receipt_skeleton_hash", expected_skeleton)
        if self.release_intent_id != expected_intent:
            raise ValidationError("receipt is bound to another release intent")
        if self.receipt_skeleton_hash != expected_skeleton:
            raise ValidationError("receipt skeleton does not match immutable fields")
        if isinstance(self.evidence, QuorumCertificate):
            if self.evidence.release_intent_id != self.release_intent_id:
                raise ValidationError("quorum evidence names another release intent")
            if self.evidence.receipt_skeleton_hash != self.receipt_skeleton_hash:
                raise ValidationError("quorum evidence names another receipt skeleton")

    @property
    def signing_digest(self) -> str:
        fields = {
            "voucher": self.voucher,
            "site_id": self.site_id,
            "path": self.path,
            "evidence": self.evidence,
            "released_at": self.released_at,
            "release_intent_id": self.release_intent_id,
            "receipt_skeleton_hash": self.receipt_skeleton_hash,
        }
        return stable_hash(fields, domain="aidmesh/receipt-signature-message/v1")

    @property
    def receipt_id(self) -> str:
        return stable_hash(self, domain="aidmesh/receipt/v1")

    def signed(self, signer: MessageSigner) -> Receipt:
        if self.site_signature:
            raise ValidationError("receipt is already signed")
        signature = signer(self.site_id, self.signing_digest)
        if not signature:
            raise ValidationError("signature interface returned an empty signature")
        return replace(self, site_signature=signature)
