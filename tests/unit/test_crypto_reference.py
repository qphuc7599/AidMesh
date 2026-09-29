from __future__ import annotations

from dataclasses import replace

from aidmesh.crypto.gate import CryptoGate, CryptoProfile, GateReason
from aidmesh.crypto.merkle import EntitlementLeaf, EntitlementTree
from aidmesh.crypto.primitives import BeneficiarySecrets, Ed25519Keypair
from aidmesh.crypto.reference import ReferenceDisclosureProfile, ReferenceVerificationContext


def _presentation():
    issuers = [Ed25519Keypair.from_seed(bytes([i]) * 32) for i in (1, 2, 3)]
    beneficiary = BeneficiarySecrets.from_seed(b"b" * 32)
    profile = ReferenceDisclosureProfile([issuer.public_key for issuer in issuers], 2)
    campaign = b"c" * 32
    credential = profile.issue(
        beneficiary=beneficiary,
        campaign_id=campaign,
        epoch=1,
        cell="z1",
        group="g1",
        quotas=(("food", 1),),
        signing_issuers=issuers[:2],
    )
    leaf = EntitlementLeaf(1, "z1", "g1", "food", 1, 2)
    tree = EntitlementTree([leaf])
    context = ReferenceVerificationContext(campaign, tree.root, b"v" * 32, b"n" * 32)
    presentation = profile.present(
        beneficiary=beneficiary,
        credential=credential,
        entitlement=leaf,
        membership=tree.proof_for(leaf),
        context=context,
    )
    return profile, context, presentation


def test_reference_relation_verifies_but_never_claims_privacy() -> None:
    profile, context, presentation = _presentation()
    decision = CryptoGate(profile).verify(
        CryptoProfile.REFERENCE_DISCLOSURE_V1, presentation, context
    )
    assert decision.accepted
    assert not decision.privacy_claim_supported
    assert not decision.zero_knowledge


def test_reference_mutated_value_is_rejected() -> None:
    profile, context, presentation = _presentation()
    mutated = replace(presentation, entitlement=replace(presentation.entitlement, value=3))
    decision = CryptoGate(profile).verify(CryptoProfile.REFERENCE_DISCLOSURE_V1, mutated, context)
    assert not decision.accepted
    assert decision.reason is GateReason.REFERENCE_REJECTED


def test_primary_profile_fails_closed() -> None:
    profile, context, presentation = _presentation()
    decision = CryptoGate(profile).verify(CryptoProfile.PRIMARY_PRIVATE_V1, presentation, context)
    assert not decision.accepted
    assert decision.reason is GateReason.PRIMARY_PROFILE_UNAVAILABLE
