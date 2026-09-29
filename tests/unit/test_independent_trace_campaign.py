from __future__ import annotations

from pathlib import Path

import pandas as pd

from aidmesh.experiments.independent_trace_campaign import (
    analyze_independent_trace_campaign,
)
from aidmesh.experiments.trace_preprocess import (
    DieselNetConversionConfig,
    normalize_dieselnet_contacts,
)


def test_dieselnet_converter_deduplicates_exact_reciprocal_event(tmp_path: Path) -> None:
    source = tmp_path / "logs"
    source.mkdir()
    (source / "1252005").write_text(
        "Bus 3010 at 0 0 on route 1 in contact with bus 3033 at 0 0 on route 1 "
        "at time 398:7 for 4096 bytes\n"
        "Bus 3033 at 0 0 on route 1 in contact with bus 3010 at 0 0 on route 1 "
        "at time 398:7 for 8192 bytes\n",
        encoding="utf-8",
    )
    rows = normalize_dieselnet_contacts(
        source,
        config=DieselNetConversionConfig(site_count=1, relay_count=1),
    )
    assert len(rows) == 1
    assert rows[0].node_a == "bus-3010"
    assert rows[0].bytes_per_second == 8192
    assert rows[0].end_us - rows[0].start_us == 1_000_000


def test_family_analysis_does_not_count_three_contexts_as_three_families() -> None:
    rows = []
    for family, contexts, transferable in (
        ("sociopatterns", ("a", "b", "c"), 0.8),
        ("umass_dieselnet", ("d",), 0.2),
    ):
        for context in contexts:
            for seed in (1, 2):
                for method, value in (
                    ("static_rights", 0.1),
                    ("transferable_rights", transferable),
                    ("fixed_home", 0.0),
                    ("first_seen_unsafe", 0.0),
                ):
                    rows.append(
                        {
                            "family": family,
                            "context": context,
                            "seed": seed,
                            "method": method,
                            "cap": 1,
                            "normalized_exposure": 0.0,
                            "completion_rate": value,
                            "primary": True,
                            "profile": "primary",
                        }
                    )
    _, family, leave_one_out, _ = analyze_independent_trace_campaign(pd.DataFrame(rows))
    assert len(family) == 2
    assert set(family["contexts"]) == {1, 3}
    assert len(leave_one_out) == 2
    assert leave_one_out["direction_agrees"].all()
