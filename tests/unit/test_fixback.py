from __future__ import annotations

from pathlib import Path

import pytest

from aidmesh.common.canonical import stable_hash
from aidmesh.common.errors import DuplicateError
from aidmesh.experiments.fixback import (
    _header,
    _voucher,
    run_abstract_delay_calibration,
    run_dtn_toy_semantics,
    run_durable_witness_campaign,
)
from aidmesh.protocol.model import witness_request_for
from aidmesh.protocol.witness import SQLiteWitnessStore, Witness, WitnessKey


def _signature(signer_id: str, digest: str) -> str:
    return stable_hash({"signer": signer_id, "digest": digest}, domain="test/fixback/signature")


def test_sqlite_witness_persists_before_sign_and_reopens(tmp_path: Path) -> None:
    header = _header()
    db_path = tmp_path / "witness.db"
    request_a = witness_request_for(_voucher(header, "n1"), "s1")
    request_b = witness_request_for(_voucher(header, "n1"), "s2")
    store = SQLiteWitnessStore(db_path, signer_id="w0")
    witness = Witness("w0", header, store, _signature)

    first = witness.sign(request_a)
    repeat = witness.sign(request_a)
    with pytest.raises(DuplicateError):
        witness.sign(request_b)
    counts = store.audit_counts()
    store.close()

    reopened = SQLiteWitnessStore(db_path, signer_id="w0")
    assert reopened.read(Witness.state_key(request_a)) == request_a.digest
    reopened.close()
    assert first.request_digest == repeat.request_digest
    assert counts["COMMIT_OK"] >= 1
    assert counts["SIGN_OK"] >= 2


def test_sqlite_witness_crash_before_commit_fails_closed(tmp_path: Path) -> None:
    header = _header()
    request = witness_request_for(_voucher(header, "n1"), "s1")

    def crash(_key: WitnessKey, _digest: str) -> None:
        raise RuntimeError("boom")

    store = SQLiteWitnessStore(tmp_path / "witness.db", signer_id="w0", before_commit=crash)
    witness = Witness("w0", header, store, _signature)
    with pytest.raises(RuntimeError):
        witness.sign(request)
    assert store.read(Witness.state_key(request)) is None
    assert store.audit_counts()["FAIL_CLOSED"] == 1
    assert "SIGN_OK" not in store.audit_counts()
    store.close()


def test_fixback_campaign_helpers_write_artifacts(tmp_path: Path) -> None:
    calibration = run_abstract_delay_calibration(
        tmp_path / "calibration",
        seeds=1,
        bundles_per_seed=8,
    )
    toy = run_dtn_toy_semantics(tmp_path / "dtn")
    durable = run_durable_witness_campaign(tmp_path / "witness", trials=3)
    assert all(path.exists() for path in [*calibration.values(), *toy.values(), *durable.values()])
