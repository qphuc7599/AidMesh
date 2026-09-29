from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pandas as pd

from aidmesh.experiments.e2e_campaign import load_e2e_registry
from aidmesh.experiments.semantic_e2e_campaign import _select_conditions


def test_semantic_e2e_selects_min_max_caps_across_three_families() -> None:
    root = Path(__file__).resolve().parents[2]
    registry = replace(
        load_e2e_registry(root / "configs" / "e2e" / "semantic_6h.yaml"),
        seeds=(8601,),
    )
    frame = pd.DataFrame(
        [
            {
                "family": family,
                "registered_context": context,
                "seed": 8601,
                "cap": cap,
            }
            for family, context, caps in (
                ("sociopatterns", "invs13", (8, 62)),
                ("umass_dieselnet", "dieselnet05", (4, 15)),
                ("ene5ai", "ene5ai", (4, 6)),
            )
            for cap in caps
        ]
    )
    conditions = _select_conditions(frame, registry)
    assert len(conditions) == 3 * 2 * 2
    assert {row["threshold"] for row in conditions} == {2, 3}
    assert {row["registered_context"] for row in conditions} == {
        "invs13",
        "dieselnet05",
        "ene5ai",
    }
