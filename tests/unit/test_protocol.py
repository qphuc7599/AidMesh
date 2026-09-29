from __future__ import annotations

import pytest

from aidmesh.common.canonical import stable_hash
from aidmesh.common.errors import DuplicateError, ValidationError
from aidmesh.protocol.campaign import CampaignHeader, CommitteeConfig, SafetyDomain
from aidmesh.protocol.contract import ContractModel, ContractVerifierInterfaces, SettlementStatus
from aidmesh.protocol.inventory import SiteInventory
from aidmesh.protocol.model import Voucher, witness_request_for
from aidmesh.protocol.quorum import assemble_certificate
from aidmesh.protocol.risk import RiskRightsLedger
from aidmesh.protocol.site import Site
from aidmesh.protocol.witness import InMemoryWitnessStore, Witness


def _header() -> CampaignHeader:
    committee = CommitteeConfig("committee", tuple(f"w{i}" for i in range(7)), 5, 2)
    domain = SafetyDomain("campaign", 1, 1, committee.committee_id)
    return CampaignHeader(
        domain=domain,
        risk_cell="z1",
        committee=committee,
        authorized_sites=("s1", "s2"),
        fallback_categories=("food",),
        initial_risk_rights=(("s1", 4), ("s2", 4)),
        start_tick=0,
        end_tick=100,
        verifier_id="reference",
        policy_hash="policy",
    )


def _signature(signer_id: str, digest: str) -> str:
    return stable_hash({"signer": signer_id, "digest": digest}, domain="test/signature")


def _verify(signer_id: str, digest: str, signature: str) -> bool:
    return signature == _signature(signer_id, digest)


def _voucher(header: CampaignHeader) -> Voucher:
    return Voucher(header.domain, "z1", "food", 2, 1, "n1", "c1", b"proof")


def test_persist_before_sign_blocks_conflicting_quorum() -> None:
    header = _header()
    first = witness_request_for(_voucher(header), "s1")
    second = witness_request_for(_voucher(header), "s2")
    witnesses = [
        Witness(member, header, InMemoryWitnessStore(), _signature)
        for member in header.committee.members
    ]
    shares = [witness.sign(first) for witness in witnesses[:5]]
    certificate = assemble_certificate(first, shares, header.committee, _verify)
    assert len(certificate.signer_ids) == 5
    with pytest.raises(DuplicateError):
        witnesses[0].sign(second)


def test_risk_right_transfer_and_debit_are_conserved() -> None:
    header = _header()
    ledger = RiskRightsLedger(header.domain, header.risk_rights)
    ledger.transfer(
        transfer_id="t1",
        source_site="s1",
        destination_site="s2",
        amount=2,
        expected_source=ledger.state_token("s1"),
        expected_destination=ledger.state_token("s2"),
    )
    ledger.debit(
        site_id="s2",
        debit_id="d1",
        nullifier="n1",
        amount=5,
        expected_state=ledger.state_token("s2"),
    )
    snapshot = ledger.snapshot()
    assert snapshot.is_conserved
    assert snapshot.remaining_total == 3
    assert snapshot.debited_total == 5


def test_contract_credits_one_quorum_receipt_only() -> None:
    header = _header()
    voucher = _voucher(header)
    request = witness_request_for(voucher, "s1")
    witnesses = [
        Witness(member, header, InMemoryWitnessStore(), _signature)
        for member in header.committee.members
    ]
    certificate = assemble_certificate(
        request,
        [witness.sign(request) for witness in witnesses[:5]],
        header.committee,
        _verify,
    )
    site = Site("s1", header, SiteInventory({"food": 10}), lambda _v, _h: True, _signature)
    receipt = site.redeem_quorum(voucher, certificate, tick=1, verify_witness_signature=_verify)
    model = ContractModel(
        ContractVerifierInterfaces(
            verify_voucher=lambda _v, _h: True,
            verify_signature=_verify,
            verify_fallback_evidence=lambda _r, _h: True,
        )
    )
    model.register_header(header, escrow=10)
    first = model.submit(receipt)
    replay = model.submit(receipt)
    assert first.status is SettlementStatus.SETTLED
    assert replay.status is SettlementStatus.CONFLICT
    assert sum(model.credits.values()) == voucher.value
    model.assert_at_most_once()


def test_same_epoch_safety_change_is_rejected() -> None:
    header = _header()
    with pytest.raises(ValidationError):
        header.transition(epoch=1, header_version=2, policy_hash="changed")
