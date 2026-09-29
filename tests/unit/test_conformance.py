from __future__ import annotations

from aidmesh.formal.conformance import default_risk_transcript, replay_risk_conformance


def test_formal_risk_oracle_matches_hash_chained_ledger_on_valid_and_invalid_steps() -> None:
    report = replay_risk_conformance({"site-a": 3, "site-b": 2}, default_risk_transcript())

    assert report["formal_oracle_matches_python_ledger"]
    assert report["risk_rights_conserved"]
    assert report["operations"][2]["accepted"] is False
