"""Render the fixed H3 replication as a compact journal figure."""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter

ROOT = Path(__file__).resolve().parents[1]
SUMMARY = ROOT / "results/h3_fixed_replication_20260928/summary.json"
ORIGINAL = ROOT / "results/h3_fixed_replication_20260928/original_contract_analysis.json"
OUTPUT = ROOT / "source/figs/allocation_evidence.pdf"

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


def main() -> int:
    summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
    original = json.loads(ORIGINAL.read_text(encoding="utf-8"))
    labels = {
        "progressive-fill": "Ray-constrained PF",
        "equal-share": "Equal share",
        "demand-proportional": "Demand proportional",
        "utility-only": "Utility only",
    }
    fairness = summary["fairnessContrasts"]
    fig, axes = plt.subplots(1, 2, figsize=(7.25, 2.7), constrained_layout=True)

    for position, row in enumerate(reversed(fairness)):
        low, high = row["twoSided95"]
        mean = row["meanMinimumServiceDifference"]
        axes[0].errorbar(
            mean,
            position,
            xerr=[[mean - low], [high - mean]],
            fmt="o",
            color="#245b85",
            ecolor="#245b85",
            capsize=2.8,
            linewidth=1.4,
            markersize=4.8,
        )
        axes[0].text(
            high + 0.004,
            position,
            f"W/T/L {row['conditionWins']}/{row['conditionTies']}/{row['conditionLosses']}",
            fontsize=6.8,
            ha="left",
            va="center",
            color="#3b4652",
        )
    axes[0].axvline(0, color="#555555", linestyle="--", linewidth=0.8)
    axes[0].set_yticks(
        range(len(fairness)),
        [labels[row["comparator"]] for row in reversed(fairness)],
    )
    axes[0].set_xlabel("Minimum-service difference")
    axes[0].set_title("(a) Full-box leximin fairness", loc="left")
    axes[0].set_xlim(-0.004, 0.305)
    axes[0].grid(axis="x", color="#d9dee7", linewidth=0.55)

    efficiency_rows = [
        (
            "Weighted utility",
            summary["utilityLoss"]["meanRelativeLoss"],
            summary["utilityLoss"],
            "#b85042",
        ),
        (
            "Fulfilled units",
            original["efficiency"]["meanRelativeFulfilledUnitLoss"],
            original["efficiency"],
            "#4f7f52",
        ),
    ]
    for y, (_label, mean, row, color) in enumerate(reversed(efficiency_rows)):
        low, high = row["twoSided95"]
        axes[1].errorbar(
            mean,
            y,
            xerr=[[mean - low], [high - mean]],
            fmt="o",
            color=color,
            ecolor=color,
            capsize=3,
            linewidth=1.5,
            markersize=5.2,
            label="two-sided 95% interval" if y == 0 else None,
        )
        axes[1].plot(
            row["oneSided95Upper"],
            y,
            marker="|",
            markersize=12,
            markeredgewidth=1.6,
            color=color,
            label="one-sided 95% upper bound" if y == 0 else None,
        )
    axes[1].axvline(
        summary["utilityLoss"]["margin"],
        color="#222222",
        linestyle="--",
        linewidth=1,
        label="3% noninferiority margin",
    )
    axes[1].set_yticks(range(len(efficiency_rows)), [row[0] for row in reversed(efficiency_rows)])
    axes[1].xaxis.set_major_formatter(PercentFormatter(1.0, decimals=1))
    axes[1].set_xlabel("Relative efficiency loss vs utility-only")
    axes[1].set_title("(b) Fixed noninferiority decision", loc="left")
    axes[1].grid(axis="x", color="#d9dee7", linewidth=0.55)
    axes[1].legend(frameon=False, fontsize=7, loc="upper left")
    axes[1].set_ylim(-0.65, 1.65)

    for axis in axes:
        axis.spines[["top", "right"]].set_visible(False)
        axis.set_axisbelow(True)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUTPUT, bbox_inches="tight")
    plt.close(fig)
    print(OUTPUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
