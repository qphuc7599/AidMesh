from __future__ import annotations

from aidmesh.experiments.catr_confirmatory import (
    CATR_FACTOR_LEVELS,
    all_catr_factorial_cells,
    catr_factor_settings,
)


def test_catr_design_is_complete_two_to_the_eight() -> None:
    cells = all_catr_factorial_cells()
    assert len(CATR_FACTOR_LEVELS) == 8
    assert len(cells) == 256
    assert len({tuple(cell.items()) for cell in cells}) == 256


def test_recovery_factor_changes_only_reclaim_timing() -> None:
    prompt = all_catr_factorial_cells()[0]
    delayed = {**prompt, "recovery_timing": "delayed"}
    prompt_settings = catr_factor_settings(prompt)
    delayed_settings = catr_factor_settings(delayed)
    differing = {key for key in prompt_settings if prompt_settings[key] != delayed_settings[key]}
    assert differing == {"reclaim_delay_us", "transfer_decision_ttl_us"}
    assert prompt_settings["reclaim_delay_us"] < delayed_settings["reclaim_delay_us"]
