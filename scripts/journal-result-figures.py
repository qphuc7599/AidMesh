"""Publication figures from recorded AidMesh evidence; no synthetic observations."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D
from matplotlib.patches import FancyArrowPatch, Rectangle

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "overleaf/figs"
BLUE, TEAL, RED, GOLD = "#356B9A", "#177E75", "#BC593E", "#AF7C24"
INK, GRAY, GRID = "#222B33", "#71808B", "#E3E8EB"
mpl.rcParams.update(
    {
        "font.family": "Arial",
        "font.size": 8,
        "axes.labelsize": 8,
        "xtick.labelsize": 7,
        "ytick.labelsize": 8,
        "legend.fontsize": 7,
        "axes.linewidth": 0.65,
        "axes.edgecolor": GRAY,
        "text.color": INK,
        "axes.labelcolor": INK,
        "figure.facecolor": "white",
        "axes.facecolor": "white",
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "svg.fonttype": "none",
    }
)


def read(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def save(fig, name):
    for ext in ["pdf", "svg", "png"]:
        fig.savefig(OUT / f"{name}.{ext}", dpi=320, bbox_inches="tight", pad_inches=0.08)
    plt.close(fig)


def axes(ax, grid="x"):
    ax.spines[["top", "right"]].set_visible(False)
    if grid == "x":
        ax.spines["left"].set_visible(False)
        ax.tick_params(axis="y", length=0, pad=7)
    ax.grid(axis=grid, color=GRID, lw=0.5)
    ax.set_axisbelow(True)


def title(ax, letter, text):
    ax.text(0, 1.08, letter, transform=ax.transAxes, fontsize=11, weight="bold")
    ax.text(0.065, 1.08, text, transform=ax.transAxes, fontsize=8.5, weight="bold")


def interval(ax, mean, low, high, y, color, marker="o", hollow=False, size=4.5):
    ax.errorbar(
        mean,
        y,
        xerr=[[mean - low], [high - mean]],
        fmt=marker,
        ms=size,
        mfc="white" if hollow else color,
        mec=color,
        mew=0.8,
        ecolor=color,
        elinewidth=1.2,
        capsize=2,
        zorder=4,
    )


def h1():
    rows = []
    for split in ["replay_analysis", "holdout_analysis"]:
        with (
            ROOT
            / "results/postreview_original_registry_20260910"
            / split
            / "confirmatory_registered_contrasts.csv"
        ).open() as f:
            rows.append(
                {
                    r["comparison_id"]: r
                    for r in csv.DictReader(f)
                    if r["metric"] == "completion_rate"
                }
            )
    suffixes = [
        "CORE",
        "WIT06_BOUNDARY",
        "WIT07",
        "WIT09",
        "P06",
        "P48",
        "P60_BOUNDARY",
        "ADV05",
        "ADV30",
    ]
    labels = [
        "Reference setting",
        "Witness availability 0.60",
        "Witness availability 0.70",
        "Witness availability 0.90",
        "Partition duration 6 h",
        "Partition duration 48 h",
        "Partition duration 60 h",
        "Adversarial fraction 0.05",
        "Adversarial fraction 0.30",
    ]
    fig = plt.figure(figsize=(7.2, 3.65))
    ax = fig.add_axes([0.25, 0.17, 0.49, 0.70])
    tab = fig.add_axes([0.775, 0.17, 0.215, 0.70], sharey=ax)
    y = np.array([10, 8.6, 7.6, 6.6, 5.2, 4.2, 3.2, 1.8, 0.8])
    ax.axvline(0, color=GRAY, lw=0.8)
    ax.axvline(0.05, color=GOLD, lw=1, ls=(0, (3, 3)))
    for i, suffix in enumerate(suffixes):
        key = "RQ1_TRANSFER_VS_ONLINE_" + suffix
        for j, data in enumerate(rows):
            r = data[key]
            m, lo, hi = [float(r[k]) for k in ["mean_difference", "ci_low", "ci_high"]]
            c = GRAY if j == 0 else TEAL if m > 0.05 else RED
            interval(
                ax, m, lo, hi, y[i] + (0.14 if j == 0 else -0.14), c, "D" if j == 0 else "o", j == 0
            )
            if j == 1:
                tab.text(0, y[i], f"{m:+.3f}", va="center", color=c, weight="bold", fontsize=8)
                tab.text(0.43, y[i], f"[{lo:+.3f}, {hi:+.3f}]", va="center", fontsize=7)
    for yy in [9.3, 5.9, 2.5]:
        ax.axhline(yy, color=GRID, lw=0.7)
        tab.axhline(yy, color=GRID, lw=0.7)
    ax.set(
        yticks=y,
        yticklabels=labels,
        xlim=(-0.22, 1.05),
        ylim=(0, 10.8),
        xlabel="Deadline-completion difference",
    )
    axes(ax)
    tab.set_xlim(0, 1)
    tab.axis("off")
    tab.text(0, 11.12, "Holdout", weight="bold", fontsize=8)
    tab.text(0.43, 11.12, "95% CI", weight="bold", fontsize=8)
    ax.text(0, 1.04, "AidMesh − Online-Only", transform=ax.transAxes, weight="bold")
    handles = [
        Line2D([], [], marker="D", color=GRAY, mfc="white", ls="", ms=4, label="Reference seeds"),
        Line2D([], [], marker="o", color=TEAL, ls="", ms=4, label="Disjoint holdout"),
        Line2D([], [], color=GOLD, ls="--", lw=1, label="+0.05 decision margin"),
    ]
    fig.legend(
        handles=handles, loc="lower center", ncol=3, frameon=False, bbox_to_anchor=(0.57, -0.01)
    )
    save(fig, "h1_operating_envelope")


def external():
    data = read("results/external_validity_three_family_20260928/fixed_analysis.json")
    effects = {(r["control"], r["family"]): r for r in data["effects"]}
    families = ["ene5ai", "sociopatterns", "umass_dieselnet", "all-three"]
    labels = ["ENE5AI", "SocioPatterns", "DieselNet", "Family-balanced"]
    fig, ax = plt.subplots(figsize=(7.2, 2.8))
    fig.subplots_adjust(left=0.18, right=0.97, bottom=0.23, top=0.79)
    for i, family in enumerate(families):
        y = 3 - i
        rs = [effects[(c, family)] for c in ["optimized_static", "periodic_sync"]]
        ax.plot([r["mean"] * 100 for r in rs], [y, y], color="#BBC6CC", lw=1)
        for r, c, marker in zip(rs, [RED, TEAL], ["s", "D"], strict=True):
            m = r["mean"] * 100
            lo, hi = np.array(r["ci95"]) * 100
            interval(ax, m, lo, hi, y, c, marker, size=5)
            ax.annotate(
                f"{m:+.1f}",
                (m, y),
                xytext=(0, 10),
                textcoords="offset points",
                ha="center",
                color=c,
                fontsize=8,
                weight="bold" if i == 3 else "normal",
            )
    ax.axhline(0.65, color=GRID, lw=0.7)
    ax.axvline(0, color=GRAY, lw=0.8)
    ax.set(
        yticks=range(4),
        yticklabels=labels[::-1],
        ylim=(-0.45, 3.65),
        xlim=(-8, 30),
        xlabel="AdaTransfer completion difference (percentage points)",
    )
    ax.get_yticklabels()[0].set_weight("bold")
    axes(ax)
    fig.legend(
        handles=[
            Line2D([], [], marker="s", color=RED, ls="", label="vs optimized static"),
            Line2D([], [], marker="D", color=TEAL, ls="", label="vs periodic synchronization"),
        ],
        frameon=False,
        ncol=2,
        loc="upper center",
        bbox_to_anchor=(0.57, 0.99),
    )
    save(fig, "three_family_replication")


def receipt():
    rows = read("results/matched_receipt_controller_20260910/summary.json")["primaryContrasts"]
    fig, axs = plt.subplots(1, 2, figsize=(7.2, 3.15))
    fig.subplots_adjust(left=0.09, right=0.985, bottom=0.26, top=0.82, wspace=0.30)
    for j, (part, scale, unit) in enumerate([(0, 1, "s"), (2, 60, "min")]):
        ax = axs[j]
        for invalid, color, marker, offset in [
            (False, TEAL, "o", -0.002),
            (True, BLUE, "s", 0.002),
        ]:
            selected = sorted(
                [
                    r
                    for r in rows
                    if r["partition_hours"] == part
                    and (r["invalid_priority_fraction"] > 0) == invalid
                ],
                key=lambda r: r["load_factor"],
            )
            x = np.array([r["load_factor"] for r in selected])
            m = -np.array([r["meanDifference"] for r in selected]) / scale
            lo = -np.array([r["ci95"][1] for r in selected]) / scale
            hi = -np.array([r["ci95"][0] for r in selected]) / scale
            ax.plot(x, m, color=color, lw=1.1, ls="--" if invalid else "-")
            ax.errorbar(
                x + offset,
                m,
                yerr=[m - lo, hi - m],
                fmt=marker,
                ms=4.8,
                mfc="white" if invalid else color,
                mec=color,
                ecolor=color,
                elinewidth=1.1,
                capsize=3,
                zorder=5,
            )
        ax.set(
            xticks=[0.65, 0.85, 0.95],
            xlim=(0.625, 0.975),
            ylim=(-3 if j == 0 else 0, 76 if j == 0 else 128),
            xlabel="Offered load",
            ylabel=f"Median delay reduction ({unit})",
        )
        axes(ax, "y")
        title(ax, chr(97 + j), ["Continuous connectivity", "After a 2 h reconnection burst"][j])
    fig.legend(
        handles=[
            Line2D([], [], color=TEAL, marker="o", ms=4, label="Valid priority traffic"),
            Line2D(
                [],
                [],
                color=BLUE,
                marker="s",
                mfc="white",
                ls="--",
                ms=4,
                label="10% invalid priority attempts",
            ),
        ],
        frameon=False,
        ncol=2,
        loc="lower center",
        bbox_to_anchor=(0.53, 0.015),
    )
    save(fig, "receipt_priority_evidence")


def allocation():
    s = read("results/h3_fixed_replication_20260928/summary.json")
    o = read("results/h3_fixed_replication_20260928/original_contract_analysis.json")
    fig = plt.figure(figsize=(7.2, 3.7))
    ax = fig.add_axes([0.23, 0.54, 0.45, 0.32])
    counts = fig.add_axes([0.73, 0.54, 0.25, 0.32], sharey=ax)
    names = {
        "progressive-fill": "Ray-constrained PF",
        "equal-share": "Equal share",
        "demand-proportional": "Demand proportional",
        "utility-only": "Utility only",
    }
    for i, r in enumerate(s["fairnessContrasts"]):
        y = 3 - i
        interval(ax, r["meanMinimumServiceDifference"], *r["twoSided95"], y, TEAL)
        counts.text(0, y, f"{r['conditionWins']:,}", va="center", color=TEAL)
        counts.text(0.44, y, f"{r['conditionTies']:,}", va="center", color=GRAY)
        counts.text(0.88, y, str(r["conditionLosses"]), va="center", color=INK)
    ax.set(
        yticks=range(4),
        yticklabels=[names[r["comparator"]] for r in s["fairnessContrasts"]][::-1],
        xlim=(-0.008, 0.265),
        ylim=(-0.45, 3.5),
        xlabel="Minimum-service gain",
    )
    axes(ax)
    title(ax, "a", "Fairness benefit")
    counts.axis("off")
    for x, txt in [(0, "Wins"), (0.44, "Ties"), (0.88, "Losses")]:
        counts.text(x, 3.8, txt, fontsize=7, weight="bold")
    bx = fig.add_axes([0.23, 0.13, 0.45, 0.20])
    note = fig.add_axes([0.73, 0.13, 0.25, 0.20])
    note.axis("off")
    for y, mean, r in [
        (1, s["utilityLoss"]["meanRelativeLoss"], s["utilityLoss"]),
        (0, o["efficiency"]["meanRelativeFulfilledUnitLoss"], o["efficiency"]),
    ]:
        interval(bx, mean * 100, *[v * 100 for v in r["twoSided95"]], y, BLUE)
        bx.plot(r["oneSided95Upper"] * 100, y, marker="|", ms=12, mew=1.3, color=INK)
    bx.axvline(3, color=RED, ls=(0, (3, 3)), lw=1)
    bx.set(
        yticks=[0, 1],
        yticklabels=["Fulfilled units", "Weighted utility"],
        xlim=(-0.12, 3.32),
        ylim=(-0.5, 1.65),
        xlabel="Relative loss vs utility-only (%)",
    )
    axes(bx)
    title(bx, "b", "Efficiency cost")
    bx.text(3, 1.52, "3% margin", ha="right", fontsize=7, color=RED)
    note.text(0, 0.8, "2.45% mean loss", color=BLUE, weight="bold", fontsize=9)
    note.text(0, 0.43, "2.56% one-sided upper bound", fontsize=7)
    note.text(0, 0.06, "640 seed clusters · 5,760 conditions", fontsize=6.8, color=GRAY)
    save(fig, "allocation_evidence")


def settlement():
    base = "results/geth_eip7825_atomic100_attackmatrix_final4_20260910/"
    evm = read(base + "summary.json")
    profiles = read(base + "aggregate_pipeline_benchmark.json")["profiles"]
    fig = plt.figure(figsize=(7.2, 4.8))
    ax = fig.add_axes([0.08, 0.57, 0.39, 0.31])
    bx = fig.add_axes([0.59, 0.57, 0.39, 0.31])
    for i, p in enumerate(profiles):
        for offset, key, color in [
            (-0.16, "proofGenerationMs", BLUE),
            (0.16, "aggregateGenerationMs", TEAL),
        ]:
            vals = np.array([r[key] for r in p["rows"]]) / 1000
            y = i + offset
            jitter = np.linspace(-0.065, 0.065, len(vals))
            ax.scatter(vals, y + jitter, s=7, color=color, alpha=0.30, edgecolors="none")
            q1, med, q3 = np.quantile(vals, [0.25, 0.5, 0.75])
            ax.plot([q1, q3], [y, y], lw=3, color=color, solid_capstyle="butt")
            ax.plot(med, y, "|", color=INK, ms=7, mew=1.1)
    ax.set(
        yticks=range(3),
        yticklabels=[f"{p['K']}/{p['q']}" for p in profiles],
        xlabel="Construction time (s)",
        ylabel="Committee K/q",
        ylim=(-0.5, 2.5),
    )
    axes(ax)
    title(ax, "a", "Proof and certificate construction")
    ax.legend(
        handles=[
            Line2D([], [], color=BLUE, lw=3, label="P1"),
            Line2D([], [], color=TEAL, lw=3, label="BLS"),
        ],
        frameon=False,
        ncol=2,
        loc="upper left",
        bbox_to_anchor=(0, -0.34),
        borderaxespad=0,
    )
    gas = np.array([r["gasUsed"] for r in evm["chunks"]] + [evm["finalizeGasUsed"]]) / 1e6
    cap = evm["eip7825Cap"] / 1e6
    bx.bar(range(1, 11), gas[:-1], width=0.62, color="#9AB7C9")
    bx.bar(11, gas[-1], width=0.62, color=TEAL)
    bx.axhline(cap, color=RED, ls=(0, (3, 3)), lw=1)
    bx.text(0.65, cap + 0.55, "Transaction cap: 16.78M", color=RED, fontsize=7)
    bx.annotate(
        f"{gas[-1]:.2f}M",
        (11, gas[-1]),
        xytext=(0, 5),
        textcoords="offset points",
        ha="center",
        fontsize=7,
    )
    bx.text(
        0.98,
        -0.38,
        f"{100 * (cap - gas[-1]) / cap:.1f}% finalizer headroom",
        transform=bx.transAxes,
        ha="right",
        fontsize=7,
        color=TEAL,
    )
    bx.set(
        xticks=[1, 5, 10, 11],
        xticklabels=["1", "5", "10", "Final"],
        ylim=(0, 20),
        xlabel="Transaction index",
        ylabel="Gas (million)",
    )
    axes(bx, "y")
    title(bx, "b", "Settlement within the gas cap")
    cx = fig.add_axes([0.08, 0.045, 0.90, 0.335])
    cx.set(xlim=(0, 1), ylim=(0, 1))
    cx.axis("off")
    title(cx, "c", "Terminal evidence → staged state → atomic credit")
    # Compact aligned lanes distinguish evidence cardinality from transaction count.
    for x, label in [(0.02, "Verified evidence"), (0.41, "On-chain staging"), (0.76, "Commit")]:
        cx.text(x, 0.87, label, fontsize=7, color=GRAY)
    for x in [0.375, 0.725]:
        cx.plot([x, x], [0.31, 0.92], color=GRID, lw=0.8)
    cx.text(0.02, 0.65, "100", fontsize=22, color=BLUE, weight="bold")
    cx.text(0.105, 0.69, "P1 proofs + CLOSED receipts", fontsize=7.5, weight="bold")
    cx.text(0.105, 0.54, "one record per actuator claim", fontsize=7, color=GRAY)
    # Each of the ten tiles is one real lock-only staging transaction.
    for i in range(10):
        cx.add_patch(
            Rectangle(
                (0.415 + i % 5 * 0.047, 0.62 - i // 5 * 0.115),
                0.036,
                0.08,
                fc="#D4E5EC",
                ec=BLUE,
                lw=0.65,
            )
        )
    cx.text(0.415, 0.36, "10 transactions · lock only", fontsize=7, color=BLUE)
    cx.text(0.77, 0.64, "1", fontsize=22, color=TEAL, weight="bold")
    cx.text(0.812, 0.68, "finalize", fontsize=8, weight="bold")
    cx.text(0.77, 0.48, "atomic credits: 100 / 50", fontsize=7, color=TEAL)
    for left, right, label in [(0.325, 0.407, "stage"), (0.655, 0.76, "finalize")]:
        cx.add_patch(
            FancyArrowPatch(
                (left, 0.61), (right, 0.61), arrowstyle="-|>", mutation_scale=9, color=GRAY, lw=1
            )
        )
        cx.text((left + right) / 2, 0.73, label, ha="center", fontsize=6.5, color=GRAY)
    cx.plot([0.02, 0.98], [0.27, 0.27], color=GRID, lw=0.8)
    cx.text(0.02, 0.14, "300/300 mutations rejected", color=RED, fontsize=7)
    cx.text(0.42, 0.14, "Replay + incomplete finalize revert", color=RED, fontsize=7)
    cx.text(
        0.02,
        -0.015,
        "Direct call: 217,779 B (rejected)    |    Final calldata: 35,300 B",
        fontsize=7,
        color=GRAY,
    )
    save(fig, "proof_settlement_evidence")


if __name__ == "__main__":
    h1()
    external()
    receipt()
    allocation()
    settlement()
    print("Rebuilt five vector PDF/SVG and PNG figure assets.")
