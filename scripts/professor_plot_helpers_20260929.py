from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "overleaf" / "figs"
OUT.mkdir(parents=True, exist_ok=True)

NAVY = "#102A43"
BLUE = "#2878B5"
TEAL = "#159D8C"
GREEN = "#2A9D60"
GOLD = "#E9A23B"
CORAL = "#DF6C5B"
RED = "#C84A52"
PURPLE = "#6C5AAE"
INK = "#263238"
MUTED = "#61717D"
GRID = "#DCE5EA"
PALE = "#F4F8FA"

mpl.rcParams.update({
    "font.family": "DejaVu Sans",
    "font.size": 8.4,
    "axes.titlesize": 10.2,
    "axes.titleweight": "bold",
    "axes.labelsize": 8.7,
    "axes.edgecolor": "#9AA9B2",
    "axes.linewidth": 0.7,
    "xtick.labelsize": 7.6,
    "ytick.labelsize": 7.6,
    "legend.fontsize": 7.5,
    "figure.facecolor": "white",
    "axes.facecolor": "white",
    "savefig.facecolor": "white",
    "pdf.fonttype": 42,
    "ps.fonttype": 42,
})


def finish(fig: plt.Figure, name: str) -> None:
    fig.savefig(OUT / f"{name}.pdf", bbox_inches="tight", pad_inches=0.035)
    fig.savefig(OUT / f"{name}.png", dpi=210, bbox_inches="tight", pad_inches=0.035)
    plt.close(fig)


def panel_label(ax: plt.Axes, label: str, title: str) -> None:
    ax.text(0.0, 1.045, label, transform=ax.transAxes, color=BLUE,
            fontsize=9.8, fontweight="bold", va="bottom")
    ax.text(0.075, 1.045, title, transform=ax.transAxes, color=NAVY,
            fontsize=9.4, fontweight="bold", va="bottom")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def h1_figure() -> None:
    replay = read_csv(ROOT / "results/postreview_original_registry_20260910/replay_analysis/confirmatory_registered_contrasts.csv")
    hold = read_csv(ROOT / "results/postreview_original_registry_20260910/holdout_analysis/confirmatory_registered_contrasts.csv")
    ids = [
        "RQ1_TRANSFER_VS_ONLINE_CORE", "RQ1_TRANSFER_VS_ONLINE_WIT06_BOUNDARY",
        "RQ1_TRANSFER_VS_ONLINE_WIT07", "RQ1_TRANSFER_VS_ONLINE_WIT09",
        "RQ1_TRANSFER_VS_ONLINE_P06", "RQ1_TRANSFER_VS_ONLINE_P48",
        "RQ1_TRANSFER_VS_ONLINE_P60_BOUNDARY", "RQ1_TRANSFER_VS_ONLINE_ADV05",
        "RQ1_TRANSFER_VS_ONLINE_ADV30",
    ]
    labels = ["Core · 24 h", "Witness 0.60", "Witness 0.70", "Witness 0.90",
              "Partition 6 h", "Partition 48 h", "Partition 60 h",
              "Adversarial 0.05", "Adversarial 0.30"]
    def pick(rows):
        by = {r["comparison_id"]: r for r in rows if r["metric"] == "completion_rate"}
        return [(float(by[k]["mean_difference"]), float(by[k]["ci_low"]), float(by[k]["ci_high"])) for k in ids]
    rp, hp = pick(replay), pick(hold)
    y = np.arange(len(ids))[::-1]

    fig, ax = plt.subplots(figsize=(7.25, 3.75))
    ax.axvspan(-0.18, 0, color="#FCEAE7", zorder=0)
    ax.axvspan(0, 0.05, color="#FFF5D9", zorder=0)
    ax.axvspan(0.05, 1.0, color="#E8F5F0", zorder=0)
    ax.axvline(0, color=RED, lw=1.1)
    ax.axvline(0.05, color=GOLD, lw=1.1, ls="--")
    for i, ((rm, rl, rh), (hm, hl, hh)) in enumerate(zip(rp, hp)):
        yy = y[i]
        ax.errorbar(rm, yy + 0.14, xerr=[[rm-rl], [rh-rm]], fmt="D", ms=4.2,
                    mfc="white", mec=MUTED, ecolor="#93A4AF", elinewidth=1.4,
                    capsize=2.2, zorder=2)
        color = TEAL if hm >= 0.05 else (GOLD if hm >= 0 else RED)
        ax.errorbar(hm, yy - 0.14, xerr=[[hm-hl], [hh-hm]], fmt="o", ms=5.4,
                    mfc=color, mec="white", mew=0.7, ecolor=color,
                    elinewidth=2.1, capsize=2.5, zorder=3)
        ax.text(hh + 0.012, yy - 0.14, f"{hm:+.3f}", va="center", fontsize=7.2, color=INK)
    ax.set_yticks(y, labels)
    ax.set_xlim(-0.19, 1.04)
    ax.set_ylim(-0.7, len(ids)-0.3)
    ax.grid(axis="x", color="white", linewidth=0.8)
    ax.set_xlabel("AidMesh − Online-Only deadline completion")
    ax.set_title("H1 operating envelope: reference and disjoint-seed holdout", loc="left", color=NAVY, pad=10)
    legend_handles = [
        mpl.lines.Line2D([], [], marker="D", ms=4.5, mfc="white", mec=MUTED,
                         color="none", label="reference-seed evaluation"),
        mpl.lines.Line2D([], [], marker="o", ms=5.5, mfc=TEAL, mec="white",
                         color="none", label="disjoint-seed holdout"),
    ]
    ax.legend(handles=legend_handles, loc="upper right", ncol=2, frameon=False,
              borderaxespad=0.25, handletextpad=0.35, columnspacing=0.9)
    ax.text(0.01, 0.02, "reversal", transform=ax.transAxes, color=RED, fontsize=7.0, fontweight="bold")
    ax.text(0.158, 0.02, "below +0.05 margin", transform=ax.transAxes, color="#A37200", fontsize=7.0, fontweight="bold")
    ax.text(0.48, 0.02, "supported envelope", transform=ax.transAxes, color=TEAL, fontsize=7.0, fontweight="bold")
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.tick_params(axis="y", length=0)
    fig.subplots_adjust(left=0.21, right=0.97, top=0.88, bottom=0.14)
    finish(fig, "h1_operating_envelope")

