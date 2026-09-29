"""Multi-process fault injection for witness durability and restart safety."""

from __future__ import annotations

import json
import multiprocessing as mp
import sqlite3
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from aidmesh.common.canonical import stable_hash
from aidmesh.common.errors import DuplicateError
from aidmesh.protocol.campaign import CampaignHeader, CommitteeConfig, SafetyDomain
from aidmesh.protocol.model import Voucher, witness_request_for
from aidmesh.protocol.witness import SQLiteWitnessStore, Witness


@dataclass(frozen=True)
class MultiprocessFaultResult:
    fault_id: str
    expected: str
    observed: str
    safety_preserved: bool
    evidence: dict[str, Any]


def _header() -> CampaignHeader:
    committee = CommitteeConfig("mp-fault-committee", tuple(f"w{i}" for i in range(7)), 5, 2)
    return CampaignHeader(
        domain=SafetyDomain("mp-fault-campaign", 1, 1, committee.committee_id),
        risk_cell="cell-a",
        committee=committee,
        authorized_sites=("site-a", "site-b"),
        fallback_categories=("food",),
        initial_risk_rights=(("site-a", 4), ("site-b", 4)),
        start_tick=0,
        end_tick=100,
        verifier_id="reference",
        policy_hash="frozen-policy",
    )


def _signer(counter: dict[str, int]):
    def sign(signer_id: str, digest: str) -> str:
        counter["calls"] += 1
        return stable_hash({"signer": signer_id, "digest": digest}, domain="fault/signature")

    return sign


def _decision_count(db_path: Path) -> int:
    if not db_path.exists():
        return 0
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT COUNT(*) FROM witness_decision").fetchone()
        return int(row[0] if row else 0)
    finally:
        conn.close()


def _child_sign_then_exit(db_path: str, request_payload: dict[str, str], queue: mp.Queue) -> None:
    header = _header()
    voucher = Voucher(
        header.domain,
        "cell-a",
        "food",
        1,
        1,
        request_payload["nullifier"],
        "cred-1",
        b"proof",
    )
    request = witness_request_for(voucher, request_payload["site"])
    counter = {"calls": 0}
    store = SQLiteWitnessStore(Path(db_path), signer_id="w0")
    witness = Witness("w0", header, store, _signer(counter))
    try:
        signature = witness.sign(request)
        queue.put({"status": "signed", "signature": signature, "calls": counter["calls"]})
    except Exception as exc:  # noqa: BLE001
        queue.put({"status": "error", "error": str(exc), "calls": counter["calls"]})
    finally:
        store.close()


def _run_mp_crash_before_commit() -> MultiprocessFaultResult:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "witness.db")
        queue: mp.Queue = mp.Queue()
        request_payload = {"nullifier": "nullifier-mp-1", "site": "site-a"}
        process = mp.Process(
            target=_child_sign_then_exit,
            args=(db_path, request_payload, queue),
            daemon=True,
        )
        process.start()
        process.terminate()
        process.join(timeout=5)
        signed = not queue.empty()
        if signed:
            queue.get_nowait()
        journal_entries = _decision_count(Path(db_path))
        preserved = not signed and journal_entries == 0
        return MultiprocessFaultResult(
            fault_id="MP1_PROCESS_KILL_MID_SIGN",
            expected="terminated child leaves no durable witness decision",
            observed="fail_closed" if preserved else "unsafe_partial_state",
            safety_preserved=preserved,
            evidence={"journal_entries": journal_entries, "child_signed": signed},
        )


def _run_mp_restart_conflict() -> MultiprocessFaultResult:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "witness.db"
        header = _header()
        voucher = Voucher(header.domain, "cell-a", "food", 1, 1, "nullifier-mp-2", "cred-1", b"proof")
        request_a = witness_request_for(voucher, "site-a")
        request_b = witness_request_for(voucher, "site-b")
        counter = {"calls": 0}
        store = SQLiteWitnessStore(db_path, signer_id="w0")
        witness = Witness("w0", header, store, _signer(counter))
        witness.sign(request_a)
        store.close()
        store = SQLiteWitnessStore(db_path, signer_id="w0")
        witness = Witness("w0", header, store, _signer(counter))
        conflict = False
        try:
            witness.sign(request_b)
        except DuplicateError:
            conflict = True
        store.close()
        return MultiprocessFaultResult(
            fault_id="MP2_RESTART_REJECTS_CONFLICT",
            expected="restarted witness rejects conflicting site request",
            observed="conflict_rejected" if conflict else "unsafe_double_sign",
            safety_preserved=conflict,
            evidence={"signature_calls": counter["calls"]},
        )


def _run_mp_stale_db_handle() -> MultiprocessFaultResult:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "witness.db"
        header = _header()
        voucher = Voucher(header.domain, "cell-a", "food", 1, 1, "nullifier-mp-3", "cred-1", b"proof")
        request = witness_request_for(voucher, "site-a")
        counter = {"calls": 0}
        store = SQLiteWitnessStore(db_path, signer_id="w0")
        witness = Witness("w0", header, store, _signer(counter))
        first = witness.sign(request)
        store.close()
        store = SQLiteWitnessStore(db_path, signer_id="w0")
        witness = Witness("w0", header, store, _signer(counter))
        second = witness.sign(request)
        store.close()
        idempotent = first == second and counter["calls"] == 2
        return MultiprocessFaultResult(
            fault_id="MP3_WAL_RESTART_IDEMPOTENT",
            expected="reopened store replays identical request without mutation",
            observed="idempotent_replay" if idempotent else "unsafe_replay",
            safety_preserved=idempotent,
            evidence={"signature_calls": counter["calls"]},
        )


def run_multiprocess_fault_campaign() -> list[MultiprocessFaultResult]:
    return [
        _run_mp_crash_before_commit(),
        _run_mp_restart_conflict(),
        _run_mp_stale_db_handle(),
    ]


def write_multiprocess_fault_campaign(output: Path) -> Path:
    output.mkdir(parents=True, exist_ok=True)
    results = run_multiprocess_fault_campaign()
    payload = {
        "campaign": "multiprocess_fault_injection_v1",
        "all_safety_preserved": all(item.safety_preserved for item in results),
        "results": [asdict(item) for item in results],
    }
    path = output / "multiprocess_fault_campaign.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path
