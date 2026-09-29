from pathlib import Path

import pytest

from aidmesh.experiments.frontier_practicality import evaluate_frontier_practicality


def test_nonzero_practical_margin_is_distinct_from_directional_significance(
    tmp_path: Path,
) -> None:
    required = Path("results/confirmatory/frontier/frontier_hypervolume_contrasts.csv")
    if not required.is_file():
        pytest.skip("retrospective result input is not included in the source-only repository")
    result = evaluate_frontier_practicality(
        Path("configs/supporting_2026Q3/frontier_practicality/registry.yaml"),
        tmp_path,
    )
    assert result["status"] == "retrospective_sensitivity_not_preregistered"
    assert result["margin"] == 0.01
    assert result["directional_positive"] > result["practically_meaningful"]
    assert any(
        row["stratum"] == "sfhh" and row["practically_meaningful"]
        for row in result["decisions"]
    )
    assert all(
        not row["practically_meaningful"]
        for row in result["decisions"]
        if row["evidence_level"] == "frozen_controlled_quorum"
    )

