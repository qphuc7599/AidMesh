from __future__ import annotations

import pandas as pd

from aidmesh.analysis.hierarchical_bow import (
    fit_factorial_mechanism_model,
    hierarchical_policy_effects,
)
from aidmesh.experiments.bow_factorial import FACTOR_LEVELS


def _campaign_frame() -> pd.DataFrame:
    rows = []
    for context, context_shift in (("a", 0.0), ("b", 0.1)):
        for seed in (1, 2):
            for policy, effect in (("optimized_static", 0.0), ("adatransfer", 0.2)):
                rows.append(
                    {
                        "context": context,
                        "seed": seed,
                        "cap": 10,
                        "quorum_availability": 0.5,
                        "policy": policy,
                        "completion_rate": 0.4 + context_shift + effect,
                    }
                )
    return pd.DataFrame(rows)


def test_nested_bootstrap_retains_exact_paired_effect() -> None:
    effects = hierarchical_policy_effects(_campaign_frame(), repetitions=100)
    row = effects.iloc[0]
    assert row["policy"] == "adatransfer"
    assert abs(row["mean_effect"] - 0.2) < 1e-12
    assert abs(row["ci_low"] - 0.2) < 1e-12
    assert row["context_seed_clusters"] == 4


def test_factorial_model_reports_clustered_mechanism_coefficients() -> None:
    rows = []
    cells = []
    for index in range(32):
        cell = {
            factor: levels[(index >> (position % 5)) & 1]
            for position, (factor, levels) in enumerate(FACTOR_LEVELS.items())
        }
        cells.append(cell)
    for context in ("a", "b"):
        for seed in (1, 2):
            for cell_index, cell in enumerate(cells):
                drift_gain = 0.1 if cell["demand_drift"] == "reversed" else 0.0
                for policy, gain in (
                    ("optimized_static", 0.0),
                    ("adatransfer", 0.05 + drift_gain),
                ):
                    rows.append(
                        {
                            "context": context,
                            "seed": seed,
                            "cell": cell_index,
                            **cell,
                            "policy": policy,
                            "completion_rate": 0.4 + gain,
                        }
                    )
    coefficients, diagnostics = fit_factorial_mechanism_model(pd.DataFrame(rows))
    assert diagnostics["observations"] == 128
    assert diagnostics["context_seed_clusters"] == 4
    assert not coefficients.empty
