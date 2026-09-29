"""Build the final TDSC temporal figures from the audited V3 campaigns."""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from aidmesh.experiments.bow_factorial import FACTOR_LEVELS

BLUE = "#174a7e"
ORANGE = "#d97706"
RED = "#b23a48"
GREEN = "#2f855a"


def _save(figure: plt.Figure, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, bbox_inches="tight")
    plt.close(figure)


def _primary_forest(primary: Path, output: Path) -> None:
    forest = pd.read_csv(primary / "bow_primary_family_forest.csv")
    block = forest.loc[forest["policy"].eq("adatransfer")].copy()
    contexts = block.loc[block["level"].eq("context")].sort_values(
        ["family", "context"]
    )
    families = block.loc[block["level"].eq("family")].sort_values("family")
    display = pd.concat([contexts, families], ignore_index=True)
    display["label"] = np.where(
        display["level"].eq("family"),
        "Family: " + display["family"],
        display["context"],
    )
    positions = np.arange(len(display))[::-1]
    figure, axis = plt.subplots(figsize=(7.8, 4.5))
    for level, marker, color in (("context", "o", BLUE), ("family", "s", ORANGE)):
        mask = display["level"].eq(level)
        axis.errorbar(
            display.loc[mask, "mean_effect"],
            positions[mask.to_numpy()],
            xerr=np.vstack(
                (
                    display.loc[mask, "mean_effect"] - display.loc[mask, "ci_low"],
                    display.loc[mask, "ci_high"] - display.loc[mask, "mean_effect"],
                )
            ),
            fmt=marker,
            color=color,
            ecolor=color,
            capsize=3,
            label=level.capitalize(),
        )
    axis.axvline(0, color="black", linestyle="--", linewidth=0.9)
    axis.set_yticks(positions, display["label"])
    axis.set_xlabel("AdaTransfer - optimized static completion rate")
    axis.grid(axis="x", alpha=0.2)
    axis.legend(frameon=False, loc="lower right")
    figure.tight_layout()
    _save(figure, output / "heldout_temporal_frontier.pdf")


def _primary_mechanism(primary: Path, output: Path) -> None:
    runs = pd.read_csv(primary / "bow_primary_runs.csv")
    ada = runs.loc[runs["policy"].eq("adatransfer")].copy()
    context = (
        ada.groupby(["family", "registered_context"], as_index=False)
        .agg(
            rows=("policy", "size"),
            started=("transfers_started", "sum"),
            delivered=("transfers_delivered", "sum"),
            stranded=("transfers_stranded", "sum"),
            oracle_ratio=("online_to_oracle_ratio", "mean"),
        )
        .sort_values(["family", "registered_context"])
    )
    labels = context["registered_context"].tolist()
    positions = np.arange(len(context))
    started = 100 * context["started"] / context["rows"]
    delivered = 100 * context["delivered"] / context["rows"]
    stranded = 100 * context["stranded"] / context["rows"]
    figure, axes = plt.subplots(1, 2, figsize=(9.0, 3.8))
    axes[0].barh(positions, delivered, color=GREEN, label="Delivered")
    axes[0].barh(positions, stranded, left=delivered, color=RED, label="Stranded")
    axes[0].plot(started, positions, "|", color="black", markersize=12, label="Started")
    axes[0].set_yticks(positions, labels)
    axes[0].set_xlabel("Transfers per 100 policy cells")
    axes[0].legend(frameon=False, fontsize=8)
    axes[0].grid(axis="x", alpha=0.2)
    axes[1].barh(positions, context["oracle_ratio"], color=BLUE)
    axes[1].axvline(1.0, color="black", linestyle="--", linewidth=0.9)
    axes[1].set_yticks(positions, labels)
    axes[1].set_xlim(0, 1.02)
    axes[1].set_xlabel("Mean useful completion / offline oracle")
    axes[1].grid(axis="x", alpha=0.2)
    figure.tight_layout()
    _save(figure, output / "heldout_temporal_mechanism.pdf")


