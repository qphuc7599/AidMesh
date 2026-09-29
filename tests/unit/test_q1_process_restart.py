"""Actual process termination at durable commit boundaries, not power-loss tests."""

import subprocess
import sys
from pathlib import Path

import pytest

from aidmesh.common.errors import DuplicateError, ValidationError
from aidmesh.protocol.campaign import SafetyDomain
from aidmesh.protocol.durable_risk import SQLiteRiskRightsLedger
from aidmesh.protocol.release import ReleaseRecord, ReleaseStatus, SQLiteReleaseStore
from aidmesh.protocol.risk_escrow import TerminalDecision, TerminalStatement, TerminalWitness
from aidmesh.protocol.witness import SQLiteWitnessStore


@pytest.mark.parametrize("boundary", ["terminal", "rights", "release"])
def test_process_exit_after_durable_commit(tmp_path, boundary):
    db = tmp_path / "actor.sqlite"
    program = """
import os, sys
from aidmesh.protocol.campaign import SafetyDomain
from aidmesh.protocol.durable_risk import SQLiteRiskRightsLedger
from aidmesh.protocol.release import ReleaseRecord, ReleaseStatus, SQLiteReleaseStore
from aidmesh.protocol.risk_escrow import TerminalDecision, TerminalStatement, TerminalWitness
from aidmesh.protocol.witness import SQLiteWitnessStore
boundary, db = sys.argv[1:]
if boundary == "terminal":
    store = SQLiteWitnessStore(db, signer_id="w")
    TerminalWitness("w", lambda *_: os._exit(73), store=store).sign(
        TerminalStatement(1, "D", 1, "t", "a", "b", 1, TerminalDecision.ACCEPT))
elif boundary == "rights":
    ledger = SQLiteRiskRightsLedger(SafetyDomain("c", 1, 1, "committee"), {"a": 1}, db)
    ledger.debit(site_id="a", debit_id="d1", nullifier="n1", amount=1)
else:
    store = SQLiteReleaseStore(db)
    record = ReleaseRecord("r", "D", 1, "n", "s", "auth", "skel", "res", ReleaseStatus.AUTHORIZED)
    store.authorize(record)
    store.persist_intent(record.slot_key)
os._exit(73)
"""
    completed = subprocess.run(
        [sys.executable, "-c", program, boundary, str(db)],
        cwd=Path(__file__).resolve().parents[2],
        timeout=30,
    )
    assert completed.returncode == 73
    if boundary == "terminal":
        store = SQLiteWitnessStore(db, signer_id="w")
        with pytest.raises(DuplicateError):
            TerminalWitness("w", lambda _, digest: digest, store=store).sign(
                TerminalStatement(1, "D", 1, "t", "a", "b", 1, TerminalDecision.RECLAIM)
            )
        store.close()
    elif boundary == "rights":
        ledger = SQLiteRiskRightsLedger(SafetyDomain("c", 1, 1, "committee"), {"a": 1}, db)
        with pytest.raises(ValidationError):
            ledger.debit(site_id="a", debit_id="d2", nullifier="n2", amount=1)
        assert ledger.balance("a") == 0
        ledger.close()
    else:
        store = SQLiteReleaseStore(db)
        record = ReleaseRecord(
            "r", "D", 1, "n", "s", "auth", "skel", "res", ReleaseStatus.AUTHORIZED
        )
        with pytest.raises(DuplicateError):
            store.persist_intent(record.slot_key)
        store.close_store()
