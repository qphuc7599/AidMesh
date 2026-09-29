from __future__ import annotations

from pathlib import Path

import pandas as pd

from aidmesh.experiments.bow_trace_families import (
    family_balanced_summary,
    load_trace_family_registry,
)

ROOT = Path(__file__).resolve().parents[2]


def test_registered_primary_inputs_resolve_to_two_independent_families() -> None:
    required = ROOT / "external" / "sociopatterns" / "normalized_slots" / "invs13" / "contacts.csv"
    if not required.is_file():
        import pytest

        pytest.skip("external normalized traces are distributed separately")
    registry = load_trace_family_registry(
        ROOT / "configs" / "trace_families" / "registry.yaml",
        workspace_root=ROOT,
    )
    primary = [item for item in registry["contexts"] if item["primary"]]
    assert {item["family"] for item in primary} == {
        "sociopatterns",
        "umass_dieselnet",
    }
    assert all((item["resolved_path"] / "contacts.csv").is_file() for item in primary)


def test_family_summary_does_not_count_contexts_as_independent_families() -> None:
    frame = pd.DataFrame(
        [
            {
                "family": "a",
                "registered_context": context,
                "policy": "p",
                "completion_rate": value,
                "end_to_end_settlement_probability": value,
            }
            for context, value in (("c1", 0.0), ("c2", 1.0))
        ]
    )
    summary = family_balanced_summary(frame)
    assert len(summary) == 1
    assert summary.iloc[0]["contexts"] == 2
    assert summary.iloc[0]["mean_completion_rate"] == 0.5
