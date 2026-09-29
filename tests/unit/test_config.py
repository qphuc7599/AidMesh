from copy import deepcopy

import pytest

from aidmesh.config import ScenarioError, load_scenario, validate_scenario


def test_checked_in_safe_scenario_is_valid() -> None:
    scenario = load_scenario("configs/development/smoke.yaml")
    assert 2 * scenario["quorum"]["q"] > (
        scenario["quorum"]["K"] + scenario["quorum"]["registered_f"]
    )


def test_checked_in_boundary_scenario_is_not_mislabeled_safe() -> None:
    scenario = load_scenario("configs/stress/unsafe_quorum.yaml")
    assert scenario["quorum"]["safe_label"] is False


def test_rejects_original_workflow_k7_q4_f1_safe_error() -> None:
    scenario = load_scenario("configs/development/smoke.yaml")
    bad = deepcopy(scenario)
    bad["quorum"].update({"K": 7, "q": 4, "registered_f": 1, "safe_label": True})
    with pytest.raises(ScenarioError, match="safe_label mismatch"):
        validate_scenario(bad)


def test_disabled_fallback_cannot_hide_nonzero_rights() -> None:
    scenario = load_scenario("configs/development/smoke.yaml")
    bad = deepcopy(scenario)
    bad["fallback"].update({"enabled": False, "transfer_enabled": False})
    with pytest.raises(ScenarioError, match="total_rights=0"):
        validate_scenario(bad)
