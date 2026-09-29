import pytest

from aidmesh.common.canonical import stable_hash
from aidmesh.common.errors import DuplicateError, ValidationError
from aidmesh.protocol.risk_escrow import (
    EscrowRiskLedger,
    TerminalDecision,
    TerminalWitness,
    TransferStatus,
    assemble_terminal_certificate,
    terminal_liveness_condition,
    terminal_safety_condition,
)


def _signature(signer_id: str, digest: str) -> str:
    return stable_hash(
        {"signer_id": signer_id, "digest": digest},
        domain="tests/terminal-signature/v1",
    )


def _verify_signature(signer_id: str, digest: str, signature: str) -> bool:
    return signature == _signature(signer_id, digest)


def test_delayed_transfer_is_conserved_and_replay_idempotent() -> None:
    ledger = EscrowRiskLedger({"a": 5, "b": 1})
    first = ledger.begin_transfer("t1", "a", "b", 3)
    assert first.status is TransferStatus.IN_TRANSIT
    assert ledger.snapshot().in_transit == 3
    assert ledger.begin_transfer("t1", "a", "b", 3) == first
    accepted = ledger.accept_transfer("t1")
    assert ledger.accept_transfer("t1") == accepted
    ledger.spend("s1", "b", 4)
    assert ledger.snapshot().conserved


def test_reordering_and_expiry_fail_closed() -> None:
    ledger = EscrowRiskLedger({"a": 5, "b": 0})
    with pytest.raises(ValidationError, match="debit not observed"):
        ledger.accept_transfer("missing")
    ledger.begin_transfer("t1", "a", "b", 2)
    ledger.terminate_transfer("t1")
    assert ledger.snapshot().expired == 2
    with pytest.raises(ValidationError, match="terminal"):
        ledger.accept_transfer("t1")
    assert ledger.snapshot().conserved


def test_revocation_is_monotone() -> None:
    ledger = EscrowRiskLedger({"a": 3, "b": 0})
    ledger.begin_transfer("t1", "a", "b", 1)
    terminal = ledger.terminate_transfer("t1", revoked=True)
    assert ledger.terminate_transfer("t1", revoked=True) == terminal
    assert ledger.snapshot().revoked == 1
    assert ledger.snapshot().conserved


def test_reclaim_returns_locked_right_without_minting() -> None:
    ledger = EscrowRiskLedger({"a": 3, "b": 0})
    ledger.begin_transfer("t1", "a", "b", 2)

    reclaimed = ledger.reclaim_transfer("t1")
    assert reclaimed.status is TransferStatus.RECLAIMED
    assert ledger.reclaim_transfer("t1") == reclaimed
    snapshot = ledger.snapshot()
    assert snapshot.balances == (("a", 3), ("b", 0))
    assert snapshot.reclaimed == 2
    assert snapshot.conserved


def test_accept_and_reclaim_are_mutually_exclusive() -> None:
    accepted = EscrowRiskLedger({"a": 2, "b": 0})
    accepted.begin_transfer("accept-first", "a", "b", 1)
    accepted.accept_transfer("accept-first")
    with pytest.raises(ValidationError):
        accepted.reclaim_transfer("accept-first")

    reclaimed = EscrowRiskLedger({"a": 2, "b": 0})
    reclaimed.begin_transfer("reclaim-first", "a", "b", 1)
    reclaimed.reclaim_transfer("reclaim-first")
    with pytest.raises(ValidationError):
        reclaimed.accept_transfer("reclaim-first")


def test_certified_terminal_decision_requires_a_valid_quorum() -> None:
    members = ("w0", "w1", "w2", "w3")
    witnesses = {member: TerminalWitness(member, _signature) for member in members}
    ledger = EscrowRiskLedger(
        {"a": 3, "b": 0},
        domain_id="relief-1",
        epoch=7,
        terminal_members=members,
        terminal_threshold=3,
    )
    ledger.begin_transfer("t-certified", "a", "b", 2)
    statement = ledger.terminal_statement("t-certified", TerminalDecision.ACCEPT)

    with pytest.raises(ValidationError, match="ACCEPT certificate"):
        ledger.accept_transfer("t-certified")
    with pytest.raises(ValidationError, match="insufficient"):
        assemble_terminal_certificate(
            statement,
            [witnesses["w0"].sign(statement), witnesses["w1"].sign(statement)],
            members=members,
            threshold=3,
            verify_signature=_verify_signature,
        )

    certificate = assemble_terminal_certificate(
        statement,
        [
            witnesses["w0"].sign(statement),
            witnesses["w1"].sign(statement),
            witnesses["w2"].sign(statement),
        ],
        members=members,
        threshold=3,
        verify_signature=_verify_signature,
    )
    accepted = ledger.accept_certified(certificate, _verify_signature)
    assert accepted.status is TransferStatus.ACCEPTED
    assert ledger.snapshot().balances == (("a", 1), ("b", 2))
    assert ledger.snapshot().conserved


def test_honest_terminal_witness_never_signs_accept_and_reclaim() -> None:
    members = ("w0", "w1", "w2", "w3")
    witnesses = {member: TerminalWitness(member, _signature) for member in members}
    ledger = EscrowRiskLedger(
        {"a": 2, "b": 0},
        terminal_members=members,
        terminal_threshold=3,
    )
    ledger.begin_transfer("t-conflict", "a", "b", 1)
    accept = ledger.terminal_statement("t-conflict", TerminalDecision.ACCEPT)
    reclaim = ledger.terminal_statement("t-conflict", TerminalDecision.RECLAIM)

    for witness in witnesses.values():
        witness.sign(accept)
        with pytest.raises(DuplicateError, match="conflicting"):
            witness.sign(reclaim)


def test_certified_reclaim_returns_escrow_without_minting() -> None:
    members = ("w0", "w1", "w2", "w3")
    witnesses = {member: TerminalWitness(member, _signature) for member in members}
    ledger = EscrowRiskLedger(
        {"a": 4, "b": 0},
        terminal_members=members,
        terminal_threshold=3,
    )
    ledger.begin_transfer("t-reclaim", "a", "b", 3)
    statement = ledger.terminal_statement("t-reclaim", TerminalDecision.RECLAIM)
    certificate = assemble_terminal_certificate(
        statement,
        [witnesses[member].sign(statement) for member in members[:3]],
        members=members,
        threshold=3,
        verify_signature=_verify_signature,
    )

    reclaimed = ledger.reclaim_certified(certificate, _verify_signature)
    assert reclaimed.status is TransferStatus.RECLAIMED
    assert ledger.snapshot().balances == (("a", 4), ("b", 0))
    assert ledger.snapshot().reclaimed == 3
    assert ledger.snapshot().conserved


def test_terminal_threshold_conditions_match_the_quorum_argument() -> None:
    assert terminal_safety_condition(committee_size=4, threshold=3, faults=1)
    assert terminal_liveness_condition(committee_size=4, threshold=3, faults=1)
    assert not terminal_safety_condition(committee_size=4, threshold=2, faults=1)
    assert not terminal_liveness_condition(committee_size=4, threshold=4, faults=1)
