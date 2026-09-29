"""Executable disclosure reference profile (explicitly **not zero knowledge**).

The profile exists to exercise the complete entitlement relation with real
cryptography while a production anonymous-credential/SNARK instantiation is
absent.  It uses threshold-policy Ed25519 issuer attestations, an Ed25519
challenge-bound proof of possession, a Merkle membership proof, and an
HMAC-SHA-256 nullifier relation.  Verifying that last relation requires
disclosing the PRF key; consequently this profile provides no unlinkability or
beneficiary secrecy and MUST NOT support a paper privacy claim.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from .encoding import domain_hash, fixed_bytes, text, u8, u64, vector
from .merkle import EntitlementLeaf, MerkleProof, verify_entitlement_proof
from .primitives import (
    BeneficiarySecrets,
    Ed25519Keypair,
    derive_fallback_tag,
    derive_nullifier,
    key_commitment,
    verify_ed25519,
)

REFERENCE_PROFILE_ID = "aidmesh-reference-disclosure-v1"
ISSUER_SIGNATURE_DOMAIN = "reference-disclosure-credential-issuance-v1"
POSSESSION_SIGNATURE_DOMAIN = "reference-disclosure-possession-v1"


def issuer_key_id(public_key: bytes) -> bytes:
    return domain_hash("reference-issuer-key-id-v1", fixed_bytes(public_key, 32, "issuer key"))


@dataclass(frozen=True, order=True)
class Quota:
    category: str
    count: int

    def __post_init__(self) -> None:
        text(self.category, "category")
        if self.count <= 0:
            raise ValueError("quota count must be positive")
        u64(self.count)

    def encode(self) -> bytes:
        return text(self.category, "category") + u64(self.count)


def canonical_quotas(quotas: Iterable[Quota | tuple[str, int]]) -> tuple[Quota, ...]:
    converted = tuple(q if isinstance(q, Quota) else Quota(*q) for q in quotas)
    if not converted:
        raise ValueError("at least one category quota is required")
    ordered = tuple(sorted(converted, key=lambda q: q.category))
    if len({q.category for q in ordered}) != len(ordered):
        raise ValueError("quota categories must be unique")
    return ordered


@dataclass(frozen=True)
class ReferenceCredentialBody:
    campaign_id: bytes
    epoch: int
    cell: str
    group: str
    quotas: tuple[Quota, ...]
    beneficiary_public_key: bytes
    prf_key_commitment: bytes

    def __post_init__(self) -> None:
        fixed_bytes(self.campaign_id, 32, "campaign_id")
        if self.epoch <= 0:
            raise ValueError("epoch must be positive")
        u64(self.epoch)
        text(self.cell, "cell")
        text(self.group, "group")
        fixed_bytes(self.beneficiary_public_key, 32, "beneficiary_public_key")
        fixed_bytes(self.prf_key_commitment, 32, "prf_key_commitment")
        if self.quotas != canonical_quotas(self.quotas):
            raise ValueError("quotas must use canonical sorted order")

    def quota_for(self, category: str) -> int | None:
        for quota in self.quotas:
            if quota.category == category:
                return quota.count
        return None

    def encode(self) -> bytes:
        return (
            b"AIDMESH/REFERENCE-CREDENTIAL-BODY/V1\x00"
            + self.campaign_id
            + u64(self.epoch)
            + text(self.cell, "cell")
            + text(self.group, "group")
            + vector((quota.encode() for quota in self.quotas), "quotas")
            + self.beneficiary_public_key
            + self.prf_key_commitment
        )


@dataclass(frozen=True, order=True)
class IssuerAttestation:
    key_id: bytes
    signature: bytes

    def __post_init__(self) -> None:
        fixed_bytes(self.key_id, 32, "issuer key id")
        fixed_bytes(self.signature, 64, "issuer signature")

    def encode(self) -> bytes:
        return self.key_id + self.signature


@dataclass(frozen=True)
class ReferenceCredential:
    body: ReferenceCredentialBody
    attestations: tuple[IssuerAttestation, ...]

    def __post_init__(self) -> None:
        if not self.attestations:
            raise ValueError("credential requires issuer attestations")
        if self.attestations != tuple(sorted(self.attestations, key=lambda item: item.key_id)):
            raise ValueError("issuer attestations must use canonical key-id order")
        ids = [item.key_id for item in self.attestations]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate issuer attestations are forbidden")

    def encode(self) -> bytes:
        return (
            b"AIDMESH/REFERENCE-CREDENTIAL/V1\x00"
            + self.body.encode()
            + vector((item.encode() for item in self.attestations), "attestations")
        )


@dataclass(frozen=True)
class ReferenceVerificationContext:
    campaign_id: bytes
    entitlement_root: bytes
    verifier_id: bytes
    nonce: bytes
    fallback_window: int | None = None

    def __post_init__(self) -> None:
        fixed_bytes(self.campaign_id, 32, "campaign_id")
        fixed_bytes(self.entitlement_root, 32, "entitlement_root")
        fixed_bytes(self.verifier_id, 32, "verifier_id")
        fixed_bytes(self.nonce, 32, "nonce")
        if self.fallback_window is not None:
            if self.fallback_window <= 0:
                raise ValueError("fallback_window must be positive")
            u64(self.fallback_window)


@dataclass(frozen=True)
class ReferencePresentation:
    credential: ReferenceCredential
    entitlement: EntitlementLeaf
    membership: MerkleProof
    entitlement_root: bytes
    nullifier: bytes
    disclosed_prf_key: bytes
    verifier_id: bytes
    nonce: bytes
    fallback_window: int | None
    fallback_tag: bytes | None
    possession_signature: bytes

    def __post_init__(self) -> None:
        fixed_bytes(self.entitlement_root, 32, "entitlement_root")
        fixed_bytes(self.nullifier, 32, "nullifier")
        fixed_bytes(self.disclosed_prf_key, 32, "disclosed_prf_key")
        fixed_bytes(self.verifier_id, 32, "verifier_id")
        fixed_bytes(self.nonce, 32, "nonce")
        fixed_bytes(self.possession_signature, 64, "possession_signature")
        if (self.fallback_window is None) != (self.fallback_tag is None):
            raise ValueError(
                "fallback window and tag must either both be present or both be absent"
            )
        if self.fallback_window is not None:
            if self.fallback_window <= 0:
                raise ValueError("fallback_window must be positive")
            fixed_bytes(self.fallback_tag or b"", 32, "fallback_tag")

    def unsigned_encode(self) -> bytes:
        fallback = u8(0)
        if self.fallback_window is not None:
            fallback = (
                u8(1)
                + u64(self.fallback_window)
                + fixed_bytes(self.fallback_tag or b"", 32, "fallback_tag")
            )
        return (
            b"AIDMESH/REFERENCE-PRESENTATION/V1\x00"
            + text(REFERENCE_PROFILE_ID, "profile")
            + self.credential.encode()
            + self.entitlement.encode()
            + self.membership.encode()
            + self.entitlement_root
            + self.nullifier
            + self.disclosed_prf_key
            + self.verifier_id
            + self.nonce
            + fallback
        )


class ReferenceReason(StrEnum):
    ACCEPTED = "ACCEPTED"
    CONTEXT_MISMATCH = "CONTEXT_MISMATCH"
    ISSUER_THRESHOLD = "ISSUER_THRESHOLD"
    INVALID_ISSUER_SIGNATURE = "INVALID_ISSUER_SIGNATURE"
    INVALID_KEY_COMMITMENT = "INVALID_KEY_COMMITMENT"
    INVALID_POSSESSION_SIGNATURE = "INVALID_POSSESSION_SIGNATURE"
    CREDENTIAL_RELATION = "CREDENTIAL_RELATION"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
    INVALID_MEMBERSHIP = "INVALID_MEMBERSHIP"
    INVALID_NULLIFIER = "INVALID_NULLIFIER"
    INVALID_FALLBACK_TAG = "INVALID_FALLBACK_TAG"
    MALFORMED = "MALFORMED"


@dataclass(frozen=True)
class ReferenceDecision:
    accepted: bool
    reason: ReferenceReason
    privacy_claim_supported: bool = False
    zero_knowledge: bool = False


class ReferenceDisclosureProfile:
    """Verifier/issuer for the executable but intentionally non-private profile."""

    profile_id = REFERENCE_PROFILE_ID
    privacy_claim_supported = False
    zero_knowledge = False

    def __init__(self, trusted_issuer_public_keys: Iterable[bytes], issuer_threshold: int):
        keys = tuple(trusted_issuer_public_keys)
        if not keys:
            raise ValueError("at least one trusted issuer is required")
        by_id: dict[bytes, bytes] = {}
        for key in keys:
            fixed_bytes(key, 32, "issuer public key")
            key_id = issuer_key_id(key)
            if key_id in by_id:
                raise ValueError("duplicate issuer public key")
            by_id[key_id] = key
        if issuer_threshold <= 0 or issuer_threshold > len(by_id):
            raise ValueError("issuer_threshold must be in 1..number of trusted issuers")
        self._trusted_issuers: Mapping[bytes, bytes] = MappingProxyType(by_id)
        self.issuer_threshold = issuer_threshold

    @property
    def trusted_issuers(self) -> Mapping[bytes, bytes]:
        return self._trusted_issuers

    def issue(
        self,
        *,
        beneficiary: BeneficiarySecrets,
        campaign_id: bytes,
        epoch: int,
        cell: str,
        group: str,
        quotas: Iterable[Quota | tuple[str, int]],
        signing_issuers: Iterable[Ed25519Keypair],
    ) -> ReferenceCredential:
        body = ReferenceCredentialBody(
            campaign_id=fixed_bytes(campaign_id, 32, "campaign_id"),
            epoch=epoch,
            cell=cell,
            group=group,
            quotas=canonical_quotas(quotas),
            beneficiary_public_key=beneficiary.possession_public_key,
            prf_key_commitment=beneficiary.prf_key_commitment,
        )
        attestations: list[IssuerAttestation] = []
        seen: set[bytes] = set()
        for signer in signing_issuers:
            key_id = issuer_key_id(signer.public_key)
            if self._trusted_issuers.get(key_id) != signer.public_key:
                raise ValueError("untrusted signing issuer")
            if key_id in seen:
                raise ValueError("duplicate signing issuer")
            seen.add(key_id)
            attestations.append(
                IssuerAttestation(
                    key_id,
                    signer.sign(ISSUER_SIGNATURE_DOMAIN, body.encode()),
                )
            )
        if len(attestations) < self.issuer_threshold:
            raise ValueError("not enough issuer attestations")
        return ReferenceCredential(body, tuple(sorted(attestations, key=lambda item: item.key_id)))

    def present(
        self,
        *,
        beneficiary: BeneficiarySecrets,
        credential: ReferenceCredential,
        entitlement: EntitlementLeaf,
        membership: MerkleProof,
        context: ReferenceVerificationContext,
    ) -> ReferencePresentation:
        body = credential.body
        if body.beneficiary_public_key != beneficiary.possession_public_key:
            raise ValueError("beneficiary does not possess the credential-bound key")
        if body.prf_key_commitment != beneficiary.prf_key_commitment:
            raise ValueError("beneficiary PRF key is not credential-bound")
        if body.campaign_id != context.campaign_id:
            raise ValueError("credential belongs to another campaign")
        if (
            entitlement.epoch != body.epoch
            or entitlement.cell != body.cell
            or entitlement.group != body.group
        ):
            raise ValueError("entitlement does not match credential attributes")
        quota = body.quota_for(entitlement.category)
        if quota is None or entitlement.index > quota:
            raise ValueError("entitlement index is outside the signed quota")
        if not verify_entitlement_proof(entitlement, membership, context.entitlement_root):
            raise ValueError("entitlement membership proof does not verify")

        nullifier = derive_nullifier(
            beneficiary.prf_key,
            epoch=entitlement.epoch,
            cell=entitlement.cell,
            category=entitlement.category,
            index=entitlement.index,
            value=entitlement.value,
        )
        fallback_tag = None
        if context.fallback_window is not None:
            fallback_tag = derive_fallback_tag(
                beneficiary.prf_key,
                epoch=entitlement.epoch,
                window=context.fallback_window,
                cell=entitlement.cell,
                category=entitlement.category,
            )
        placeholder = ReferencePresentation(
            credential=credential,
            entitlement=entitlement,
            membership=membership,
            entitlement_root=context.entitlement_root,
            nullifier=nullifier,
            disclosed_prf_key=beneficiary.prf_key,
            verifier_id=context.verifier_id,
            nonce=context.nonce,
            fallback_window=context.fallback_window,
            fallback_tag=fallback_tag,
            possession_signature=b"\x00" * 64,
        )
        signature = beneficiary.possession_keypair.sign(
            POSSESSION_SIGNATURE_DOMAIN, placeholder.unsigned_encode()
        )
        return ReferencePresentation(
            credential=credential,
            entitlement=entitlement,
            membership=membership,
            entitlement_root=context.entitlement_root,
            nullifier=nullifier,
            disclosed_prf_key=beneficiary.prf_key,
            verifier_id=context.verifier_id,
            nonce=context.nonce,
            fallback_window=context.fallback_window,
            fallback_tag=fallback_tag,
            possession_signature=signature,
        )

    def _verify_credential(self, credential: ReferenceCredential) -> ReferenceReason | None:
        valid = 0
        for attestation in credential.attestations:
            public_key = self._trusted_issuers.get(attestation.key_id)
            if public_key is None:
                continue
            if not verify_ed25519(
                public_key,
                attestation.signature,
                ISSUER_SIGNATURE_DOMAIN,
                credential.body.encode(),
            ):
                return ReferenceReason.INVALID_ISSUER_SIGNATURE
            valid += 1
        if valid < self.issuer_threshold:
            return ReferenceReason.ISSUER_THRESHOLD
        return None

    def verify(
        self,
        presentation: ReferencePresentation,
        context: ReferenceVerificationContext,
    ) -> ReferenceDecision:
        try:
            body = presentation.credential.body
            if (
                body.campaign_id != context.campaign_id
                or presentation.entitlement_root != context.entitlement_root
                or presentation.verifier_id != context.verifier_id
                or presentation.nonce != context.nonce
                or presentation.fallback_window != context.fallback_window
            ):
                return ReferenceDecision(False, ReferenceReason.CONTEXT_MISMATCH)

            credential_error = self._verify_credential(presentation.credential)
            if credential_error is not None:
                return ReferenceDecision(False, credential_error)
            if key_commitment(presentation.disclosed_prf_key) != body.prf_key_commitment:
                return ReferenceDecision(False, ReferenceReason.INVALID_KEY_COMMITMENT)

            entitlement = presentation.entitlement
            if (
                entitlement.epoch != body.epoch
                or entitlement.cell != body.cell
                or entitlement.group != body.group
            ):
                return ReferenceDecision(False, ReferenceReason.CREDENTIAL_RELATION)
            quota = body.quota_for(entitlement.category)
            if quota is None or entitlement.index > quota:
                return ReferenceDecision(False, ReferenceReason.QUOTA_EXCEEDED)
            if not verify_entitlement_proof(
                entitlement, presentation.membership, context.entitlement_root
            ):
                return ReferenceDecision(False, ReferenceReason.INVALID_MEMBERSHIP)

            expected_nullifier = derive_nullifier(
                presentation.disclosed_prf_key,
                epoch=entitlement.epoch,
                cell=entitlement.cell,
                category=entitlement.category,
                index=entitlement.index,
                value=entitlement.value,
            )
            if expected_nullifier != presentation.nullifier:
                return ReferenceDecision(False, ReferenceReason.INVALID_NULLIFIER)

            if context.fallback_window is None:
                if presentation.fallback_tag is not None:
                    return ReferenceDecision(False, ReferenceReason.INVALID_FALLBACK_TAG)
            else:
                expected_tag = derive_fallback_tag(
                    presentation.disclosed_prf_key,
                    epoch=entitlement.epoch,
                    window=context.fallback_window,
                    cell=entitlement.cell,
                    category=entitlement.category,
                )
                if expected_tag != presentation.fallback_tag:
                    return ReferenceDecision(False, ReferenceReason.INVALID_FALLBACK_TAG)

            if not verify_ed25519(
                body.beneficiary_public_key,
                presentation.possession_signature,
                POSSESSION_SIGNATURE_DOMAIN,
                presentation.unsigned_encode(),
            ):
                return ReferenceDecision(False, ReferenceReason.INVALID_POSSESSION_SIGNATURE)
            return ReferenceDecision(True, ReferenceReason.ACCEPTED)
        except (TypeError, ValueError, OverflowError):
            return ReferenceDecision(False, ReferenceReason.MALFORMED)
