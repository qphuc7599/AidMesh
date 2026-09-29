"""Advanced family-aware inference and predictive validation for BOW campaigns."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from aidmesh.analysis.hierarchical_bow import _paired_deltas
from aidmesh.experiments.bow_factorial import FACTOR_LEVELS


def benjamini_hochberg(coefficients: pd.DataFrame) -> pd.DataFrame:
    """Add monotone Benjamini-Hochberg q-values to coefficient tests."""

    result = coefficients.copy()
    if result.empty or "p_value" not in result:
        result["q_value_bh"] = pd.Series(dtype=float)
        return result
    p_values = result["p_value"].fillna(1.0).clip(0.0, 1.0).to_numpy(float)
    order = np.argsort(p_values)
    ranked = p_values[order]
    adjusted = ranked * len(ranked) / np.arange(1, len(ranked) + 1)
    adjusted = np.minimum.accumulate(adjusted[::-1])[::-1].clip(0.0, 1.0)
    q_values = np.empty_like(adjusted)
    q_values[order] = adjusted
    result["q_value_bh"] = q_values
    return result


def family_forest_effects(
    frame: pd.DataFrame,
    *,
    outcome: str = "completion_rate",
    reference: str = "optimized_static",
) -> pd.DataFrame:
    """Return context and equal-context family effects for forest reporting."""

    paired = _paired_deltas(frame, outcome=outcome, reference=reference)
    if "family" not in paired:
        paired["family"] = paired["context"]
    context_column = "registered_context" if "registered_context" in paired else "context"
    rows: list[dict[str, Any]] = []
    for (policy, family, context), block in paired.groupby(
        ["policy", "family", context_column], sort=True
    ):
        seed_means = block.groupby("seed")["delta"].mean()
        standard_error = float(seed_means.std(ddof=1) / np.sqrt(len(seed_means))) if len(
            seed_means
        ) > 1 else 0.0
        estimate = float(seed_means.mean())
        rows.append(
            {
                "level": "context",
                "policy": policy,
                "reference": reference,
                "family": family,
                "context": context,
                "seeds": len(seed_means),
                "mean_effect": estimate,
                "std_error": standard_error,
                "ci_low": estimate - 1.96 * standard_error,
                "ci_high": estimate + 1.96 * standard_error,
            }
        )
    context_frame = pd.DataFrame(rows)
    family_rows = []
    for (policy, family), block in context_frame.groupby(["policy", "family"], sort=True):
        estimate = float(block["mean_effect"].mean())
        standard_error = float(block["mean_effect"].std(ddof=1) / np.sqrt(len(block))) if len(
            block
        ) > 1 else float(block["std_error"].mean())
        family_rows.append(
            {
                "level": "family",
                "policy": policy,
                "reference": reference,
                "family": family,
                "context": "ALL",
                "seeds": int(block["seeds"].sum()),
                "mean_effect": estimate,
                "std_error": standard_error,
                "ci_low": estimate - 1.96 * standard_error,
                "ci_high": estimate + 1.96 * standard_error,
            }
        )
    return pd.concat([context_frame, pd.DataFrame(family_rows)], ignore_index=True)


def empirical_regret(
    frame: pd.DataFrame,
    *,
    policy: str = "adatransfer",
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Compute run-level and context-level regret against the registered flow oracle."""

    required = {
        "policy",
        "completed",
        "duplicate_exposure",
        "oracle_total_completed",
        "generated_requests",
    }
    if missing := required - set(frame):
        raise ValueError(f"regret columns are absent: {sorted(missing)}")
    runs = frame.loc[frame["policy"] == policy].copy()
    runs = runs.loc[runs["oracle_total_completed"].notna()].copy()
    # The oracle optimizes one useful completion per nullifier.  Raw online
    # ``completed`` can be larger only by counting losing cross-site duplicate
    # acceptances, which are exposure rather than utility.  Compare like with
    # like so regret remains a valid non-negative optimality gap.
    runs["useful_completed"] = runs["completed"] - runs["duplicate_exposure"]
    runs["absolute_regret"] = (
        runs["oracle_total_completed"] - runs["useful_completed"]
    )
    if (runs["absolute_regret"] < 0).any():
        minimum = float(runs["absolute_regret"].min())
        raise AssertionError(f"oracle regret is negative after exposure correction: {minimum}")
    runs["normalized_regret"] = runs["absolute_regret"] / runs[
        "oracle_total_completed"
    ].clip(lower=1)
    runs["competitive_ratio"] = runs["useful_completed"] / runs[
        "oracle_total_completed"
    ].clip(lower=1)
    group_columns = [
        column
        for column in ("family", "registered_context", "context", "cap", "quorum_availability")
        if column in runs
    ]
    summary = (
        runs.groupby(group_columns, as_index=False)
        .agg(
            runs=("completed", "size"),
            mean_absolute_regret=("absolute_regret", "mean"),
            p95_absolute_regret=("absolute_regret", lambda values: values.quantile(0.95)),
            mean_normalized_regret=("normalized_regret", "mean"),
            mean_competitive_ratio=("competitive_ratio", "mean"),
            p05_competitive_ratio=("competitive_ratio", lambda values: values.quantile(0.05)),
        )
    )
    return runs, summary


