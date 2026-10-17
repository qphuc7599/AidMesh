"""Create a journal forest plot for clustered external-validity evidence."""

from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
INCIDENT = ROOT / "results/catr_confirmatory_20260822/analysis/incident_clustered_effects.csv"
DIESEL = ROOT / "results/supporting_2026Q3/e2e_dieselnet_scale/analysis/e2e_paired_hierarchical_bootstrap.csv"
OUTPUT = ROOT / "source/figs/external_validity_clustered.pdf"

plt.rcParams.update(
    {
        "font.family": "serif",
        "font.size": 8.5,
        "axes.titlesize": 9.5,
        "axes.labelsize": 8.5,
        "xtick.labelsize": 8,
        "ytick.labelsize": 8,
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
    }
)

fig, axes = plt.subplots(1, 2, figsize=(7.25, 2.75), constrained_layout=True)

incident = pd.read_csv(INCIDENT)
order = [
    ("adatransfer", "stable", "AdaTransfer · stable"),
    ("adatransfer", "shifting", "AdaTransfer · shifting"),
    ("robust_static", "stable", "Robust-static · stable"),
    ("robust_static", "shifting", "Robust-static · shifting"),
    ("reclaimable_transfer", "stable", "Reclaimable · stable"),
    ("reclaimable_transfer", "shifting", "Reclaimable · shifting"),
]
colors = {"stable": "#365F91", "shifting": "#C45A2D"}
for position, (policy, regime, label) in enumerate(reversed(order)):
    row = incident[(incident.policy == policy) & (incident.demand_drift == regime)].iloc[0]
    axes[0].errorbar(
        row.mean_completion_delta,
        position,
        xerr=[
            [row.mean_completion_delta - row.ci95_low],
            [row.ci95_high - row.mean_completion_delta],
        ],
        fmt="o",
        color=colors[regime],
        ecolor=colors[regime],
        markersize=4.5,
        capsize=2.5,
        linewidth=1.3,
    )
axes[0].axvline(0, color="#4A4A4A", linewidth=0.8, linestyle="--")
axes[0].set_yticks(range(len(order)), [item[2] for item in reversed(order)])
axes[0].set_xlabel("Completion difference from CATR")
axes[0].set_title("(a) Asturies: incident-cluster inference ($n=23$)", loc="left")
axes[0].xaxis.set_major_locator(MaxNLocator(5))
axes[0].grid(axis="x", color="#D9DEE7", linewidth=0.55)
axes[0].set_axisbelow(True)

diesel = pd.read_csv(DIESEL)
metrics = [
    ("completion_rate", "Completion"),
    ("physical_release_rate", "Physical release"),
    ("receipt_delivery_rate", "Receipt delivery"),
]
regime_colors = {"fallback": "#117A65", "quorum": "#7655A6"}
positions = []
labels = []
position = 0
for metric, label in reversed(metrics):
    for regime in ("quorum", "fallback"):
        row = diesel[(diesel.metric == metric) & (diesel.witness_regime == regime)].iloc[0]
        axes[1].errorbar(
            row.estimate,
            position,
            xerr=[[row.estimate - row.ci95_low], [row.ci95_high - row.estimate]],
            fmt="s" if regime == "fallback" else "D",
            color=regime_colors[regime],
            ecolor=regime_colors[regime],
            markersize=4.3,
            capsize=2.5,
            linewidth=1.3,
        )
        positions.append(position)
        labels.append(f"{label} · {regime}")
        position += 1
axes[1].axvline(0, color="#4A4A4A", linewidth=0.8, linestyle="--")
axes[1].set_yticks(positions, labels)
axes[1].set_xlabel("Transferable minus static")
axes[1].set_title("(b) DieselNet: workload-seed bootstrap ($n=8$)", loc="left")
axes[1].xaxis.set_major_locator(MaxNLocator(4))
axes[1].grid(axis="x", color="#D9DEE7", linewidth=0.55)
axes[1].set_axisbelow(True)

for axis in axes:
    axis.spines[["top", "right"]].set_visible(False)
    axis.margins(y=0.14)

OUTPUT.parent.mkdir(parents=True, exist_ok=True)
fig.savefig(OUTPUT, bbox_inches="tight")
print(OUTPUT)
