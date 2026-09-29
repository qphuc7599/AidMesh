from __future__ import annotations

import pandas as pd

from aidmesh.analysis.bow_advanced import (
    benjamini_hochberg,
    empirical_regret,
    leave_one_family_out_validation,
)
from aidmesh.experiments.bow_factorial import FACTOR_LEVELS


def test_bh_values_are_monotone_and_bounded() -> None:
    corrected = benjamini_hochberg(
        pd.DataFrame({"term": ["a", "b", "c"], "p_value": [0.01, 0.04, 0.03]})
    )
    assert corrected["q_value_bh"].between(0, 1).all()
    ordered = corrected.sort_values("p_value")
    assert ordered["q_value_bh"].is_monotonic_increasing


def test_empirical_regret_uses_registered_oracle() -> None:
    frame = pd.DataFrame(
        {
            "policy": ["adatransfer", "optimized_static"],
            "completed": [8, 6],
            "duplicate_exposure": [0, 0],
            "oracle_total_completed": [10, None],
            "generated_requests": [12, 12],
            "context": ["a", "a"],
            "seed": [1, 1],
        }
    )
    runs, summary = empirical_regret(frame)
    assert runs.iloc[0]["absolute_regret"] == 2
    assert runs.iloc[0]["competitive_ratio"] == 0.8
    assert summary.iloc[0]["mean_absolute_regret"] == 2


def test_lofo_reports_each_family_without_training_on_it() -> None:
    rows = []
    cells = []
    for index in range(16):
        cells.append(
            {
                factor: levels[(index >> (position % 4)) & 1]
                for position, (factor, levels) in enumerate(FACTOR_LEVELS.items())
            }
        )
    for family in ("a", "b", "c"):
        for seed in (1, 2):
            for cell_index, cell in enumerate(cells):
                drift = float(cell["demand_drift"] == FACTOR_LEVELS["demand_drift"][1])
                methods = (
                    ("optimized_static", 0.0),
                    ("adatransfer", 0.1 + 0.05 * drift),
                )
                for policy, gain in methods:
                    rows.append(
                        {
                            "family": family,
                            "registered_context": family,
                            "context": family,
                            "seed": seed,
                            "cell": cell_index,
                            **cell,
                            "policy": policy,
                            "completion_rate": 0.4 + gain,
                        }
                    )
    predictions, summary, calibration = leave_one_family_out_validation(pd.DataFrame(rows))
    assert set(summary["family"]) == {"a", "b", "c"}
    assert summary["sign_accuracy"].eq(1.0).all()
    assert len(predictions) == 3 * 2 * 16
    assert not calibration.empty
