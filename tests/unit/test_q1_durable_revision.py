from concurrent.futures import ThreadPoolExecutor

import pytest

from aidmesh.common.errors import DuplicateError, ValidationError
from aidmesh.protocol.campaign import SafetyDomain
from aidmesh.protocol.durable_risk import SQLiteRiskRightsLedger
from aidmesh.protocol.release import ReleaseRecord, ReleaseStatus, SQLiteReleaseStore
from aidmesh.protocol.risk_escrow import TerminalDecision, TerminalStatement, TerminalWitness
from aidmesh.protocol.witness import SQLiteWitnessStore


def test_terminal_restart_preserves_decision_before_sign(tmp_path):
    path = tmp_path / "w.sqlite"
    accept = TerminalStatement(1, "D", 1, "t", "a", "b", 1, TerminalDecision.ACCEPT)
    reclaim = TerminalStatement(1, "D", 1, "t", "a", "b", 1, TerminalDecision.RECLAIM)
    store = SQLiteWitnessStore(path, signer_id="w")

    # Signing fails after durable decision: restart must still not equivocate.
    def fail(*_):
        raise RuntimeError("signer failed")

    with pytest.raises(RuntimeError):
        TerminalWitness("w", fail, store=store).sign(accept)
    store.close()
    store = SQLiteWitnessStore(path, signer_id="w")
    w = TerminalWitness("w", lambda _, digest: digest, store=store)
    assert w.sign(accept).statement_digest == accept.digest
    with pytest.raises(DuplicateError):
        w.sign(reclaim)
    store.close()


def test_rights_restart_and_new_nullifier_cannot_remint(tmp_path):
    path = tmp_path / "rights.sqlite"
    domain = SafetyDomain("c", 1, 1, "committee")
    ledger = SQLiteRiskRightsLedger(domain, {"a": 1}, path)
    debit = ledger.debit(site_id="a", debit_id="d1", nullifier="n1", amount=1)
    ledger.close()
    ledger = SQLiteRiskRightsLedger(domain, {"a": 1}, path)
    assert ledger.debit(site_id="a", debit_id="d1", nullifier="n1", amount=1) == debit
    with pytest.raises(ValidationError):
        ledger.debit(site_id="a", debit_id="d2", nullifier="n2", amount=1)
    assert ledger.snapshot().is_conserved
    assert len(ledger.journal) == 1
    ledger.close()
    with pytest.raises(ValidationError, match="genesis"):
        SQLiteRiskRightsLedger(domain, {"a": 2}, path)


def test_independent_connections_serialize_actuator_claim(tmp_path):
    path = tmp_path / "release.sqlite"
    stores = [SQLiteReleaseStore(path), SQLiteReleaseStore(path)]
    record = ReleaseRecord("r", "D", 1, "n", "s", "auth", "skel", "res", ReleaseStatus.AUTHORIZED)
    for store in stores:
        store.authorize(record)

    def claim(store):
        try:
            store.persist_intent(record.slot_key)
            return True
        except DuplicateError:
            return False

    with ThreadPoolExecutor(2) as pool:
        assert sum(pool.map(claim, stores)) == 1
    for store in stores:
        store.close_store()


def test_concurrent_rights_debits_do_not_reset_each_others_state(tmp_path):
    path = tmp_path / "rights.sqlite"
    domain = SafetyDomain("c", 1, 1, "committee")
    ledgers = [SQLiteRiskRightsLedger(domain, {"a": 1}, path) for _ in range(2)]

    def spend(i):
        try:
            ledgers[i].debit(site_id="a", debit_id=f"d{i}", nullifier=f"n{i}", amount=1)
            return True
        except ValidationError:
            return False

    with ThreadPoolExecutor(2) as pool:
        assert sum(pool.map(spend, range(2))) == 1
    for ledger in ledgers:
        assert ledger.balance("a") == 0
        ledger.close()
