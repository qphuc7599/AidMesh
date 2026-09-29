import pandas as pd

from aidmesh.experiments.temporal_factorial import (
    FACTOR_LEVELS,
    _all_cells,
    factorial_main_effects,
)


def test_complete_factorial_has_64_unique_cells() -> None:
    cells = _all_cells()
    assert len(cells) == 64
    assert len({tuple(cell[name] for name in FACTOR_LEVELS) for cell in cells}) == 64


def test_factorial_main_effect_recovers_binary_contrast() -> None:
    rows = []
    for seed in (1, 2):
        for cell in _all_cells():
            rows.append(
                {
                    "context": "trace",
                    "seed": seed,
                    **cell,
                    "transfer_gain": 3.0
                    if cell["demand_drift"] == "reversed"
                    else 1.0,
                }
            )
    effects = factorial_main_effects(pd.DataFrame(rows))
    demand = effects[
        (effects["context"] == "trace") & (effects["factor"] == "demand_drift")
    ].iloc[0]
    assert demand["effect_on_transfer_gain"] == 2.0
    assert demand["paired_cells"] == 64
