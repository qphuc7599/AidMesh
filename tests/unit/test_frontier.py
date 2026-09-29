from __future__ import annotations

import pandas as pd
import pytest

from aidmesh.analysis.frontier import (
    hypervolume_2d,
    load_frontier_frame,
    paired_hypervolume_contrasts,
    per_seed_hypervolume,
)


def test_hypervolume_matches_hand_computed_union() -> None:
    # Point A dominates [0.25, 1.0] x [0, 0.4]; B adds completion 0.9 from 0.5.
    points = [(0.25, 0.4), (0.5, 0.9)]
    expected = 0.25 * 0.4 + 0.5 * 0.9
    assert hypervolume_2d(points) == pytest.approx(expected)


def test_hypervolume_ignores_dominated_and_out_of_reference_points() -> None:
    dominated = [(0.2, 0.5), (0.3, 0.4), (1.2, 0.99)]
    assert hypervolume_2d(dominated) == pytest.approx(0.8 * 0.5)
    assert hypervolume_2d([]) == 0.0
    assert hypervolume_2d([(0.0, 1.0)]) == pytest.approx(1.0)


def test_per_seed_hypervolume_requires_full_cap_grid() -> None:
    frame = pd.DataFrame(
        [
            {
                "scenario_id": "FRONT_C100_K07Q05",
                "method": "aidmesh_static_rights",
                "seed": 1,
                "completion_rate": 0.8,
                "duplicate_exposure": 10,
                "exposure_cap_ratio": 0.1,
            },
            {
                "scenario_id": "FRONT_C200_K07Q05",
                "method": "aidmesh_static_rights",
                "seed": 1,
                "completion_rate": 0.9,
                "duplicate_exposure": 30,
                "exposure_cap_ratio": 0.15,
            },
        ]
    )
    frontier = load_frontier_frame(frame)
    volumes = per_seed_hypervolume(frontier)
    assert len(volumes) == 1
    # exposure normalized by max cap 200: points (0.05, 0.8), (0.15, 0.9)
    assert volumes.iloc[0]["hypervolume"] == pytest.approx(0.1 * 0.8 + 0.85 * 0.9)

    missing = frontier[frontier["cap"] == 100].copy()
    missing.loc[:, "seed"] = 2
    with pytest.raises(ValueError, match="incomplete cap grid"):
        per_seed_hypervolume(pd.concat([frontier, missing], ignore_index=True))


def test_frontier_contrasts_apply_holm_correction() -> None:
    rows = []
    for family_index, family in enumerate(("K4q3", "K7q5", "K10q7")):
        for seed in range(8):
            rows.extend(
                [
                    {
                        "quorum_family": family,
                        "method": "aidmesh_static_rights",
                        "seed": seed,
                        "hypervolume": 0.7 + seed / 1000,
                    },
                    {
                        "quorum_family": family,
                        "method": "aidmesh_transferable_rights",
                        "seed": seed,
                        "hypervolume": (
                            0.72 + seed / 1000 + (seed % 3) / 2000 + family_index / 1000
                        ),
                    },
                ]
            )
    contrasts = paired_hypervolume_contrasts(
        pd.DataFrame(rows),
        bootstrap_resamples=500,
        permutation_resamples=500,
    )
    assert "holm_adjusted_p_value" in contrasts
    assert (contrasts["holm_adjusted_p_value"] >= contrasts["permutation_p_value_greater"]).all()
