import json
from pathlib import Path

import pytest

from aidmesh.formal.integrated_refinement import check_event_log


def _write_log(path: Path, events: list[dict[str, object]]) -> Path:
    path.write_text(
        "".join(json.dumps(event, sort_keys=True) + "\n" for event in events),
        encoding="utf-8",
    )
    return path


def _event(
    sequence: int,
    event_id: str,
    component: str,
    action: str,
    outcome: str,
    *,
    nullifier: str | None = None,
    path_label: str | None = None,
) -> dict[str, object]:
    row: dict[str, object] = {
        "sequence": sequence,
        "event_id": event_id,
        "component": component,
        "action": action,
        "outcome": outcome,
    }
    if nullifier is not None:
        row["circuit_nullifier"] = nullifier
    if path_label is not None:
        row["path"] = path_label
    return row


def test_mixed_quorum_and_fallback_releases_refine_one_credit(tmp_path: Path) -> None:
    events = [
        _event(0, "quorum-release", "groth16-local-evm", "verifyProof", "verified", nullifier="7"),
        _event(1, "quorum-release", "quorum", "assemble-certificate", "authorized"),
        _event(
            2,
            "quorum-release",
            "inventory",
            "release",
            "committed",
            nullifier="7",
            path_label="quorum",
        ),
        _event(3, "quorum-release", "local-evm", "verifyAndSettle", "settled", nullifier="7"),
        _event(
            4, "fallback-release", "groth16-local-evm", "verifyProof", "verified", nullifier="7"
        ),
        _event(5, "fallback-release", "risk-right", "spend", "authorized"),
        _event(
            6,
            "fallback-release",
            "inventory",
            "release",
            "committed",
            nullifier="7",
            path_label="fallback",
        ),
    ]
    summary, _, nullifiers = check_event_log(_write_log(tmp_path / "events.jsonl", events))
    assert summary["quorum_releases"] == 1
    assert summary["fallback_debits"] == 1
    assert summary["fallback_releases"] == 1
    assert summary["duplicate_physical_releases"] == 1
    assert summary["settled"] == 1
    assert nullifiers[0]["simulation_relation_holds"] is True


def test_stage_order_violation_is_rejected(tmp_path: Path) -> None:
    events = [
        _event(0, "bad-order", "groth16-local-evm", "verifyProof", "verified", nullifier="9"),
        _event(1, "bad-order", "quorum", "assemble-certificate", "authorized"),
        _event(2, "bad-order", "local-evm", "verifyAndSettle", "settled", nullifier="9"),
        _event(
            3,
            "bad-order",
            "inventory",
            "release",
            "committed",
            nullifier="9",
            path_label="quorum",
        ),
    ]
    with pytest.raises(AssertionError, match="settlement does not refine local release"):
        check_event_log(_write_log(tmp_path / "events.jsonl", events))


def test_fallback_release_without_distinct_debit_is_rejected(tmp_path: Path) -> None:
    events = [
        _event(0, "fallback-a", "groth16-local-evm", "verifyProof", "verified", nullifier="11"),
        _event(1, "fallback-a", "risk-right", "spend", "authorized"),
        _event(
            2,
            "fallback-a",
            "inventory",
            "release",
            "committed",
            nullifier="11",
            path_label="fallback",
        ),
        _event(
            3,
            "fallback-a",
            "inventory",
            "release",
            "committed",
            nullifier="11",
            path_label="fallback",
        ),
    ]
    with pytest.raises(AssertionError, match="prefix fallback release exceeds debit"):
        check_event_log(_write_log(tmp_path / "events.jsonl", events))


def test_repeated_fallback_debit_id_is_rejected(tmp_path: Path) -> None:
    events = [
        _event(0, "fallback-a", "groth16-local-evm", "verifyProof", "verified", nullifier="13"),
        _event(1, "fallback-a", "risk-right", "spend", "authorized"),
        _event(2, "fallback-a", "risk-right", "spend", "authorized"),
        _event(
            3,
            "fallback-a",
            "inventory",
            "release",
            "committed",
            nullifier="13",
            path_label="fallback",
        ),
    ]
    with pytest.raises(AssertionError, match="fallback debit id is counted twice"):
        check_event_log(_write_log(tmp_path / "events.jsonl", events))


def test_authorization_without_local_proof_is_rejected(tmp_path: Path) -> None:
    events = [
        _event(0, "missing-proof", "risk-right", "spend", "authorized"),
        _event(
            1,
            "missing-proof",
            "inventory",
            "release",
            "committed",
            nullifier="17",
            path_label="fallback",
        ),
    ]
    with pytest.raises(AssertionError, match="authorization precedes proof"):
        check_event_log(_write_log(tmp_path / "events.jsonl", events))


def test_integrated_safe_configs_cover_mixed_path_and_conflict() -> None:
    root = Path(__file__).resolve().parents[2]
    mixed = (root / "formal" / "AidMeshIntegratedSafe.cfg").read_text(encoding="utf-8")
    conflict = (root / "formal" / "AidMeshIntegratedSafeConflict.cfg").read_text(
        encoding="utf-8"
    )
    assert "RightTokens = {r0, r1}" in mixed
    assert "Digests = {d0, d1}" in conflict
    assert "NoConflictingCertificates" in conflict