def _factor_modifiers(factorial: Path, output: Path) -> None:
    contrasts = pd.read_csv(factorial / "bow_factorial_contrasts.csv")
    data = contrasts.loc[contrasts["policy"].eq("adatransfer")].copy()
    if "family" not in data or "registered_context" not in data:
        runs = pd.read_csv(
            factorial / "bow_factorial_runs.csv",
            usecols=["context", "family", "registered_context"],
        ).drop_duplicates()
        data = data.merge(runs, on="context", how="left", validate="many_to_one")
    rows = []
    for factor, levels in FACTOR_LEVELS.items():
        other = [name for name in FACTOR_LEVELS if name != factor]
        keys = ["family", "registered_context", "seed", *other]
        paired = data.pivot_table(
            index=keys,
            columns=factor,
            values="completion_delta",
            aggfunc="mean",
        ).dropna()
        paired["effect"] = paired[levels[1]] - paired[levels[0]]
        clusters = paired.reset_index().groupby(
            ["family", "registered_context", "seed"], as_index=False
        )["effect"].mean()
        estimate = float(clusters["effect"].mean())
        se = float(clusters["effect"].std(ddof=1) / np.sqrt(len(clusters)))
        rows.append(
            {
                "factor": factor,
                "estimate": estimate,
                "low": estimate - 1.96 * se,
                "high": estimate + 1.96 * se,
            }
        )
    frame = pd.DataFrame(rows).sort_values("estimate")
    positions = np.arange(len(frame))
    figure, axis = plt.subplots(figsize=(7.8, 4.2))
    axis.errorbar(
        frame["estimate"],
        positions,
        xerr=np.vstack(
            (frame["estimate"] - frame["low"], frame["high"] - frame["estimate"])
        ),
        fmt="o",
        color=BLUE,
        ecolor=BLUE,
        capsize=3,
    )
    axis.axvline(0, color="black", linestyle="--", linewidth=0.9)
    axis.set_yticks(positions, [name.replace("_", " ") for name in frame["factor"]])
    axis.set_xlabel("Change in AdaTransfer - optimized static completion effect")
    axis.grid(axis="x", alpha=0.2)
    figure.tight_layout()
    _save(figure, output / "temporal_factorial_main_effects.pdf")


def _family_validation(factorial: Path, output: Path) -> None:
    forest = pd.read_csv(factorial / "bow_factorial_family_forest.csv")
    family = forest.loc[
        forest["policy"].eq("adatransfer") & forest["level"].eq("family")
    ].sort_values("family")
    lofo = pd.read_csv(factorial / "bow_factorial_lofo_summary.csv").sort_values("family")
    positions = np.arange(len(family))
    figure, axes = plt.subplots(1, 2, figsize=(8.6, 3.5))
    axes[0].errorbar(
        family["mean_effect"],
        positions,
        xerr=np.vstack(
            (
                family["mean_effect"] - family["ci_low"],
                family["ci_high"] - family["mean_effect"],
            )
        ),
        fmt="o",
        color=BLUE,
        capsize=3,
    )
    axes[0].axvline(0, color="black", linestyle="--", linewidth=0.9)
    axes[0].set_yticks(positions, family["family"])
    axes[0].set_xlabel("Family mean effect")
    axes[0].grid(axis="x", alpha=0.2)
    width = 0.36
    axes[1].barh(positions - width / 2, lofo["sign_accuracy"], width, label="Sign accuracy")
    axes[1].barh(
        positions + width / 2,
        lofo["prediction_interval_coverage"],
        width,
        label="PI coverage",
        color=ORANGE,
    )
    axes[1].set_yticks(positions, lofo["family"])
    axes[1].set_xlim(0, 1)
    axes[1].set_xlabel("Leave-one-family-out score")
    axes[1].legend(frameon=False, fontsize=8)
    axes[1].grid(axis="x", alpha=0.2)
    figure.tight_layout()
    _save(figure, output / "independent_trace_family_campaign.pdf")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--primary", type=Path, required=True)
    parser.add_argument("--factorial", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    _primary_forest(args.primary, args.output)
    _primary_mechanism(args.primary, args.output)
    _factor_modifiers(args.factorial, args.output)
    _family_validation(args.factorial, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
