from __future__ import annotations

from pathlib import Path

import pytest

from aidmesh.config import load_scenario
from aidmesh.simulator import METHODS, run_experiment
from aidmesh.simulator import experiment as experiment_module
from aidmesh.simulator.experiment import (
    HOUR_US,
    WANG_SEEN_TAG_BYTES,
    Attempt,
    _settlement,
    _synchronize_seen_tags,
)

ROOT = Path(__file__).resolve().parents[2]


def test_simulator_is_deterministic_and_conserves_risk() -> None:
    config = load_scenario(ROOT / "configs" / "development" / "smoke.yaml")
    left = run_experiment(config, "aidmesh_transferable_rights")
    right = run_experiment(config, "aidmesh_transferable_rights")
    assert left == right
    assert left.risk_rights_conserved
    assert left.conflicting_quorum_certificates == 0
    assert left.duplicate_exposure <= left.risk_cap
    assert left.physical_duplicate_exposure <= left.risk_cap


def test_online_only_never_releases_duplicate_value() -> None:
    config = load_scenario(ROOT / "configs" / "development" / "smoke.yaml")
    result = run_experiment(config, "online_only")
    assert result.conflicts == 0
    assert result.duplicate_exposure == 0
    assert result.physical_duplicate_exposure == 0


def test_all_registered_methods_execute() -> None:
    config = load_scenario(ROOT / "configs" / "development" / "smoke.yaml")
    results = [run_experiment(config, method) for method in METHODS]
    assert {result.method for result in results} == set(METHODS)


def test_paired_methods_share_exogenous_delivery_and_contact_draws() -> None:
    config = load_scenario(ROOT / "configs" / "development" / "smoke.yaml")
    attempt = Attempt(
        request_id="paired-request",
        nullifier="n",
        beneficiary=0,
        site=0,
        cell=0,
        category=0,
        group=0,
        value=1,
        created_us=0,
        deadline_us=config["analysis_horizon_us"],
        adversarial_duplicate=False,
    )
    left = _settlement(config, attempt, "fallback", "first_seen_dtn")
    right = _settlement(config, attempt, "fallback", "aidmesh_transferable_rights")
    assert left == right


def test_seen_tag_sync_replicates_only_missing_tag_payload() -> None:
    site_seen = {(0, "a"), (1, "a"), (1, "b")}
    tags, payload = _synchronize_seen_tags(site_seen, sites=3)
    assert tags == 2
    assert payload == 3 * WANG_SEEN_TAG_BYTES
    assert site_seen == {(site, tag) for site in range(3) for tag in ("a", "b")}


def test_wang_seen_tag_rejects_cross_site_repeat_after_connectivity_returns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_scenario(ROOT / "configs" / "development" / "smoke.yaml")
    config["network"]["partition_us"] = HOUR_US
    config["analysis_horizon_us"] = 10 * HOUR_US
    config["routing"]["ttl_us"] = 8 * HOUR_US
    attempts = (
        Attempt("r-000000-0", "tag-a", 0, 0, 0, 0, 0, 1, HOUR_US // 2, 8 * HOUR_US, False),
        Attempt("r-000001-0", "tag-a", 1, 1, 0, 0, 0, 1, 3 * HOUR_US // 4, 8 * HOUR_US, True),
        Attempt("r-000002-0", "tag-a", 2, 2, 0, 0, 0, 1, 2 * HOUR_US, 8 * HOUR_US, True),
    )
    monkeypatch.setattr(experiment_module, "_world", lambda _config: attempts)
    result = run_experiment(config, "wang_seen_tag_sync_1h")
    assert result.accepted_releases == 2
    assert result.physical_duplicate_exposure == 1
    assert result.seen_tag_sync_rounds == 2
    assert result.seen_tag_payload_bytes == 4 * WANG_SEEN_TAG_BYTES


def test_wang_seen_tag_frequency_is_an_explicit_method_parameter() -> None:
    config = load_scenario(ROOT / "configs" / "development" / "smoke.yaml")
    one_hour = run_experiment(config, "wang_seen_tag_sync_1h")
    six_hours = run_experiment(config, "wang_seen_tag_sync_6h")
    twenty_four_hours = run_experiment(config, "wang_seen_tag_sync_24h")
    assert one_hour.seen_tag_sync_rounds >= six_hours.seen_tag_sync_rounds
    assert six_hours.seen_tag_sync_rounds >= twenty_four_hours.seen_tag_sync_rounds
