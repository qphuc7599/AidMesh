"""Paired seed-level bootstrap; no pairing of unrelated runs or survivor-only median."""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

out = Path(sys.argv[1])
assert (out / "COMPLETE.json").exists(), "grid must finish before final analysis"
frame = pd.read_json(out / "runs.jsonl", lines=True)
keys = ["seed", "partition", "bandwidth_kbit", "buffer_mb"]
left = frame[frame.policy == "risk_priority_spray"].set_index(keys)
right = frame[frame.policy == "binary_spray_wait"].set_index(keys)
assert left.index.equals(right.index)
delta = left[["capped_mean_hours", "delivered"]] - right[["capped_mean_hours", "delivered"]]
delta = delta.rename(
    columns={"capped_mean_hours": "delta_capped_mean_hours", "delivered": "delta_delivered"}
)
delta["both_medians_reached"] = left.median_reached & right.median_reached
delta["delta_median_hours"] = left.median_hours - right.median_hours
delta.to_csv(out / "paired_runs.csv")
rows = []
for settings, group in delta.reset_index().groupby(["partition", "bandwidth_kbit", "buffer_mb"]):
    assert len(group) == 30
    values = group.delta_capped_mean_hours.to_numpy()
    rng = np.random.default_rng(78123)
    means = values[rng.integers(0, len(values), size=(10000, len(values)))].mean(axis=1)
    rows.append(
        dict(
            partition=int(settings[0]),
            bandwidth_kbit=int(settings[1]),
            buffer_mb=int(settings[2]),
            paired_seeds=len(group),
            delta_capped_mean_hours=float(values.mean()),
            bootstrap95_low=float(np.quantile(means, 0.025)),
            bootstrap95_high=float(np.quantile(means, 0.975)),
            both_medians_reached=int(group.both_medians_reached.sum()),
            median_differences_all_zero=bool((group.delta_median_hours.dropna() == 0).all()),
        )
    )
pd.DataFrame(rows).to_csv(out / "paired_summary.csv", index=False)
summary = dict(
    contrasts=len(rows),
    paired_runs=len(delta),
    all_capped_mean_differences_zero=bool((delta.delta_capped_mean_hours == 0).all()),
    all_delivery_differences_zero=bool((delta.delta_delivered == 0).all()),
    original_H4_established=False,
    scope=("Synthetic 8-site/250-bundle/zero-loss routing grid. "
           "95% intervals are per-cell descriptive, not simultaneous confirmatory intervals."),
)
(out / "paired_analysis.json").write_text(json.dumps(summary, indent=2))
print(json.dumps(summary))
