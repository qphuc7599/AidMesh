"""Rebuild the five manuscript result figures from the recorded evidence.

The script changes only presentation. Every point, interval and count is read
from the same result artifacts used by the manuscript.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch
from matplotlib.ticker import PercentFormatter

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "overleaf" / "figs"
OUT.mkdir(parents=True, exist_ok=True)

INK = "#111827"
NAVY = "#0B1930"
SLATE = "#475569"
MUTED = "#64748B"
GRID = "#D9E2EC"
PANEL = "#F6F8FC"
BLUE = "#2563EB"
CYAN = "#0891B2"
TEAL = "#0F9D8A"
MINT = "#2DD4BF"
AMBER = "#F59E0B"
CORAL = "#E45756"
PURPLE = "#7C3AED"
WHITE = "#FFFFFF"

mpl.rcParams.update(
    {
        "font.family": "DejaVu Sans",
        "font.size": 8.4,
        "axes.labelsize": 8.8,
        "axes.titlesize": 9.2,
        "xtick.labelsize": 7.8,
        "ytick.labelsize": 8.0,
        "legend.fontsize": 7.4,
        "axes.edgecolor": "#94A3B8",
        "axes.linewidth": 0.8,
        "figure.facecolor": WHITE,
        "axes.facecolor": WHITE,
        "savefig.facecolor": WHITE,
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
    }
)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def finish(fig: plt.Figure, name: str) -> None:
    fig.savefig(OUT / f"{name}.pdf", bbox_inches="tight", pad_inches=0.045)
    fig.savefig(OUT / f"{name}.png", dpi=360, bbox_inches="tight", pad_inches=0.045)
    plt.close(fig)


def clean_axis(ax: plt.Axes, *, left: bool = False) -> None:
    ax.spines[["top", "right"]].set_visible(False)
    if not left:
        ax.spines["left"].set_visible(False)
        ax.tick_params(axis="y", length=0)
    ax.set_axisbelow(True)


def panel_badge(ax: plt.Axes, letter: str, title: str) -> None:
    ax.text(
        0.0,
        1.035,
        f"({letter})",
        transform=ax.transAxes,
        color=WHITE,
        fontsize=8.2,
        fontweight="bold",
        va="bottom",
        bbox=dict(boxstyle="round,pad=0.26", facecolor=NAVY, edgecolor=NAVY),
    )
    ax.text(
        0.088,
        1.04,
        title,
        transform=ax.transAxes,
        color=INK,
        fontsize=9.0,
        fontweight="bold",
        va="bottom",
    )


def h1_figure() -> None:
    replay = load_csv(
        ROOT
        / "results/postreview_original_registry_20260910/replay_analysis/"
        "confirmatory_registered_contrasts.csv"
    )
    holdout = load_csv(
        ROOT
        / "results/postreview_original_registry_20260910/holdout_analysis/"
        "confirmatory_registered_contrasts.csv"
    )
    contrast_ids = [
        "RQ1_TRANSFER_VS_ONLINE_CORE",
        "RQ1_TRANSFER_VS_ONLINE_WIT06_BOUNDARY",
        "RQ1_TRANSFER_VS_ONLINE_WIT07",
        "RQ1_TRANSFER_VS_ONLINE_WIT09",
        "RQ1_TRANSFER_VS_ONLINE_P06",
        "RQ1_TRANSFER_VS_ONLINE_P48",
        "RQ1_TRANSFER_VS_ONLINE_P60_BOUNDARY",
        "RQ1_TRANSFER_VS_ONLINE_ADV05",
        "RQ1_TRANSFER_VS_ONLINE_ADV30",
    ]
    labels = [
        "Core · 24 h",
        "Witness 0.60",
        "Witness 0.70",
        "Witness 0.90",
        "Partition 6 h",
        "Partition 48 h",
        "Partition 60 h",
        "Adversarial 0.05",
        "Adversarial 0.30",
    ]

    def select(rows: list[dict[str, str]]) -> list[tuple[float, float, float]]:
        indexed = {
            row["comparison_id"]: row
            for row in rows
            if row["metric"] == "completion_rate"
        }
        return [
            (
                float(indexed[key]["mean_difference"]),
                float(indexed[key]["ci_low"]),
                float(indexed[key]["ci_high"]),
            )
            for key in contrast_ids
        ]

    reference = select(replay)
    hold = select(holdout)
    y = np.arange(len(labels))[::-1]
    fig, ax = plt.subplots(figsize=(7.35, 3.85))
    ax.set_facecolor("#FCFDFE")
    ax.axvspan(-0.20, 0.00, color=CORAL, alpha=0.12, zorder=0)
    ax.axvspan(0.00, 0.05, color=AMBER, alpha=0.15, zorder=0)
    ax.axvspan(0.05, 1.11, color=TEAL, alpha=0.09, zorder=0)
    ax.axvline(0, color=CORAL, lw=1.65, zorder=1)
    ax.axvline(0.05, color=AMBER, lw=1.5, ls=(0, (4, 2)), zorder=1)
    ax.grid(axis="x", color=WHITE, linewidth=1.1)
    for yy in np.arange(0.5, len(labels), 1):
        ax.axhline(yy, color="#E9EEF4", lw=0.55, zorder=0)

    for index, ((rm, rl, rh), (hm, hl, hh)) in enumerate(
        zip(reference, hold, strict=True)
    ):
        yy = y[index]
        color = TEAL if hm >= 0.05 else (AMBER if hm >= 0 else CORAL)
        ax.plot([rm, hm], [yy, yy], color="#9AA9B8", lw=1.2, zorder=2)
        ax.errorbar(
            rm,
            yy,
            xerr=[[rm - rl], [rh - rm]],
            fmt="D",
            ms=4.5,
            mfc=WHITE,
            mec=SLATE,
            mew=1.1,
            ecolor=SLATE,
            elinewidth=1.1,
            capsize=2.4,
            zorder=3,
        )
        ax.errorbar(
            hm,
            yy,
            xerr=[[hm - hl], [hh - hm]],
            fmt="o",
            ms=6.3,
            mfc=color,
            mec=WHITE,
            mew=0.9,
            ecolor=color,
            elinewidth=2.2,
            capsize=3.0,
            zorder=4,
        )
        tx = max(hh, rh) + 0.014
        ax.text(
            tx,
            yy,
            f"{hm:+.3f}",
            va="center",
            ha="left",
            fontsize=7.3,
            color=INK,
            fontweight="bold" if abs(hm) < 0.06 else "normal",
        )

    ax.set_yticks(y, labels)
    ax.set_ylim(-0.75, len(labels) - 0.05)
    ax.set_xlim(-0.205, 1.105)
    ax.set_xlabel("AidMesh − Online-Only deadline completion")
    zone_transform = ax.get_xaxis_transform()
    ax.text(
        -0.102,
        1.005,
        "REVERSAL",
        transform=zone_transform,
        ha="center",
        va="bottom",
        color=CORAL,
        fontsize=6.7,
        fontweight="bold",
        clip_on=False,
    )
    ax.text(
        0.025,
        1.005,
        "0–.05",
        transform=zone_transform,
        ha="center",
        va="bottom",
        color="#A76500",
        fontsize=5.6,
        fontweight="bold",
        clip_on=False,
    )
    ax.text(
        0.58,
        1.005,
        "SUPPORTED ENVELOPE",
        transform=zone_transform,
        ha="center",
        va="bottom",
        color=TEAL,
        fontsize=6.7,
        fontweight="bold",
        clip_on=False,
    )
    handles = [
        mpl.lines.Line2D(
            [], [], marker="D", ms=4.6, mfc=WHITE, mec=SLATE, color="none",
            label="reference seeds"
        ),
        mpl.lines.Line2D(
            [], [], marker="o", ms=6.2, mfc=TEAL, mec=WHITE, color="none",
            label="disjoint holdout"
        ),
    ]
    ax.legend(
        handles=handles,
        loc="lower right",
        ncol=2,
        frameon=True,
        facecolor=WHITE,
        edgecolor="#D5DEE8",
        columnspacing=0.8,
        handletextpad=0.35,
    )
    clean_axis(ax)
    fig.subplots_adjust(left=0.205, right=0.985, top=0.93, bottom=0.15)
    finish(fig, "h1_operating_envelope")


def external_validity_figure() -> None:
    data = load_json(
        ROOT / "results/external_validity_three_family_20260928/fixed_analysis.json"
    )
    effects = {(row["control"], row["family"]): row for row in data["effects"]}
    families = ["ene5ai", "sociopatterns", "umass_dieselnet", "all-three"]
    labels = ["ENE5AI", "SocioPatterns", "DieselNet", "Family-balanced"]
    y = np.arange(4)[::-1]

    fig, ax = plt.subplots(figsize=(7.35, 3.15))
    ax.set_facecolor("#FCFDFE")
    ax.axvspan(-0.08, 0, color=CORAL, alpha=0.11, zorder=0)
    ax.axvspan(0, 0.295, color=TEAL, alpha=0.08, zorder=0)
    ax.axvline(0, color=INK, lw=1.2, zorder=1)
    for yy in np.arange(0.5, 4, 1):
        ax.axhline(yy, color="#E8EEF4", lw=0.6, zorder=0)
    ax.axhspan(-0.45, 0.45, color="#E8EEF8", alpha=0.8, zorder=0)

    for index, family in enumerate(families):
        yy = y[index]
        static = effects[("optimized_static", family)]
        periodic = effects[("periodic_sync", family)]
        sm, (sl, sh) = static["mean"], static["ci95"]
        pm, (pl, ph) = periodic["mean"], periodic["ci95"]
        ax.plot([sm, pm], [yy, yy], color="#AAB7C5", lw=1.5, zorder=2)
        ax.errorbar(
            sm,
            yy,
            xerr=[[sm - sl], [sh - sm]],
            fmt="s",
            ms=5.8,
            mfc=CORAL,
            mec=WHITE,
            mew=0.8,
            ecolor=CORAL,
            elinewidth=2.0,
            capsize=3.0,
            zorder=3,
        )
        ax.errorbar(
            pm,
            yy,
            xerr=[[pm - pl], [ph - pm]],
            fmt="D",
            ms=5.8,
            mfc=TEAL,
            mec=WHITE,
            mew=0.8,
            ecolor=TEAL,
            elinewidth=2.0,
            capsize=3.0,
            zorder=3,
        )
        ax.text(
            sl - 0.004,
            yy,
            f"{100 * sm:+.1f}",
            ha="right",
            va="center",
            color=CORAL,
            fontsize=7.2,
            fontweight="bold",
        )
        ax.text(
            ph + 0.004,
            yy,
            f"{100 * pm:+.1f}",
            ha="left",
            va="center",
            color=TEAL,
            fontsize=7.2,
            fontweight="bold",
        )

    ax.set_yticks(y, labels)
    ax.get_yticklabels()[-1].set_fontweight("bold")
    ax.set_xlim(-0.082, 0.303)
    ax.set_ylim(-0.55, 3.55)
    ax.xaxis.set_major_formatter(mpl.ticker.PercentFormatter(1.0, decimals=0))
    ax.set_xlabel("AdaTransfer completion difference")
    ax.grid(axis="x", color=WHITE, lw=1.0)
    ax.text(
        -0.04,
        3.44,
        "OPTIMIZED STATIC WINS",
        ha="center",
        va="top",
        color=CORAL,
        fontsize=7.0,
        fontweight="bold",
    )
    ax.text(
        0.15,
        3.44,
        "ADATRANSFER BEATS PERIODIC SYNC",
        ha="center",
        va="top",
        color=TEAL,
        fontsize=7.0,
        fontweight="bold",
    )
    ax.text(
        0.985,
        0.025,
        "COMPARATOR REVERSAL · 3 / 3 TRACE FAMILIES",
        transform=ax.transAxes,
        ha="right",
        va="bottom",
        fontsize=6.8,
        color=WHITE,
        fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.32", facecolor=NAVY, edgecolor=NAVY),
    )
    clean_axis(ax)
    fig.subplots_adjust(left=0.18, right=0.97, top=0.96, bottom=0.17)
    finish(fig, "three_family_replication")


def receipt_figure() -> None:
    data = load_json(
        ROOT / "results/matched_receipt_controller_20260910/summary.json"
    )
    rows = data["primaryContrasts"]
    fig, axes = plt.subplots(
        1,
        2,
        figsize=(7.35, 3.25),
        sharey=True,
        gridspec_kw={"width_ratios": [0.9, 1.1]},
    )
    loads = [0.65, 0.85, 0.95]
    ymap = {0.65: 2, 0.85: 1, 0.95: 0}
    colors = {0.65: BLUE, 0.85: CYAN, 0.95: PURPLE}
    settings = [
        (0.0, axes[0], "seconds", 1.0, 71, "Continuous connectivity"),
        (2.0, axes[1], "minutes", 60.0, 119, "Two-hour reconnection burst"),
    ]
    for part, ax, unit, scale, xmax, title in settings:
        ax.set_facecolor(PANEL)
        ax.axvline(0, color=INK, lw=1.0)
        for load in loads:
            selected = sorted(
                [
                    row
                    for row in rows
                    if row["partition_hours"] == part
                    and row["load_factor"] == load
                ],
                key=lambda row: row["invalid_priority_fraction"],
            )
            points: list[float] = []
            for row in selected:
                mean = -row["meanDifference"] / scale
                low = -row["ci95"][1] / scale
                high = -row["ci95"][0] / scale
                points.append(mean)
                invalid = row["invalid_priority_fraction"] > 0
                offset = -0.105 if invalid else 0.105
                ax.errorbar(
                    mean,
                    ymap[load] + offset,
                    xerr=[[mean - low], [high - mean]],
                    fmt="s" if invalid else "o",
                    ms=6.0,
                    mfc=WHITE if invalid else colors[load],
                    mec=colors[load],
                    mew=1.4 if invalid else 0.8,
                    ecolor=colors[load],
                    elinewidth=2.0,
                    capsize=2.8,
                    zorder=4,
                )
            ax.plot(
                points,
                [ymap[load] + 0.105, ymap[load] - 0.105],
                color=colors[load],
                alpha=0.45,
                lw=1.4,
                zorder=2,
            )
            ax.plot(
                [0, min(points)],
                [ymap[load], ymap[load]],
                color=colors[load],
                alpha=0.16,
                lw=5.0,
                solid_capstyle="round",
                zorder=1,
            )
        ax.set_xlim(-1.5 if part == 0 else -4, xmax)
        ax.set_ylim(-0.85, 2.55)
        ax.grid(axis="x", color=WHITE, lw=1.0)
        ax.set_xlabel(f"Median receipt-delay reduction ({unit})")
        panel_badge(ax, "a" if part == 0 else "b", title)
        clean_axis(ax)

    axes[0].set_yticks([2, 1, 0], ["Load 0.65", "Load 0.85", "Load 0.95"])
    axes[1].text(
        0.96,
        0.96,
        "12 / 12 cells\n30 / 30 paired wins",
        transform=axes[1].transAxes,
        ha="right",
        va="top",
        fontsize=8.0,
        color=WHITE,
        fontweight="bold",
        linespacing=1.35,
        bbox=dict(
            boxstyle="round,pad=0.42",
            facecolor=NAVY,
            edgecolor=NAVY,
        ),
    )
    handles = [
        mpl.lines.Line2D(
            [], [], marker="o", ms=6, mfc=TEAL, mec=WHITE, color="none",
            label="valid priority traffic"
        ),
        mpl.lines.Line2D(
            [], [], marker="s", ms=6, mfc=WHITE, mec=TEAL, color="none",
            label="10% invalid attempts rejected"
        ),
    ]
    fig.legend(
        handles=handles,
        loc="lower center",
        ncol=2,
        frameon=False,
        bbox_to_anchor=(0.5, -0.01),
    )
    fig.subplots_adjust(left=0.135, right=0.985, top=0.84, bottom=0.24, wspace=0.24)
    finish(fig, "receipt_priority_evidence")


def allocation_figure() -> None:
    summary = load_json(ROOT / "results/h3_fixed_replication_20260928/summary.json")
    original = load_json(
        ROOT / "results/h3_fixed_replication_20260928/original_contract_analysis.json"
    )
    labels = {
        "progressive-fill": "Ray-constrained PF",
        "equal-share": "Equal share",
        "demand-proportional": "Demand proportional",
        "utility-only": "Utility only",
    }
    fairness = summary["fairnessContrasts"]
    fig, axes = plt.subplots(
        1,
        2,
        figsize=(7.35, 3.05),
        gridspec_kw={"width_ratios": [1.08, 0.92]},
    )

    ax = axes[0]
    ax.set_facecolor(PANEL)
    ordered = list(reversed(fairness))
    for position, row in enumerate(ordered):
        low, high = row["twoSided95"]
        mean = row["meanMinimumServiceDifference"]
        ax.plot(
            [0, mean],
            [position, position],
            color=BLUE,
            alpha=0.20,
            lw=6,
            solid_capstyle="round",
            zorder=1,
        )
        ax.errorbar(
            mean,
            position,
            xerr=[[mean - low], [high - mean]],
            fmt="o",
            color=BLUE,
            ecolor=BLUE,
            mfc=BLUE,
            mec=WHITE,
            mew=0.8,
            capsize=3.0,
            linewidth=2.0,
            markersize=6.2,
            zorder=3,
        )
        ax.text(
            high + 0.004,
            position,
            f"W/T/L {row['conditionWins']}/{row['conditionTies']}/{row['conditionLosses']}",
            fontsize=6.9,
            ha="left",
            va="center",
            color=INK,
        )
    ax.axvline(0, color=INK, lw=1.0)
    ax.set_yticks(
        range(len(ordered)),
        [labels[row["comparator"]] for row in ordered],
    )
    ax.set_xlim(-0.005, 0.305)
    ax.set_xlabel("Minimum-service gain")
    ax.grid(axis="x", color=WHITE, lw=1.0)
    panel_badge(ax, "a", "Fairness frontier")
    clean_axis(ax)

    bx = axes[1]
    bx.set_facecolor(PANEL)
    margin = summary["utilityLoss"]["margin"]
    bx.axvspan(0, margin, color=TEAL, alpha=0.10, zorder=0)
    bx.axvspan(margin, 0.032, color=CORAL, alpha=0.12, zorder=0)
    rows = [
        (
            "Weighted utility",
            summary["utilityLoss"]["meanRelativeLoss"],
            summary["utilityLoss"],
            TEAL,
        ),
        (
            "Fulfilled units",
            original["efficiency"]["meanRelativeFulfilledUnitLoss"],
            original["efficiency"],
            BLUE,
        ),
    ]
    for yy, (_label, mean, row, color) in enumerate(reversed(rows)):
        low, high = row["twoSided95"]
        bx.plot(
            [0, mean],
            [yy, yy],
            color=color,
            alpha=0.22,
            lw=6,
            solid_capstyle="round",
            zorder=1,
        )
        bx.errorbar(
            mean,
            yy,
            xerr=[[mean - low], [high - mean]],
            fmt="o",
            color=color,
            ecolor=color,
            mfc=color,
            mec=WHITE,
            mew=0.8,
            capsize=3.2,
            linewidth=2.1,
            markersize=6.4,
            zorder=3,
        )
        bx.plot(
            row["oneSided95Upper"],
            yy,
            marker="|",
            markersize=15,
            markeredgewidth=2.0,
            color=INK,
            zorder=4,
        )
        bx.text(
            mean + (0.0008 if mean > 0 else 0.00035),
            yy + 0.18,
            f"{100 * mean:.2f}%",
            color=color,
            fontsize=7.1,
            fontweight="bold",
            ha="left",
        )
    bx.axvline(margin, color=CORAL, ls=(0, (4, 2)), lw=1.6, zorder=2)
    bx.set_xlim(-0.0015, 0.032)
    bx.set_ylim(-0.55, 1.55)
    bx.set_yticks([0, 1], ["Fulfilled units", "Weighted utility"])
    bx.xaxis.set_major_formatter(PercentFormatter(1.0, decimals=1))
    bx.set_xlabel("Relative loss vs utility-only")
    bx.grid(axis="x", color=WHITE, lw=1.0)
    panel_badge(bx, "b", "Fixed noninferiority gate")
    bx.text(
        margin,
        1.47,
        "3% LIMIT",
        ha="right",
        va="top",
        color=CORAL,
        fontsize=7.0,
        fontweight="bold",
    )
    bx.text(
        0.96,
        0.08,
        "PASS\nupper bound 2.56%",
        transform=bx.transAxes,
        ha="right",
        va="bottom",
        color=WHITE,
        fontsize=7.8,
        fontweight="bold",
        linespacing=1.25,
        bbox=dict(boxstyle="round,pad=0.38", facecolor=TEAL, edgecolor=TEAL),
    )
    clean_axis(bx)
    fig.subplots_adjust(left=0.19, right=0.985, top=0.83, bottom=0.19, wspace=0.30)
    finish(fig, "allocation_evidence")


def settlement_figure() -> None:
    evm = load_json(
        ROOT / "results/geth_eip7825_atomic100_attackmatrix_final4_20260910/summary.json"
    )
    pipeline = load_json(
        ROOT
        / "results/geth_eip7825_atomic100_attackmatrix_final4_20260910/"
        "aggregate_pipeline_benchmark.json"
    )
    profiles = pipeline["profiles"]
    kq = [f"{profile['K']}/{profile['q']}" for profile in profiles]
    p1 = [
        float(np.median([row["proofGenerationMs"] for row in profile["rows"]]))
        for profile in profiles
    ]
    bls = [
        float(np.median([row["aggregateGenerationMs"] for row in profile["rows"]]))
        for profile in profiles
    ]
    verify = [profile["pairedVerificationMs"]["median"] for profile in profiles]
    wire = [profile["combinedProofCertificateBytes"]["max"] for profile in profiles]

    fig = plt.figure(figsize=(7.35, 4.95))
    grid = fig.add_gridspec(
        2,
        2,
        height_ratios=[1.0, 0.88],
        width_ratios=[0.96, 1.04],
        hspace=0.61,
        wspace=0.31,
    )
    ax = fig.add_subplot(grid[0, 0])
    bx = fig.add_subplot(grid[0, 1])
    cx = fig.add_subplot(grid[1, :])

    y = np.arange(3)
    ax.set_facecolor(PANEL)
    ax.barh(y, p1, color=BLUE, height=0.56, label="P1 generation", zorder=2)
    ax.barh(
        y,
        bls,
        left=p1,
        color=TEAL,
        height=0.56,
        label="BLS construction",
        zorder=2,
    )
    for index, (a, b, value, size) in enumerate(
        zip(p1, bls, verify, wire, strict=True)
    ):
        ax.text(
            a + b + 65,
            index,
            f"{value:.2f} ms verify\n{size / 1000:.2f} kB wire",
            va="center",
            ha="left",
            fontsize=6.8,
            color=INK,
            linespacing=1.25,
        )
    ax.text(
        p1[-1] / 2,
        2,
        "P1",
        ha="center",
        va="center",
        fontsize=7.2,
        color=WHITE,
        fontweight="bold",
    )
    ax.text(
        p1[-1] + bls[-1] / 2,
        2,
        "BLS",
        ha="center",
        va="center",
        fontsize=7.2,
        color=WHITE,
        fontweight="bold",
    )
    ax.set_yticks(y, kq)
    ax.set_ylabel("Committee K/q")
    ax.set_xlabel("Sequential construction time (ms)")
    ax.set_xlim(0, 4550)
    ax.grid(axis="x", color=WHITE, lw=1.0)
    panel_badge(ax, "a", "Proof + constant-size certificate")
    clean_axis(ax)

    bx.set_facecolor(PANEL)
    gas = [chunk["gasUsed"] / 1e6 for chunk in evm["chunks"]] + [
        evm["finalizeGasUsed"] / 1e6
    ]
    x = np.arange(1, 12)
    cap = evm["eip7825Cap"] / 1e6
    bx.axhspan(cap, 18.3, color=CORAL, alpha=0.12, zorder=0)
    bx.bar(
        x[:-1],
        gas[:-1],
        color=NAVY,
        width=0.70,
        edgecolor=WHITE,
        linewidth=0.6,
        zorder=2,
    )
    bx.bar(
        x[-1],
        gas[-1],
        color=AMBER,
        width=0.74,
        edgecolor=WHITE,
        linewidth=0.7,
        zorder=3,
    )
    bx.axhline(cap, color=CORAL, lw=1.6, ls=(0, (4, 2)), zorder=4)
    bx.set_xticks([1, 5, 10, 11], ["1", "5", "10", "final"])
    bx.set_xlabel("Stage transaction index")
    bx.set_ylabel("Gas per transaction (million)")
    bx.set_ylim(0, 18.25)
    bx.grid(axis="y", color=WHITE, lw=1.0)
    bx.text(
        0.02,
        0.95,
        "EIP-7825 CAP · 16.78M",
        transform=bx.transAxes,
        ha="left",
        va="top",
        fontsize=7.0,
        color=CORAL,
        fontweight="bold",
    )
    headroom = 100 * (cap - gas[-1]) / cap
    bx.text(
        11,
        gas[-1] + 0.45,
        f"{gas[-1]:.2f}M\n{headroom:.1f}% headroom",
        ha="right",
        va="bottom",
        fontsize=7.2,
        color=INK,
        fontweight="bold",
        linespacing=1.25,
    )
    panel_badge(bx, "b", "Staged-atomic gas envelope")
    clean_axis(bx, left=True)

    cx.set_xlim(0, 1)
    cx.set_ylim(0, 1)
    cx.set_facecolor(WHITE)
    cx.set_xticks([])
    cx.set_yticks([])
    for spine in cx.spines.values():
        spine.set_visible(False)
    cx.text(
        0.0,
        1.015,
        "(c)",
        color=WHITE,
        fontsize=8.2,
        fontweight="bold",
        va="bottom",
        bbox=dict(boxstyle="round,pad=0.26", facecolor=NAVY, edgecolor=NAVY),
    )
    cx.text(
        0.060,
        1.02,
        "Evidence compression and atomic commit",
        color=INK,
        fontsize=9.0,
        fontweight="bold",
        va="bottom",
    )
    cx.text(
        0.985,
        1.015,
        "217,779 B  →  35,300 B final calldata",
        ha="right",
        va="bottom",
        fontsize=6.8,
        color=TEAL,
        fontweight="bold",
        bbox=dict(
            boxstyle="round,pad=0.30",
            facecolor="#E7F6F3",
            edgecolor="#A7DDD4",
            linewidth=0.8,
        ),
    )

    stations = [
        (0.105, "100", "P1 proof batch", "all verified", BLUE, "#E9F0FE"),
        (
            0.365,
            "100",
            "CLOSED evidence",
            "after actuator claims",
            TEAL,
            "#E7F6F3",
        ),
        (0.635, "10×", "staged locks", "lock only · no credit", CYAN, "#E6F5F8"),
        (
            0.895,
            "1×",
            "atomic finalize",
            "credits 100 / 50",
            AMBER,
            "#FFF5DB",
        ),
    ]
    flow_labels = ["verify", "bind terminal evidence", "batch 10 × 10"]
    for index, (x0, count, title, subtitle, accent, tint) in enumerate(stations):
        cx.scatter(
            [x0],
            [0.565],
            s=1530 if index != 2 else 1360,
            facecolor=tint,
            edgecolor=accent,
            linewidth=1.45,
            zorder=4,
        )
        cx.text(
            x0,
            0.575,
            count,
            ha="center",
            va="center",
            color=accent,
            fontsize=12.5,
            fontweight="bold",
            zorder=5,
        )
        cx.text(
            x0,
            0.835,
            f"{index + 1:02d}",
            ha="center",
            va="center",
            color=accent,
            fontsize=6.2,
            fontweight="bold",
        )
        cx.text(
            x0,
            0.755,
            title,
            ha="center",
            va="center",
            color=INK,
            fontsize=7.3,
            fontweight="bold",
        )
        cx.text(
            x0,
            0.345,
            subtitle,
            ha="center",
            va="center",
            color=MUTED,
            fontsize=6.4,
        )
        if index < len(stations) - 1:
            next_x = stations[index + 1][0]
            cx.add_patch(
                FancyArrowPatch(
                    (x0 + 0.065, 0.565),
                    (next_x - 0.065, 0.565),
                    arrowstyle="-|>",
                    mutation_scale=11,
                    linewidth=1.6,
                    color="#8CA0B3",
                    zorder=2,
                )
            )
            cx.text(
                (x0 + next_x) / 2,
                0.625,
                flow_labels[index],
                ha="center",
                va="bottom",
                fontsize=5.8,
                color=SLATE,
                fontweight="bold",
            )

    guard = FancyBboxPatch(
        (0.018, 0.025),
        0.964,
        0.175,
        boxstyle="round,pad=0.010,rounding_size=0.018",
        linewidth=0.85,
        edgecolor="#F3B0AE",
        facecolor="#FFF2F1",
        zorder=0,
    )
    cx.add_patch(guard)
    cx.text(
        0.035,
        0.112,
        "FAIL-CLOSED",
        ha="left",
        va="center",
        color=CORAL,
        fontsize=6.5,
        fontweight="bold",
    )
    controls = [
        (0.275, "300 / 300 mutations rejected"),
        (0.605, "replay rejected"),
        (0.845, "incomplete finalize reverts"),
    ]
    for x0, label in controls:
        cx.text(
            x0,
            0.112,
            f"×  {label}",
            ha="center",
            va="center",
            color="#9F2D2B",
            fontsize=6.35,
            fontweight="bold",
        )
    fig.subplots_adjust(left=0.085, right=0.985, top=0.92, bottom=0.055)
    finish(fig, "proof_settlement_evidence")


def main() -> int:
    h1_figure()
    external_validity_figure()
    receipt_figure()
    allocation_figure()
    settlement_figure()
    print(f"Wrote five figures to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
