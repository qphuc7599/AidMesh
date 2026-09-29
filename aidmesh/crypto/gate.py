"""Fail-closed selection between the unavailable primary and reference profiles."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .reference import (
    ReferenceDisclosureProfile,
    ReferencePresentation,
    ReferenceVerificationContext,
)


class CryptoProfile(StrEnum):
    """Protocol profile identifiers; these names are part of the safety boundary."""

    PRIMARY_PRIVATE_V1 = "aidmesh-primary-private-v1"
    REFERENCE_DISCLOSURE_V1 = "aidmesh-reference-disclosure-v1"


class GateReason(StrEnum):
    ACCEPTED_REFERENCE_NON_PRIVATE = "ACCEPTED_REFERENCE_NON_PRIVATE"
    PRIMARY_PROFILE_UNAVAILABLE = "PRIMARY_PROFILE_UNAVAILABLE"
    REFERENCE_PROFILE_UNCONFIGURED = "REFERENCE_PROFILE_UNCONFIGURED"
    REFERENCE_REJECTED = "REFERENCE_REJECTED"
    PROFILE_TYPE_MISMATCH = "PROFILE_TYPE_MISMATCH"
    UNKNOWN_PROFILE = "UNKNOWN_PROFILE"


@dataclass(frozen=True)
class GateDecision:
    accepted: bool
    reason: GateReason
    detail: str
    privacy_claim_supported: bool = False
    zero_knowledge: bool = False


class PrivacyProfileUnavailable(RuntimeError):
    """Raised when a caller requires a privacy-capable primary verifier."""


class CryptoGate:
    """Never substitutes the disclosure reference for the primary profile.

    Until a pinned anonymous-credential + proof relation is implemented, every
    request for ``PRIMARY_PRIVATE_V1`` is rejected independent of the supplied
    bytes.  This is a deliberate research-integrity property, not a temporary
    success stub.
    """

    def __init__(self, reference_profile: ReferenceDisclosureProfile | None = None):
        self.reference_profile = reference_profile

    @property
    def privacy_ready(self) -> bool:
        return False

    def require_privacy_ready(self) -> None:
        raise PrivacyProfileUnavailable(
            "aidmesh-primary-private-v1 has no implemented anonymous-credential/ZK verifier"
        )

    def verify(
        self,
        profile: CryptoProfile | str,
        presentation: object,
        context: object,
    ) -> GateDecision:
        try:
            selected = profile if isinstance(profile, CryptoProfile) else CryptoProfile(profile)
        except (TypeError, ValueError):
            return GateDecision(False, GateReason.UNKNOWN_PROFILE, "unrecognized crypto profile")

        if selected is CryptoProfile.PRIMARY_PRIVATE_V1:
            return GateDecision(
                False,
                GateReason.PRIMARY_PROFILE_UNAVAILABLE,
                "primary anonymous-credential/ZK relation is not implemented; fail closed",
            )

        if self.reference_profile is None:
            return GateDecision(
                False,
                GateReason.REFERENCE_PROFILE_UNCONFIGURED,
                "no trusted issuer roster was configured",
            )
        if not isinstance(presentation, ReferencePresentation) or not isinstance(
            context, ReferenceVerificationContext
        ):
            return GateDecision(
                False,
                GateReason.PROFILE_TYPE_MISMATCH,
                "reference profile received an incompatible presentation/context type",
            )
        result = self.reference_profile.verify(presentation, context)
        if not result.accepted:
            return GateDecision(
                False,
                GateReason.REFERENCE_REJECTED,
                result.reason.value,
            )
        return GateDecision(
            True,
            GateReason.ACCEPTED_REFERENCE_NON_PRIVATE,
            "relation verified; this result does not support a privacy or zero-knowledge claim",
        )