def _factor_matrix(frame: pd.DataFrame) -> tuple[np.ndarray, list[str]]:
    columns = [np.ones(len(frame), dtype=float)]
    names = ["intercept"]
    encoded: dict[str, np.ndarray] = {}
    for factor, levels in FACTOR_LEVELS.items():
        values = (frame[factor].astype(str) == levels[1]).to_numpy(float)
        encoded[factor] = values
        columns.append(values)
        names.append(factor)
    interactions = (
        ("demand_drift", "cap_tightness"),
        ("demand_drift", "transfer_reliability"),
        ("buffer_provisioning", "cross_class_traffic"),
        ("contact_predictability", "transfer_reliability"),
        ("witness_availability", "cross_class_traffic"),
    )
    for left, right in interactions:
        columns.append(encoded[left] * encoded[right])
        names.append(f"{left}:{right}")
    return np.column_stack(columns), names


def leave_one_family_out_validation(
    frame: pd.DataFrame,
    *,
    policy: str = "adatransfer",
    reference: str = "optimized_static",
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Predict paired policy gains for every entirely held-out trace family."""

    paired = _paired_deltas(frame, outcome="completion_rate", reference=reference)
    paired = paired.loc[paired["policy"] == policy].copy()
    if "family" not in paired or paired["family"].nunique() < 2:
        raise ValueError("LOFO validation requires at least two registered families")
    predictions = []
    for held_family in sorted(paired["family"].unique()):
        training = paired.loc[paired["family"] != held_family]
        testing = paired.loc[paired["family"] == held_family]
        x_train, terms = _factor_matrix(training)
        x_test, _ = _factor_matrix(testing)
        y_train = training["delta"].to_numpy(float)
        penalty = np.eye(x_train.shape[1]) * 1e-6
        penalty[0, 0] = 0.0
        beta = np.linalg.solve(x_train.T @ x_train + penalty, x_train.T @ y_train)
        fitted = x_train @ beta
        residuals = y_train - fitted
        low, high = (
            np.quantile(residuals, (0.025, 0.975)) if len(residuals) > 1 else (0.0, 0.0)
        )
        predicted = x_test @ beta
        block = testing[
            [
                column
                for column in (
                    "family",
                    "registered_context",
                    "context",
                    "seed",
                    "cell",
                    *FACTOR_LEVELS,
                )
                if column in testing
            ]
        ].copy()
        block["actual_gain"] = testing["delta"].to_numpy(float)
        block["predicted_gain"] = predicted
        block["prediction_low"] = predicted + low
        block["prediction_high"] = predicted + high
        block["covered"] = (
            (block["actual_gain"] >= block["prediction_low"])
            & (block["actual_gain"] <= block["prediction_high"])
        )
        block["sign_correct"] = np.sign(block["actual_gain"]) == np.sign(
            block["predicted_gain"]
        )
        block["squared_error"] = (
            block["actual_gain"] - block["predicted_gain"]
        ) ** 2
        block["terms"] = "|".join(terms)
        predictions.append(block)
    prediction_frame = pd.concat(predictions, ignore_index=True)
    summary = (
        prediction_frame.groupby("family", as_index=False)
        .agg(
            observations=("actual_gain", "size"),
            actual_mean=("actual_gain", "mean"),
            predicted_mean=("predicted_gain", "mean"),
            rmse=("squared_error", lambda values: float(np.sqrt(values.mean()))),
            sign_accuracy=("sign_correct", "mean"),
            prediction_interval_coverage=("covered", "mean"),
        )
    )
    try:
        prediction_frame["calibration_bin"] = pd.qcut(
            prediction_frame["predicted_gain"],
            q=min(10, prediction_frame["predicted_gain"].nunique()),
            duplicates="drop",
        ).astype(str)
    except ValueError:
        prediction_frame["calibration_bin"] = "all"
    calibration = (
        prediction_frame.groupby("calibration_bin", as_index=False, observed=True)
        .agg(
            observations=("actual_gain", "size"),
            predicted_mean=("predicted_gain", "mean"),
            actual_mean=("actual_gain", "mean"),
            actual_std=("actual_gain", "std"),
        )
        .sort_values("predicted_mean")
    )
    return prediction_frame, summary, calibration


def _forest_plot(frame: pd.DataFrame, path: Path) -> None:
    block = frame.loc[frame["level"] == "family"].sort_values(
        ["policy", "family"]
    )
    if block.empty:
        return
    labels = [f"{row.policy} | {row.family}" for row in block.itertuples()]
    positions = np.arange(len(block))
    figure, axis = plt.subplots(figsize=(8.0, max(3.2, 0.34 * len(block))))
    axis.errorbar(
        block["mean_effect"],
        positions,
        xerr=np.vstack(
            (
                block["mean_effect"] - block["ci_low"],
                block["ci_high"] - block["mean_effect"],
            )
        ),
        fmt="o",
        color="#174a7e",
        ecolor="#6b8fb3",
        capsize=3,
    )
    axis.axvline(0.0, color="black", linewidth=0.8, linestyle="--")
    axis.set_yticks(positions, labels)
    axis.set_xlabel("Paired completion-rate effect vs optimized static")
    axis.grid(axis="x", alpha=0.2)
    figure.tight_layout()
    figure.savefig(path, dpi=220)
    plt.close(figure)


def _calibration_plot(frame: pd.DataFrame, path: Path) -> None:
    if frame.empty:
        return
    low = min(frame["predicted_mean"].min(), frame["actual_mean"].min())
    high = max(frame["predicted_mean"].max(), frame["actual_mean"].max())
    figure, axis = plt.subplots(figsize=(5.4, 4.6))
    axis.plot([low, high], [low, high], linestyle="--", color="black", linewidth=0.8)
    axis.scatter(frame["predicted_mean"], frame["actual_mean"], color="#b23a48")
    axis.set_xlabel("Predicted held-family gain")
    axis.set_ylabel("Observed held-family gain")
    axis.grid(alpha=0.2)
    figure.tight_layout()
    figure.savefig(path, dpi=220)
    plt.close(figure)


def write_advanced_analysis(
    frame: pd.DataFrame,
    output: Path,
    *,
    kind: str,
    coefficients: pd.DataFrame | None = None,
) -> dict[str, Path]:
    """Write checksummed advanced analysis tables and publication plots."""

    output.mkdir(parents=True, exist_ok=True)
    assets: dict[str, Path] = {}
    forest = family_forest_effects(frame)
    forest_path = output / f"bow_{kind}_family_forest.csv"
    forest.to_csv(forest_path, index=False, lineterminator="\n")
    assets["family_forest"] = forest_path
    forest_plot = output / f"bow_{kind}_family_forest.png"
    _forest_plot(forest, forest_plot)
    if forest_plot.exists():
        assets["family_forest_plot"] = forest_plot
    if kind == "primary":
        regret_runs, regret_summary = empirical_regret(frame)
        for name, data in (("regret_runs", regret_runs), ("regret_summary", regret_summary)):
            path = output / f"bow_primary_{name}.csv"
            data.to_csv(path, index=False, lineterminator="\n")
            assets[name] = path
    elif kind == "factorial":
        predictions, lofo_summary, calibration = leave_one_family_out_validation(frame)
        for name, data in (
            ("lofo_predictions", predictions),
            ("lofo_summary", lofo_summary),
            ("calibration", calibration),
        ):
            path = output / f"bow_factorial_{name}.csv"
            data.to_csv(path, index=False, lineterminator="\n")
            assets[name] = path
        calibration_plot = output / "bow_factorial_lofo_calibration.png"
        _calibration_plot(calibration, calibration_plot)
        assets["calibration_plot"] = calibration_plot
        if coefficients is not None:
            corrected = benjamini_hochberg(coefficients)
            corrected_path = output / "bow_factorial_mechanism_coefficients_bh.csv"
            corrected.to_csv(corrected_path, index=False, lineterminator="\n")
            assets["coefficients_bh"] = corrected_path
    manifest = {
        "schema_version": 1,
        "kind": kind,
        "files": {name: path.name for name, path in assets.items()},
        "sha256": {
            name: hashlib.sha256(path.read_bytes()).hexdigest()
            for name, path in assets.items()
        },
    }
    manifest_path = output / f"bow_{kind}_advanced_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    assets["manifest"] = manifest_path
    return assets
