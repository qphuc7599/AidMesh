"""Publication figure for the three post-review matched/optimality results."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
allocation = json.loads((ROOT / "results/strong_allocation_20260910_v6/summary.json").read_text())
controller = json.loads(
    (ROOT / "results/matched_receipt_controller_20260910/summary.json").read_text()
)
with (
    ROOT
    / "results/postreview_original_registry_20260910/holdout_analysis/"
    "confirmatory_registered_contrasts.csv"
).open(encoding="utf-8", newline="") as handle:
    original_holdout = list(csv.DictReader(handle))

plt.rcParams.update(
    {
        "font.family": "serif",
        "font.size": 8.3,
        "axes.titlesize": 9,
        "axes.labelsize": 8.3,
        "legend.fontsize": 7.2,
        "pdf.fonttype": 42,
    }
)
fig, axes = plt.subplots(1, 3, figsize=(10.2, 3.0))

# (a) Original H1 completion envelope in the fresh disjoint-seed holdout.
partition_map = {
    "RQ1_TRANSFER_VS_ONLINE_P06": 6,
    "RQ1_TRANSFER_VS_ONLINE_CORE": 24,
    "RQ1_TRANSFER_VS_ONLINE_P48": 48,
    "RQ1_TRANSFER_VS_ONLINE_P60_BOUNDARY": 60,
}
rows = [
    row
    for row in original_holdout
    if row["comparison_id"] in partition_map and row["metric"] == "completion_rate"
]
rows.sort(key=lambda row: partition_map[row["comparison_id"]])
x = np.arange(len(rows))
y = np.asarray([float(row["mean_difference"]) for row in rows])
lo = y - np.asarray([float(row["ci_low"]) for row in rows])
hi = np.asarray([float(row["ci_high"]) for row in rows]) - y
axes[0].errorbar(x, y, yerr=[lo, hi], fmt="o-", color="#1261a0", capsize=3, lw=1.4)
axes[0].axhline(0, color="#444444", lw=0.8)
axes[0].axhline(0.05, color="#aa3377", lw=0.8, ls="--", label="+0.05 margin")
axes[0].set_xticks(x, [str(partition_map[row["comparison_id"]]) for row in rows])
axes[0].set_xlabel("Partition duration (h)")
axes[0].set_ylabel(r"$\Delta$ deadline completion")
axes[0].set_title("(a) Fresh original-registry holdout")
axes[0].legend(frameon=False, loc="upper left")

# (b) Strong allocation frontier.
names = {
    "progressive-fill": "PF",
    "fair-floor-completion": "FEC",
    "demand-proportional": "Prop.",
    "utility-only": "Utility",
    "full-box-leximin-then-weighted-utility": "Leximin",
    "weighted-utility-optimum-then-leximin": "Utility+lex",
}
colors = {
    "progressive-fill": "#777777",
    "fair-floor-completion": "#cc6677",
    "demand-proportional": "#999933",
    "utility-only": "#aa4499",
    "full-box-leximin-then-weighted-utility": "#117733",
    "weighted-utility-optimum-then-leximin": "#44aa99",
}
optimum = allocation["means"]["utility-only"]["weighted_utility"]
for method, values in allocation["means"].items():
    axes[1].scatter(
        values["minimum"],
        values["weighted_utility"] / optimum,
        s=36,
        color=colors[method],
        label=names[method],
        zorder=3,
    )
    offsets = {
        "utility-only": (4, 3),
        "weighted-utility-optimum-then-leximin": (4, 3),
        "demand-proportional": (2, -13),
        "progressive-fill": (4, 3),
        "fair-floor-completion": (2, -13),
        "full-box-leximin-then-weighted-utility": (4, 4),
    }
    axes[1].annotate(
        names[method],
        (values["minimum"], values["weighted_utility"] / optimum),
        xytext=offsets[method],
        textcoords="offset points",
        fontsize=6.8,
    )
axes[1].set_xlabel("Worst-group service ratio")
axes[1].set_ylabel("Utility / utility optimum")
axes[1].set_ylim(0.80, 1.015)
axes[1].set_title("(b) Full-box allocation frontier")
axes[1].grid(color="#dddddd", lw=0.5, zorder=0)

# (c) H4 receipt delay under the reconnect burst, no forged priority packets.
rows = [
    row
    for row in controller["primaryContrasts"]
    if row["partition_hours"] == 2 and row["invalid_priority_fraction"] == 0
]
rows.sort(key=lambda row: row["load_factor"])
y = np.asarray([-row["meanDifference"] / 60 for row in rows])
lo = y - np.asarray([-row["ci95"][1] / 60 for row in rows])
hi = np.asarray([-row["ci95"][0] / 60 for row in rows]) - y
axes[2].bar(np.arange(len(rows)), y, color="#ddcc77", edgecolor="#665c2d", width=0.62)
axes[2].errorbar(
    np.arange(len(rows)), y, yerr=[lo, hi], fmt="none", ecolor="#222222", capsize=3, lw=1
)
axes[2].set_xticks(np.arange(len(rows)), [f"{row['load_factor']:.2f}" for row in rows])
axes[2].set_xlabel("Offered load / capacity")
axes[2].set_ylabel("Median delay reduction (min)")
axes[2].set_title("(c) Matched receipt controller")

for axis in axes:
    axis.spines[["top", "right"]].set_visible(False)
fig.tight_layout(w_pad=1.8)
for target in (
    ROOT / "source/figs/postreview_core_results.pdf",
    ROOT / "overleaf/figs/postreview_core_results.pdf",
):
    target.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(target, bbox_inches="tight")
plt.close(fig)
