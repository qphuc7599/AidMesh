from __future__ import annotations

import pandas as pd
import pytest

from aidmesh.experiments.bow_factorial import (
    FACTOR_LEVELS,
    all_factorial_cells,
    factor_settings,
    factorial_policy_contrasts,
)


def test_registered_factorial_is_complete_two_to_the_seventh() -> None:
    cells = all_factorial_cells()
    assert len(FACTOR_LEVELS) == 7
    assert len(cells) == 128
    assert len({tuple(cell.items()) for cell in cells}) == 128


def test_degraded_cell_changes_only_registered_mechanism_parameters() -> None:
    low = {name: levels[0] for name, levels in FACTOR_LEVELS.items()}
    high = {name: levels[1] for name, levels in FACTOR_LEVELS.items()}
    low_settings = factor_settings(low)
    high_settings = factor_settings(high)
    assert low_settings["transfer_loss_probability"] > high_settings[
        "transfer_loss_probability"
    ]
    assert low_settings["buffer_bytes"] < high_settings["buffer_bytes"]
    assert low_settings["background_bundles_per_hour"] > high_settings[
        "background_bundles_per_hour"
    ]


def test_factorial_contrast_preserves_cell_pairing() -> None:
    levels = {name: values[0] for name, values in FACTOR_LEVELS.items()}
    rows = []
    for policy, completion in (("optimized_static", 0.4), ("adatransfer", 0.6)):
        rows.append(
            {
                "context": "trace",
                "seed": 1,
                "cell": 0,
                **levels,
                "policy": policy,
                "completion_rate": completion,
                "end_to_end_settlement_probability": 0.5,
                "exposure_per_1000_requests": 0.0,
                "forwarded_bytes": 100,
                "transfers_stranded": 0,
            }
        )
    contrasts = factorial_policy_contrasts(pd.DataFrame(rows))
    assert len(contrasts) == 1
    assert contrasts.iloc[0]["completion_delta"] == pytest.approx(0.2)