def receipt_figure() -> None:
    data = json.loads((ROOT / "results/matched_receipt_controller_20260910/summary.json").read_text())
    rows = data["primaryContrasts"]
    fig, (ax, bx) = plt.subplots(1, 2, figsize=(7.25, 3.35), sharey=True,
                                 gridspec_kw={"width_ratios": [0.92, 1.08]})
    loads = [0.65, 0.85, 0.95]
    colors = {0.65: BLUE, 0.85: TEAL, 0.95: PURPLE}
    ys = {0.65: 2, 0.85: 1, 0.95: 0}
    for part, axis, unit, scale in [(0.0, ax, "seconds", 1.0), (2.0, bx, "minutes", 60.0)]:
        for load in loads:
            selected = [r for r in rows if r["partition_hours"] == part and r["load_factor"] == load]
            for r in selected:
                offset = 0.11 if r["invalid_priority_fraction"] == 0 else -0.11
                mean = -r["meanDifference"] / scale
                lo = -r["ci95"][1] / scale
                hi = -r["ci95"][0] / scale
                marker = "o" if r["invalid_priority_fraction"] == 0 else "s"
                fill = colors[load] if r["invalid_priority_fraction"] == 0 else "white"
                axis.errorbar(mean, ys[load] + offset, xerr=[[mean-lo], [hi-mean]],
                              fmt=marker, ms=6.2, mfc=fill, mec=colors[load], mew=1.2,
                              ecolor=colors[load], elinewidth=2.1, capsize=3, zorder=3)
        axis.axvline(0, color=NAVY, lw=0.9)
        axis.grid(axis="x", color=GRID, linewidth=0.6)
        axis.set_xlabel(f"Median receipt-delay reduction ({unit})  →")
        axis.spines[["top", "right", "left"]].set_visible(False)
        axis.tick_params(axis="y", length=0)
    ax.set_yticks([2, 1, 0], ["Load 0.65", "Load 0.85", "Load 0.95"])
    ax.set_xlim(-1.5, 70)
    bx.set_xlim(-4, 119)
    panel_label(ax, "A", "Continuous connectivity")
    panel_label(bx, "B", "Two-hour reconnection burst")
    bx.text(0.04, 0.055, "12/12 cells · 30/30 paired wins",
            transform=bx.transAxes, ha="left", va="bottom", fontsize=7.3,
            color=TEAL, fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.25", facecolor="white", edgecolor="#B7DCD5", alpha=0.92))
    handles = [
        mpl.lines.Line2D([], [], color=INK, marker="o", mfc=INK, linestyle="", label="valid priority only"),
        mpl.lines.Line2D([], [], color=INK, marker="s", mfc="white", linestyle="", label="10% invalid attempts rejected"),
    ]
    fig.legend(handles=handles, loc="lower center", ncol=2, frameon=False, bbox_to_anchor=(0.5, -0.005))
    fig.subplots_adjust(left=0.13, right=0.98, top=0.82, bottom=0.23, wspace=0.20)
    finish(fig, "receipt_priority_evidence")
