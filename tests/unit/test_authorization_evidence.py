from __future__ import annotations

import json
from pathlib import Path

from aidmesh.analysis.authorization_evidence import verify_authorization_events


def _write_events(root: Path, events: list[dict[str, object]]) -> None:
    path = root / "runs" / "context" / "events.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )


def test_authorization_event_scan_accepts_bound_winner_and_conflict(tmp_path: Path) -> None:
    base = {
        "component": "local-evm",
        "action": "verifyAndSettle",
        "authorization_evidence_hash": "0x" + "11" * 32,
        "authorization_commitment": "0x" + "22" * 32,
        "run_id": "run-1",
        "circuit_nullifier": "7",
    }
    _write_events(
        tmp_path,
        [base | {"outcome": "settled"}, base | {"outcome": "conflict"}],
    )

    result = verify_authorization_events(tmp_path)

    assert result["status"] == "valid"
    assert result["transaction_events"] == 2
    assert result["distinct_winner_keys"] == 1


def test_authorization_event_scan_rejects_zero_and_duplicate_winner(tmp_path: Path) -> None:
    base = {
        "component": "local-evm",
        "action": "verifyAndSettle",
        "authorization_evidence_hash": "0x" + "00" * 32,
        "authorization_commitment": "0x" + "22" * 32,
        "run_id": "run-1",
        "circuit_nullifier": "7",
        "outcome": "settled",
    }
    _write_events(tmp_path, [base, base])

    result = verify_authorization_events(tmp_path)

    assert result["status"] == "invalid"
    assert {failure["error"] for failure in result["failures"]} == {
        "zero binding",
        "multiple settled winners in one run",
    }
