"""Strict distinct-signer quorum certificate construction and verification."""

from __future__ import annotations

from collections.abc import Iterable

from aidmesh.common.crypto import SignatureVerifier
from aidmesh.common.errors import ConfigurationError, ValidationError

from .campaign import CommitteeConfig
from .model import QuorumCertificate, WitnessRequest, WitnessShare


def verify_certificate(
    request: WitnessRequest,
    certificate: QuorumCertificate,
    committee: CommitteeConfig,
    verify_signature: SignatureVerifier,
) -> bool:
    """Verify a certificate, rejecting duplicate identities rather than counting them."""

    if request.domain.committee_id != committee.committee_id:
        return False
    if certificate.domain != request.domain:
        return False
    if certificate.nullifier != request.nullifier:
        return False
    if certificate.request_digest != request.digest:
        return False
    if certificate.release_intent_id != request.release_intent_id:
        return False
    if certificate.receipt_skeleton_hash != request.receipt_skeleton_hash:
        return False
    if len(certificate.shares) < committee.threshold:
        return False

    signer_ids = certificate.signer_ids
    if len(signer_ids) != len(set(signer_ids)):
        return False
    members = set(committee.members)
    for share in certificate.shares:
        if share.signer_id not in members:
            return False
        if share.domain != request.domain:
            return False
        if share.nullifier != request.nullifier:
            return False
        if share.request_digest != request.digest:
            return False
        if not verify_signature(share.signer_id, request.digest, share.signature):
            return False
    return True


def assemble_certificate(
    request: WitnessRequest,
    shares: Iterable[WitnessShare],
    committee: CommitteeConfig,
    verify_signature: SignatureVerifier,
) -> QuorumCertificate:
    materialized = tuple(shares)
    signer_ids = tuple(share.signer_id for share in materialized)
    if len(signer_ids) != len(set(signer_ids)):
        raise ValidationError("certificate input contains a repeated witness identity")
    certificate = QuorumCertificate(
        domain=request.domain,
        nullifier=request.nullifier,
        request_digest=request.digest,
        shares=materialized,
        release_intent_id=request.release_intent_id,
        receipt_skeleton_hash=request.receipt_skeleton_hash,
    )
    if not verify_certificate(request, certificate, committee, verify_signature):
        raise ValidationError("invalid or insufficient witness shares")
    return certificate


def require_theorem_configuration(committee: CommitteeConfig) -> None:
    """Fail closed when a run claims the quorum non-conflict theorem."""

    if not committee.has_safe_intersection:
        raise ConfigurationError("quorum safety claim disabled: 2q must be greater than K + f")


def certificates_conflict(left: QuorumCertificate, right: QuorumCertificate) -> bool:
    return (
        left.domain.nullifier_scope == right.domain.nullifier_scope
        and left.nullifier == right.nullifier
        and left.request_digest != right.request_digest
    )
