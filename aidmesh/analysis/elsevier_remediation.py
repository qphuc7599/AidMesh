"""Reviewer-requested direct, clustered, and sensitivity analyses."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import matplotlib
import numpy as np
import pandas as pd

from aidmesh.experiments.catr_confirmatory import CATR_FACTOR_LEVELS

matplotlib.use("Agg")
matplotlib.rcParams.update({"pdf.fonttype": 42, "ps.fonttype": 42})
import matplotlib.pyplot as plt  # noqa: E402

PAIR_KEYS = [
    "registered_context",
    "seed",
    "cell",
    *CATR_FACTOR_LEVELS,
]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _paired(frame: pd.DataFrame, candidate: str, reference: str) -> pd.DataFrame:
    outcomes = [
        "completion_rate",
        "end_to_end_settlement_probability",
        "exposure_per_1000_requests",
        "restricted_mean_latency_hours",
        "forwarded_bytes",
        "peak_buffer_bytes",
        "transfers_started",
        "transfers_accepted",
        "transfers_reclaimed",
        "transfers_stranded",
    ]
    candidate_rows = frame.loc[frame["policy"] == candidate, PAIR_KEYS + outcomes]
    reference_rows = frame.loc[frame["policy"] == reference, PAIR_KEYS + outcomes]
    paired = candidate_rows.merge(
        reference_rows,
        on=PAIR_KEYS,
        suffixes=("_candidate", "_reference"),
        validate="one_to_one",
    )
    for outcome in outcomes:
        paired[f"{outcome}_delta"] = (
            paired[f"{outcome}_candidate"] - paired[f"{outcome}_reference"]
        )
    return paired


def _cluster_bootstrap(
    frame: pd.DataFrame,
    value: str,
    *,
    cluster: str = "registered_context",
    repetitions: int,
    seed: int,
) -> tuple[float, float, float, int]:
    means = frame.groupby(cluster, sort=True)[value].mean().dropna().to_numpy(dtype=float)
    if not len(means):
        return np.nan, np.nan, np.nan, 0
    rng = np.random.default_rng(seed)
    draws = rng.choice(means, size=(repetitions, len(means)), replace=True).mean(axis=1)
    return (
        float(means.mean()),
        float(np.quantile(draws, 0.025)),
        float(np.quantile(draws, 0.975)),
        len(means),
    )


def _wild_cluster_ci(
    frame: pd.DataFrame,
    value: str,
    *,
    cluster: str = "registered_context",
    repetitions: int,
    seed: int,
) -> tuple[float, float, float, float, int]:
    """Rademacher wild-cluster sensitivity for an intercept-only paired estimand."""

    means = frame.groupby(cluster, sort=True)[value].mean().dropna().to_numpy(dtype=float)
    if not len(means):
        return np.nan, np.nan, np.nan, np.nan, 0
    estimate = float(means.mean())
    rng = np.random.default_rng(seed)
    signs = rng.choice((-1.0, 1.0), size=(repetitions, len(means)))
    centered_draws = (signs * (means - estimate)).mean(axis=1)
    null_draws = (signs * means).mean(axis=1)
    low = estimate - float(np.quantile(centered_draws, 0.975))
    high = estimate - float(np.quantile(centered_draws, 0.025))
    p_value = float((np.count_nonzero(np.abs(null_draws) >= abs(estimate)) + 1) / (repetitions + 1))
    return estimate, low, high, p_value, len(means)


def _direct_effects(paired: pd.DataFrame, repetitions: int) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for index, drift in enumerate(("all", *CATR_FACTOR_LEVELS["demand_drift"])):
        subset = paired if drift == "all" else paired.loc[paired["demand_drift"] == drift]
        estimate, low, high, clusters = _cluster_bootstrap(
            subset,
            "completion_rate_delta",
            repetitions=repetitions,
            seed=202609010 + index,
        )
        records.append(
            {
                "contrast": "AdaTransfer minus robust static",
                "demand_drift": drift,
                "incident_clusters": clusters,
                "paired_cells": len(subset),
                "mean_completion_delta": estimate,
                "ci95_low": low,
                "ci95_high": high,
                "beneficial_incident_fraction": float(
                    (subset.groupby("registered_context")["completion_rate_delta"].mean() > 0.01)
                    .mean()
                ),
                "harmful_incident_fraction": float(
                    (subset.groupby("registered_context")["completion_rate_delta"].mean() < -0.01)
                    .mean()
                ),
            }
        )
    return pd.DataFrame(records)


def _stratified_effects(paired: pd.DataFrame, repetitions: int) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    index = 0
    for factor, levels in CATR_FACTOR_LEVELS.items():
        for level in levels:
            subset = paired.loc[paired[factor] == level]
            estimate, low, high, clusters = _cluster_bootstrap(
                subset,
                "completion_rate_delta",
                repetitions=repetitions,
                seed=202609100 + index,
            )
            records.append(
                {
                    "factor": factor,
                    "level": level,
                    "incident_clusters": clusters,
                    "paired_cells": len(subset),
                    "mean_completion_delta": estimate,
                    "ci95_low": low,
                    "ci95_high": high,
                }
            )
            index += 1
    return pd.DataFrame(records)


def _factor_interactions(paired: pd.DataFrame, repetitions: int) -> pd.DataFrame:
    """Estimate balanced interactions with pointwise and family-wise intervals.

    The simultaneous intervals use an incident-cluster bootstrap max-|t|
    critical value across the complete, preregistered eight-interaction family.
    """

    records: list[dict[str, Any]] = []
    incident_interactions: list[pd.Series] = []
    for index, (factor, levels) in enumerate(CATR_FACTOR_LEVELS.items()):
        low, high = levels
        incident_level = (
            paired.groupby(["registered_context", factor], as_index=False)[
                "completion_rate_delta"
            ]
            .mean()
            .pivot(index="registered_context", columns=factor, values="completion_rate_delta")
            .dropna()
        )
        incident_level["interaction"] = incident_level[high] - incident_level[low]
        incident_interactions.append(incident_level["interaction"].rename(factor))
        estimate, ci_low, ci_high, clusters = _cluster_bootstrap(
            incident_level.reset_index(),
            "interaction",
            repetitions=repetitions,
            seed=202609200 + index,
        )
        records.append(
            {
                "factor": factor,
                "low_level": low,
                "high_level": high,
                "estimand": f"(Ada-robust at {high}) - (Ada-robust at {low})",
                "incident_clusters": clusters,
                "interaction_completion_delta": estimate,
                "ci95_low": ci_low,
                "ci95_high": ci_high,
            }
        )

    result = pd.DataFrame(records)
    family = pd.concat(incident_interactions, axis=1, join="inner").dropna()
    cluster_count = len(family)
    if cluster_count < 2:
        result["simultaneous_ci95_low"] = np.nan
        result["simultaneous_ci95_high"] = np.nan
        result["max_t_critical_value"] = np.nan
        return result

    values = family.to_numpy(dtype=float)
    estimate = values.mean(axis=0)
    standard_error = values.std(axis=0, ddof=1) / np.sqrt(cluster_count)
    rng = np.random.default_rng(202609299)
    sampled_indices = rng.integers(
        0,
        cluster_count,
        size=(repetitions, cluster_count),
    )
    sampled = values[sampled_indices, :]
    bootstrap_estimate = sampled.mean(axis=1)
    bootstrap_se = sampled.std(axis=1, ddof=1) / np.sqrt(cluster_count)
    with np.errstate(divide="ignore", invalid="ignore"):
        studentized = np.abs((bootstrap_estimate - estimate) / bootstrap_se)
    studentized = np.where(np.isfinite(studentized), studentized, 0.0)
    max_t = studentized.max(axis=1)
    critical = float(np.quantile(max_t, 0.95))
    simultaneous_low = estimate - critical * standard_error
    simultaneous_high = estimate + critical * standard_error
    by_factor = {
        factor: (float(low), float(high))
        for factor, low, high in zip(
            family.columns,
            simultaneous_low,
            simultaneous_high,
            strict=True,
        )
    }
    result["simultaneous_ci95_low"] = result["factor"].map(
        lambda factor: by_factor[str(factor)][0]
    )
    result["simultaneous_ci95_high"] = result["factor"].map(
        lambda factor: by_factor[str(factor)][1]
    )
    result["max_t_critical_value"] = critical
    return result


def _joint_endpoints(paired: pd.DataFrame, repetitions: int) -> pd.DataFrame:
    metrics = (
        "completion_rate",
        "end_to_end_settlement_probability",
        "exposure_per_1000_requests",
        "restricted_mean_latency_hours",
        "forwarded_bytes",
        "peak_buffer_bytes",
        "transfers_started",
        "transfers_accepted",
        "transfers_reclaimed",
        "transfers_stranded",
    )
    records: list[dict[str, Any]] = []
    for index, metric in enumerate(metrics):
        estimate, low, high, clusters = _cluster_bootstrap(
            paired,
            f"{metric}_delta",
            repetitions=repetitions,
            seed=202609300 + index,
        )
        records.append(
            {
                "endpoint": metric,
                "direction": "AdaTransfer minus robust static",
                "incident_clusters": clusters,
                "mean_delta": estimate,
                "ci95_low": low,
                "ci95_high": high,
                "candidate_mean": float(paired[f"{metric}_candidate"].mean()),
                "reference_mean": float(paired[f"{metric}_reference"].mean()),
            }
        )
    return pd.DataFrame(records)


def _leave_one_incident_out(paired: pd.DataFrame) -> pd.DataFrame:
    records = []
    contexts = sorted(paired["registered_context"].unique())
    for drift in ("all", *CATR_FACTOR_LEVELS["demand_drift"]):
        subset = paired if drift == "all" else paired.loc[paired["demand_drift"] == drift]
        for omitted in contexts:
            retained = subset.loc[subset["registered_context"] != omitted]
            incident_means = retained.groupby("registered_context")["completion_rate_delta"].mean()
            records.append(
                {
                    "demand_drift": drift,
                    "omitted_incident": omitted,
                    "retained_incidents": len(incident_means),
                    "mean_completion_delta": float(incident_means.mean()),
                }
            )
    return pd.DataFrame(records)


def _incident_effects(paired: pd.DataFrame) -> pd.DataFrame:
    effects = (
        paired.groupby(["registered_context", "demand_drift"], as_index=False)
        .agg(
            mean_completion_delta=("completion_rate_delta", "mean"),
            paired_cells=("completion_rate_delta", "size"),
        )
        .sort_values(["demand_drift", "mean_completion_delta", "registered_context"])
    )
    effects["cluster_weight"] = 1.0 / paired["registered_context"].nunique()
    return effects


def _wild_cluster_sensitivity(paired: pd.DataFrame, repetitions: int) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for index, drift in enumerate(("all", *CATR_FACTOR_LEVELS["demand_drift"])):
        subset = paired if drift == "all" else paired.loc[paired["demand_drift"] == drift]
        estimate, low, high, p_value, clusters = _wild_cluster_ci(
            subset,
            "completion_rate_delta",
            repetitions=repetitions,
            seed=202609400 + index,
        )
        records.append(
            {
                "estimand": "AdaTransfer minus robust static",
                "stratum": drift,
                "incident_clusters": clusters,
                "mean_completion_delta": estimate,
                "wild_ci95_low": low,
                "wild_ci95_high": high,
                "wild_two_sided_p": p_value,
            }
        )

    interaction = (
        paired.groupby(["registered_context", "demand_drift"], as_index=False)[
            "completion_rate_delta"
        ]
        .mean()
        .pivot(index="registered_context", columns="demand_drift", values="completion_rate_delta")
        .dropna()
    )
    interaction["interaction"] = interaction["shifting"] - interaction["stable"]
    estimate, low, high, p_value, clusters = _wild_cluster_ci(
        interaction.reset_index(),
        "interaction",
        repetitions=repetitions,
        seed=202609403,
    )
    records.append(
        {
            "estimand": "(Ada-robust shifting) minus (Ada-robust stable)",
            "stratum": "demand_drift_interaction",
            "incident_clusters": clusters,
            "mean_completion_delta": estimate,
            "wild_ci95_low": low,
            "wild_ci95_high": high,
            "wild_two_sided_p": p_value,
        }
    )
    return pd.DataFrame(records)


def _policy_absolute_endpoints(factorial: pd.DataFrame) -> pd.DataFrame:
    metrics = [
        "completion_rate",
        "end_to_end_settlement_probability",
        "exposure_per_1000_requests",
        "restricted_mean_latency_hours",
        "transfers_stranded",
    ]
    incident_means = factorial.groupby(
        ["policy", "demand_drift", "registered_context"], as_index=False
    )[metrics].mean()
    return (
        incident_means.groupby(["policy", "demand_drift"], as_index=False)[metrics]
        .mean()
        .sort_values(["demand_drift", "policy"])
    )


def _terminal_reachability_sensitivity(factorial: pd.DataFrame) -> pd.DataFrame:
    transfer = factorial.loc[factorial["policy"] == "reclaimable_transfer"]
    counts = [
        "transfers_started",
        "transfers_accepted",
        "transfers_reclaimed",
        "transfers_locked_final",
    ]
    incident = transfer.groupby(
        ["registered_context", "witness_availability", "recovery_timing"],
        as_index=False,
    )[counts].sum()
    denominator = incident["transfers_started"].replace(0, np.nan)
    incident["terminal_resolution_fraction"] = (
        incident["transfers_accepted"] + incident["transfers_reclaimed"]
    ) / denominator
    incident["final_lock_fraction"] = incident["transfers_locked_final"] / denominator
    return (
        incident.groupby(["witness_availability", "recovery_timing"], as_index=False)
        .agg(
            incident_clusters=("registered_context", "nunique"),
            mean_transfers_started=("transfers_started", "mean"),
            mean_terminal_resolution_fraction=("terminal_resolution_fraction", "mean"),
            mean_final_lock_fraction=("final_lock_fraction", "mean"),
        )
        .sort_values(["witness_availability", "recovery_timing"])
    )


def _selection_table(manifest_path: Path) -> pd.DataFrame:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    records = []
    for row in manifest["incidents"]:
        failures = []
        if row["nodes_with_contacts"] < 8:
            failures.append("nodes<8")
        if row["duration_us"] < 2 * 3_600_000_000:
            failures.append("duration<2h")
        if row["calibration_contacts"] < 100:
            failures.append("calibration_contacts<100")
        if row["heldout_contacts"] < 100:
            failures.append("heldout_contacts<100")
        records.append(
            {
                "context": row["context"],
                "eligible": bool(row["eligible"]),
                "exclusion_reason": ";".join(failures) if failures else "retained",
                "nodes_with_contacts": row["nodes_with_contacts"],
                "duration_hours": row["duration_us"] / 3_600_000_000,
                "calibration_contacts": row["calibration_contacts"],
                "heldout_contacts": row["heldout_contacts"],
                "normalized_contacts": row["normalized_contacts"],
            }
        )
    return pd.DataFrame(records)


def _range_summary(manifests: dict[int, Path]) -> pd.DataFrame:
    records = []
    for range_m, path in sorted(manifests.items()):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        rows = pd.DataFrame(manifest["incidents"])
        records.append(
            {
                "radio_range_m": range_m,
                "total_incidents": len(rows),
                "eligible_incidents": int(rows["eligible"].sum()),
                "median_nodes_with_contacts": float(rows["nodes_with_contacts"].median()),
                "median_raw_contact_samples": float(rows["raw_contact_samples"].median()),
                "median_heldout_contacts": float(rows["heldout_contacts"].median()),
            }
        )
    return pd.DataFrame(records)


def _write_figure(
    direct: pd.DataFrame,
    interactions: pd.DataFrame,
    output: Path,
) -> Path:
    figure, axes = plt.subplots(1, 2, figsize=(10.4, 4.0))
    left = direct.iloc[::-1].reset_index(drop=True)
    y = np.arange(len(left))
    axes[0].errorbar(
        left["mean_completion_delta"],
        y,
        xerr=np.vstack(
            (
                left["mean_completion_delta"] - left["ci95_low"],
                left["ci95_high"] - left["mean_completion_delta"],
            )
        ),
        fmt="o",
        capsize=3,
        color="#1f5a94",
    )
    axes[0].axvline(0, color="black", linewidth=0.8)
    axes[0].set_yticks(y, left["demand_drift"])
    axes[0].set_xlabel("Completion delta (AdaTransfer - robust static)")
    axes[0].set_title("Direct paired contrast")

    right = interactions.sort_values("interaction_completion_delta").reset_index(drop=True)
    y = np.arange(len(right))
    axes[1].errorbar(
        right["interaction_completion_delta"],
        y,
        xerr=np.vstack(
            (
                right["interaction_completion_delta"]
                - right["simultaneous_ci95_low"],
                right["simultaneous_ci95_high"]
                - right["interaction_completion_delta"],
            )
        ),
        fmt="o",
        capsize=3,
        color="#a63603",
    )
    axes[1].axvline(0, color="black", linewidth=0.8)
    factor_labels = {
        "demand_drift": "Demand drift",
        "cap_tightness": "Cap tightness",
        "witness_availability": "Witness availability",
        "transfer_reliability": "Transfer reliability",
        "contact_predictability": "Contact predictability",
        "buffer_provisioning": "Buffer provisioning",
        "cross_class_traffic": "Background traffic",
        "recovery_timing": "Recovery timing",
    }
    axes[1].set_yticks(
        y, [factor_labels.get(value, value.replace("_", " ")) for value in right["factor"]]
    )
    axes[1].set_xlabel("High-low change in direct contrast")
    axes[1].set_title("Interactions (simultaneous 95% intervals)")
    figure.tight_layout()
    path = output / "direct_effects_and_interactions.pdf"
    figure.savefig(path, bbox_inches="tight")
    plt.close(figure)
    return path


def _write_operating_envelope_figure(
    policy_endpoints: pd.DataFrame,
    incident_effects: pd.DataFrame,
    output: Path,
) -> Path:
    labels = {
        "optimized_static": "Opt/CATR",
        "robust_static": "Robust",
        "periodic_sync": "Periodic",
        "adatransfer": "Ada",
        "reclaimable_transfer": "RCT",
    }
    offsets = {
        ("stable", "adatransfer"): (-12, -12),
        ("stable", "robust_static"): (4, 4),
        ("stable", "optimized_static"): (-28, 5),
        ("stable", "reclaimable_transfer"): (-22, -12),
        ("shifting", "adatransfer"): (3, 5),
        ("shifting", "robust_static"): (-38, -12),
        ("shifting", "optimized_static"): (4, 5),
        ("shifting", "reclaimable_transfer"): (-25, -12),
    }
    figure = plt.figure(figsize=(10.4, 7.0))
    grid = figure.add_gridspec(2, 2, height_ratios=(1.0, 1.15), hspace=0.45, wspace=0.28)
    for column, drift in enumerate(("stable", "shifting")):
        axis = figure.add_subplot(grid[0, column])
        subset = policy_endpoints.loc[policy_endpoints["demand_drift"] == drift]
        axis.scatter(
            subset["exposure_per_1000_requests"],
            subset["completion_rate"],
            color="#1f5a94" if drift == "stable" else "#a63603",
            s=28,
        )
        for row in subset.itertuples(index=False):
            if row.policy == "catr":
                continue
            axis.annotate(
                labels.get(row.policy, row.policy),
                (row.exposure_per_1000_requests, row.completion_rate),
                xytext=offsets.get((drift, row.policy), (4, 3)),
                textcoords="offset points",
                fontsize=7,
            )
        axis.set_title(f"{drift.capitalize()} demand")
        axis.set_xlabel("Duplicate exposure per 1,000 requests")
        axis.set_ylabel("Absolute completion")
        axis.grid(alpha=0.2)

    axis = figure.add_subplot(grid[1, :])
    wide = incident_effects.pivot(
        index="registered_context", columns="demand_drift", values="mean_completion_delta"
    ).dropna()
    wide = wide.sort_values("shifting")
    y = np.arange(len(wide))
    axis.scatter(wide["stable"], y, s=18, label="stable", color="#1f5a94")
    axis.scatter(wide["shifting"], y, s=18, label="shifting", color="#a63603")
    axis.axvline(0, color="black", linewidth=0.8)
    incident_labels = [
        f"WF{int(value.split('_wf', 1)[1].split('_', 1)[0]):02d}"
        if "_wf" in value
        else value
        for value in wide.index
    ]
    axis.set_yticks(y, incident_labels, fontsize=7)
    axis.set_xlabel("Incident-level completion delta (AdaTransfer - robust static)")
    axis.set_title("Equal-weight incident heterogeneity")
    axis.legend(frameon=False, ncol=2, loc="upper left")
    axis.grid(axis="x", alpha=0.2)
    path = output / "availability_risk_and_incident_effects.pdf"
    figure.savefig(path, bbox_inches="tight")
    plt.close(figure)
    return path


def write_elsevier_remediation_analysis(
    factorial_path: Path,
    r200_manifest: Path,
    range_manifests: dict[int, Path],
    output: Path,
    *,
    repetitions: int = 9999,
) -> dict[str, Path]:
    factorial = pd.read_csv(factorial_path)
    paired = _paired(factorial, "adatransfer", "robust_static")
    output.mkdir(parents=True, exist_ok=True)
    tables = {
        "direct_adatransfer_vs_robust_static": _direct_effects(paired, repetitions),
        "direct_stratified_effects": _stratified_effects(paired, repetitions),
        "policy_factor_interactions": _factor_interactions(paired, repetitions),
        "joint_endpoint_deltas": _joint_endpoints(paired, repetitions),
        "leave_one_incident_out": _leave_one_incident_out(paired),
        "incident_level_effects": _incident_effects(paired),
        "wild_cluster_sensitivity": _wild_cluster_sensitivity(paired, repetitions),
        "policy_absolute_endpoints": _policy_absolute_endpoints(factorial),
        "terminal_reachability_sensitivity": _terminal_reachability_sensitivity(factorial),
        "incident_selection_crosswalk": _selection_table(r200_manifest),
        "radio_range_topology_sensitivity": _range_summary(range_manifests),
    }
    assets: dict[str, Path] = {}
    for name, table in tables.items():
        path = output / f"{name}.csv"
        table.to_csv(path, index=False, lineterminator="\n")
        assets[name] = path
    assets["direct_effects_and_interactions"] = _write_figure(
        tables["direct_adatransfer_vs_robust_static"],
        tables["policy_factor_interactions"],
        output,
    )
    assets["availability_risk_and_incident_effects"] = _write_operating_envelope_figure(
        tables["policy_absolute_endpoints"],
        tables["incident_level_effects"],
        output,
    )
    loo = tables["leave_one_incident_out"]
    report = {
        "schema_version": 2,
        "candidate": "adatransfer",
        "reference": "robust_static",
        "bootstrap_unit": "wildfire incident",
        "bootstrap_repetitions": repetitions,
        "interaction_family": (
            "All eight registered policy-by-factor interactions; simultaneous "
            "95% max-|t| incident-cluster bootstrap intervals"
        ),
        "wild_cluster_sensitivity": "Rademacher sign weights over incident means",
        "paired_rows": len(paired),
        "incident_clusters": paired["registered_context"].nunique(),
        "factorial_cells": paired["cell"].nunique(),
        "seeds": sorted(int(seed) for seed in paired["seed"].unique()),
        "leave_one_out_range": {
            drift: {
                "minimum": float(group["mean_completion_delta"].min()),
                "maximum": float(group["mean_completion_delta"].max()),
            }
            for drift, group in loo.groupby("demand_drift")
        },
        "interpretation": (
            "The direct contrast is heterogeneous: stable demand favors robust static, "
            "while shifting demand favors AdaTransfer. It does not support universal "
            "superiority of an adaptive policy."
        ),
    }
    report_path = output / "analysis_report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    assets["analysis_report"] = report_path
    checksum_path = output / "CHECKSUMS.sha256"
    checksum_path.write_text(
        "".join(f"{_sha256(path)}  {path.name}\n" for path in assets.values()),
        encoding="ascii",
    )
    assets["checksums"] = checksum_path
    return assets
