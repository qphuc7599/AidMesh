"""Frozen-style aggregation and compact paper-asset generation for pilot runs."""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
from scipy import stats
from statsmodels.stats.multitest import multipletests

SUMMARY_COLUMNS = (
    "completion_rate",
    "non_home_completion_rate",
    "duplicate_exposure",
    "physical_duplicate_exposure",
    "exposure_cap_ratio",
    "rmst_hours",
    "bytes_per_settlement",
)


def load_run_summaries(path: Path) -> pd.DataFrame:
    files = sorted(path.rglob("summary.json"))
    if not files:
        raise ValueError(f"no summary.json files below {path}")
    rows = [json.loads(file.read_text(encoding="utf-8")) for file in files]
    frame = pd.DataFrame(rows)
    if frame.duplicated(["scenario_id", "method", "seed"]).any():
        raise ValueError("duplicate run summaries detected")
    if not frame["risk_rights_conserved"].all():
        raise ValueError("risk-right conservation violation in run summaries")
    if (frame["conflicting_quorum_certificates"].fillna(0) > 0).any():
        raise ValueError("safe-setting quorum conflict in run summaries")
    return frame.sort_values(["method", "seed"]).reset_index(drop=True)


def aggregate(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []
    for method, group in frame.groupby("method", sort=True):
        row: dict[str, object] = {"method": method, "seeds": int(group["seed"].nunique())}
        for column in SUMMARY_COLUMNS:
            values = pd.to_numeric(group[column], errors="coerce").dropna()
            row[f"{column}_mean"] = float(values.mean()) if len(values) else None
            row[f"{column}_sd"] = float(values.std(ddof=1)) if len(values) > 1 else 0.0
            row[f"{column}_min"] = float(values.min()) if len(values) else None
            row[f"{column}_max"] = float(values.max()) if len(values) else None
        rows.append(row)
    return pd.DataFrame(rows)


def paired_differences(
    frame: pd.DataFrame,
    *,
    treatment: str = "aidmesh_transferable_rights",
    control: str = "online_only",
) -> pd.DataFrame:
    indexed = frame.set_index(["scenario_id", "seed", "method"])
    rows: list[dict] = []
    pairs = sorted({(s, int(seed)) for s, seed, _ in indexed.index})
    for scenario_id, seed in pairs:
        try:
            treated = indexed.loc[(scenario_id, seed, treatment)]
            baseline = indexed.loc[(scenario_id, seed, control)]
        except KeyError:
            continue
        rows.append(
            {
                "scenario_id": scenario_id,
                "seed": seed,
                "treatment": treatment,
                "control": control,
                "completion_difference": float(
                    treated["completion_rate"] - baseline["completion_rate"]
                ),
                "rmst_difference_hours": float(treated["rmst_hours"] - baseline["rmst_hours"]),
            }
        )
    return pd.DataFrame(rows)


def completion_contrasts(frame: pd.DataFrame, control: str = "online_only") -> pd.DataFrame:
    methods = sorted(set(frame["method"]) - {control})
    rows: list[dict] = []
    for method in methods:
        pivot = frame[frame["method"].isin([control, method])].pivot(
            index=["scenario_id", "seed"], columns="method", values="completion_rate"
        )
        pivot = pivot.dropna()
        differences = pivot[method] - pivot[control]
        count = len(differences)
        mean = float(differences.mean())
        standard_deviation = float(differences.std(ddof=1)) if count > 1 else 0.0
        standard_error = standard_deviation / count**0.5 if count else float("nan")
        critical = float(stats.t.ppf(0.975, count - 1)) if count > 1 else 0.0
        test = stats.ttest_rel(pivot[method], pivot[control]) if count > 1 else None
        rows.append(
            {
                "method": method,
                "control": control,
                "pairs": count,
                "mean_difference": mean,
                "ci95_low": mean - critical * standard_error,
                "ci95_high": mean + critical * standard_error,
                "paired_dz": mean / standard_deviation if standard_deviation else None,
                "p_value": float(test.pvalue) if test is not None else None,
            }
        )
    p_values = [row["p_value"] for row in rows]
    if rows and all(value is not None for value in p_values):
        adjusted = multipletests(p_values, alpha=0.05, method="holm")[1]
        for row, value in zip(rows, adjusted, strict=True):
            row["holm_p_value"] = float(value)
    return pd.DataFrame(rows)


def write_assets(frame: pd.DataFrame, output: Path) -> dict[str, Path]:
    output.mkdir(parents=True, exist_ok=True)
    aggregated = aggregate(frame)
    aggregate_path = output / "pilot_method_summary.csv"
    aggregated.to_csv(aggregate_path, index=False)
    pair_path = output / "pilot_paired_differences.csv"
    paired_differences(frame).to_csv(pair_path, index=False)
    contrast_path = output / "pilot_completion_contrasts.csv"
    completion_contrasts(frame).to_csv(contrast_path, index=False)

    order = list(aggregated["method"])
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2), constrained_layout=True)
    fig.patch.set_facecolor("white")
    for axis in axes:
        axis.set_facecolor("white")
    axes[0].bar(order, aggregated["completion_rate_mean"], color="#2563eb")
    axes[0].set_ylabel("Mean completion rate")
    axes[0].set_ylim(0, 1.02)
    axes[0].tick_params(axis="x", rotation=30)
    axes[0].grid(axis="y", alpha=0.25)
    axes[1].bar(order, aggregated["exposure_cap_ratio_mean"], color="#dc2626")
    axes[1].axhline(1, color="black", linestyle="--", linewidth=1, label="registered cap")
    axes[1].set_ylabel("Mean duplicate exposure / cap")
    axes[1].tick_params(axis="x", rotation=30)
    axes[1].grid(axis="y", alpha=0.25)
    axes[1].legend(frameon=False)
    figure_path = output / "pilot_completion_exposure.png"
    fig.savefig(figure_path, dpi=220, facecolor="white", transparent=False)
    plt.close(fig)
    return {
        "aggregate": aggregate_path,
        "paired": pair_path,
        "contrasts": contrast_path,
        "figure": figure_path,
    }
