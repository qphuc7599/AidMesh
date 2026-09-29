"""Build auditable paper tables and figures from confirmatory artifacts.

This module does not alter the registered RQ1/RQ2 estimands.  It copies their
frozen contrasts and performs presentation-only aggregation for the main paper,
plus explicitly supporting analyses for allocation, privacy, implementation,
and external DTN traces.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.stats import bootstrap
from statsmodels.stats.multitest import multipletests

CORE_SCENARIO = "CONF_CORE_W08_P24"
METHOD_ORDER = (
    "online_only",
    "aidmesh_quorum_only",
    "aidmesh_static_rights",
    "aidmesh_transferable_rights",
    "first_seen_dtn",
)
METHOD_LABELS = {
    "online_only": "Online-only",
    "aidmesh_quorum_only": "Quorum-only",
    "aidmesh_static_rights": "Static rights",
    "aidmesh_transferable_rights": "Transferable rights",
    "first_seen_dtn": "FirstSeen-DTN",
    "equal-share": "Equal share",
    "demand-proportional": "Demand proportional",
    "utility-only": "Utility only",
    "aidmesh-progressive-fill": "Progressive fill",
    "aidmesh-maximin-utility": "Maximin + utility",
}
DTN_CONTEXTS = ("invs13", "lh10", "sfhh")
DTN_POLICIES = (
    "fifo_single_copy",
    "epidemic",
    "binary_spray_wait",
    "risk_priority_spray",
)


def _require(path: Path) -> Path:
    if not path.exists():
        raise FileNotFoundError(path)
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _mean_interval(
    values: np.ndarray,
    *,
    confidence_level: float = 0.95,
    resamples: int = 20_000,
    seed: int = 20_260_713,
) -> tuple[float, float, float]:
    values = np.asarray(values, dtype=float)
    if values.size == 0:
        raise ValueError("cannot estimate an empty sample")
    mean = float(np.mean(values))
    if values.size < 2 or np.allclose(values, values[0]):
        return mean, mean, mean
    interval = bootstrap(
        (values,),
        np.mean,
        confidence_level=confidence_level,
        n_resamples=resamples,
        method="BCa",
        rng=np.random.default_rng(seed),
    ).confidence_interval
    return mean, float(interval.low), float(interval.high)


def _sign_flip_pvalue(
    values: np.ndarray,
    *,
    alternative: str,
    resamples: int = 20_000,
    seed: int = 20_260_713,
) -> float:
    values = np.asarray(values, dtype=float)
    observed = float(np.mean(values))
    rng = np.random.default_rng(seed)
    signs = rng.choice(np.array([-1.0, 1.0]), size=(resamples, len(values)))
    simulated = np.mean(signs * values, axis=1)
    if alternative == "greater":
        tail = simulated >= observed
    elif alternative == "less":
        tail = simulated <= observed
    elif alternative == "two-sided":
        tail = np.abs(simulated) >= abs(observed)
    else:
        raise ValueError(f"unsupported alternative: {alternative}")
    return float((np.count_nonzero(tail) + 1) / (resamples + 1))


def _core_summary(summaries: pd.DataFrame) -> pd.DataFrame:
    core = summaries[summaries["scenario_id"] == CORE_SCENARIO]
    rows: list[dict[str, Any]] = []
    for method in METHOD_ORDER:
        group = core[core["method"] == method]
        if len(group) != 50:
            raise ValueError(f"expected 50 core runs for {method}, found {len(group)}")
        completion, completion_low, completion_high = _mean_interval(
            group["completion_rate"].to_numpy()
        )
        rows.append(
            {
                "method": method,
                "label": METHOD_LABELS[method],
                "runs": int(len(group)),
                "completion_mean": completion,
                "completion_ci_low": completion_low,
                "completion_ci_high": completion_high,
                "non_home_completion_mean": float(group["non_home_completion_rate"].mean()),
                "exposure_mean": float(group["duplicate_exposure"].mean()),
                "exposure_cap_ratio_mean": float(group["exposure_cap_ratio"].mean()),
                "exposure_cap_ratio_max": float(group["exposure_cap_ratio"].max()),
                "rmst_hours_mean": float(group["rmst_hours"].mean()),
                "bytes_per_settlement_mean": float(group["bytes_per_settlement"].mean()),
            }
        )
    return pd.DataFrame(rows)


def _allocation_assets(allocation: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    summary_rows: list[dict[str, Any]] = []
    for method, group in allocation.groupby("method", sort=True):
        min_mean, min_low, min_high = _mean_interval(group["minimum_service_ratio"].to_numpy())
        utility_mean, utility_low, utility_high = _mean_interval(
            group["utility_ratio"].to_numpy()
        )
        summary_rows.append(
            {
                "method": method,
                "label": METHOD_LABELS[method],
                "instances": int(group["seed"].nunique()),
                "minimum_service_ratio_mean": min_mean,
                "minimum_service_ratio_ci_low": min_low,
                "minimum_service_ratio_ci_high": min_high,
                "utility_ratio_mean": utility_mean,
                "utility_ratio_ci_low": utility_low,
                "utility_ratio_ci_high": utility_high,
                "gini_mean": float(group["gini_service_ratio"].mean()),
                "jain_mean": float(group["jain_service_ratio"].mean()),
            }
        )
    summary = pd.DataFrame(summary_rows)

    pivot_min = allocation.pivot(
        index="seed", columns="method", values="minimum_service_ratio"
    )
    pivot_utility = allocation.pivot(index="seed", columns="method", values="utility_ratio")
    specs = (
        (
            "ALLOC_MIN_VS_DEMAND",
            pivot_min["aidmesh-maximin-utility"] - pivot_min["demand-proportional"],
            "greater",
            0.0,
        ),
        (
            "ALLOC_MIN_VS_UTILITY",
            pivot_min["aidmesh-maximin-utility"] - pivot_min["utility-only"],
            "greater",
            0.0,
        ),
        (
            "ALLOC_UTILITY_NONINFERIOR_0_97",
            pivot_utility["aidmesh-maximin-utility"] - 0.97,
            "greater",
            0.0,
        ),
    )
    contrast_rows: list[dict[str, Any]] = []
    for index, (contrast_id, values, alternative, null_value) in enumerate(specs):
        differences = values.dropna().to_numpy(dtype=float)
        mean, low, high = _mean_interval(differences, seed=20_260_720 + index)
        contrast_rows.append(
            {
                "contrast_id": contrast_id,
                "pairs": len(differences),
                "mean_difference": mean,
                "ci_low": low,
                "ci_high": high,
                "null_value": null_value,
                "alternative": alternative,
                "permutation_p_value": _sign_flip_pvalue(
                    differences, alternative=alternative, seed=20_260_730 + index
                ),
                "analysis_class": "supporting_not_frozen_primary",
            }
        )
    adjusted = multipletests(
        [row["permutation_p_value"] for row in contrast_rows], method="holm"
    )[1]
    for row, value in zip(contrast_rows, adjusted, strict=True):
        row["holm_adjusted_p_value"] = float(value)
    return summary, pd.DataFrame(contrast_rows)


def _privacy_summary(results_root: Path) -> pd.DataFrame:
    metrics = pd.read_csv(_require(results_root / "privacy_batch" / "privacy_metrics.csv"))
    delays = pd.read_csv(_require(results_root / "privacy_batch" / "delay_costs.csv"))
    manifests = pd.read_csv(_require(results_root / "privacy_batch" / "batch_manifest.csv"))

    delay_by_run = (
        delays.groupby(["method", "seed"], sort=True)
        .agg(
            median_added_delay_hours=("added_delay_us", lambda x: float(x.median()) / 3.6e9),
            deadline_miss_fraction=("deadline_missed_after_batch", "mean"),
            transport_overhead_bytes_mean=("transport_overhead_bytes", "mean"),
        )
        .reset_index()
    )
    delay_summary = (
        delay_by_run.groupby("method", sort=True)
        .agg(
            median_added_delay_hours_mean=("median_added_delay_hours", "mean"),
            deadline_miss_fraction_mean=("deadline_miss_fraction", "mean"),
            transport_overhead_bytes_mean=("transport_overhead_bytes_mean", "mean"),
        )
        .reset_index()
    )
    batch_summary = (
        manifests.groupby("method", sort=True)
        .agg(
            dummy_receipts=("dummy_receipts", "sum"),
            public_receipts=("public_receipts", "sum"),
        )
        .reset_index()
    )
    grouped = (
        metrics.groupby(["method", "vantage"], sort=True)
        .agg(
            runs=("seed", "nunique"),
            observations_mean=("observations", "mean"),
            min_anonymity_set_mean=("min_anonymity_set", "mean"),
            posterior_entropy_bits_mean=("posterior_entropy_bits", "mean"),
            attacker_top1_success_mean=("attacker_top1_success", "mean"),
            unique_bucket_fraction_mean=("unique_bucket_fraction", "mean"),
        )
        .reset_index()
    )
    grouped = grouped.merge(delay_summary, on="method", how="left")
    grouped = grouped.merge(batch_summary, on="method", how="left")
    grouped["dummy_overhead_fraction"] = (
        grouped["dummy_receipts"] / grouped["public_receipts"]
    )
    return grouped


def _dtn_summary(results_root: Path) -> pd.DataFrame:
    frames = []
    for context in DTN_CONTEXTS:
        path = _require(
            results_root / f"dtn_{context}" / "dtn_temporal_contact_summary.csv"
        )
        frame = pd.read_csv(path)
        frame.insert(0, "context", context.upper())
        frames.append(frame)
    return pd.concat(frames, ignore_index=True)


def _implementation_summary(results_root: Path) -> pd.DataFrame:
    zk = pd.read_csv(_require(results_root / "zk_p1.csv"))
    evm = pd.read_csv(_require(results_root / "evm_p2.csv"))
    p0 = pd.read_csv(_require(results_root / "crypto_p0.csv"))
    rows: list[dict[str, Any]] = []
    for metric in (
        "groth16_fullprove_ms",
        "groth16_fullprove_p95_ms",
        "groth16_fullprove_p99_ms",
        "evm_verifyProof_gas",
        "groth16_calldata_bytes",
        "constraint_count",
    ):
        record = zk[zk["metric"] == metric].iloc[0]
        rows.append(
            {
                "profile": "P1",
                "metric": metric,
                "value": record["value"],
                "unit": record["unit"],
            }
        )
    for operation in ("generation", "verification"):
        record = p0[p0["operation"] == operation].iloc[0]
        rows.append(
            {
                "profile": "P0",
                "metric": f"{operation}_median_ms",
                "value": record["median_ms"],
                "unit": "ms",
            }
        )
    deployment = evm[evm["case"] == "deployment"].iloc[0]
    rows.append(
        {
            "profile": "P2",
            "metric": "deployment_gas",
            "value": deployment["gas_total"],
            "unit": "gas",
        }
    )
    for _, record in evm[evm["case"] == "fresh_batch"].iterrows():
        rows.append(
            {
                "profile": "P2",
                "metric": f"gas_per_receipt_batch_{int(record['batch_size'])}",
                "value": record["gas_per_receipt"],
                "unit": "gas",
            }
        )
    return pd.DataFrame(rows)


def _registered_decisions(
    summaries: pd.DataFrame,
    contrasts: pd.DataFrame,
    frontier: pd.DataFrame,
) -> pd.DataFrame:
    safe = summaries[summaries["method"].str.startswith("aidmesh_")]
    no_cap_violation = bool((safe["exposure_cap_ratio"] <= 1.0).all())
    no_quorum_conflict = bool((safe["conflicting_quorum_certificates"] == 0).all())
    rights_conserved = bool(safe["risk_rights_conserved"].all())
    rows = []
    for comparison_id in (
        "RQ1_TRANSFER_VS_ONLINE_CORE",
        "RQ1_RMST_TRANSFER_VS_ONLINE_CORE",
        "RQ1_TRANSFER_VS_ONLINE_WIT06_BOUNDARY",
        "RQ1_TRANSFER_VS_ONLINE_WIT07",
        "RQ1_TRANSFER_VS_ONLINE_P06",
        "RQ2_TRANSFER_VS_STATIC_CORE",
    ):
        selected = contrasts[contrasts["comparison_id"] == comparison_id]
        for _, record in selected.iterrows():
            practical = (
                record["metric"] in {"completion_rate", "non_home_completion_rate"}
                and record["ci_low"] > 0.05
                and record["holm_adjusted_p_value"] < 0.05
            )
            rows.append(
                {
                    "estimand": f"{comparison_id}:{record['metric']}",
                    "mean_difference": record["mean_difference"],
                    "ci_low": record["ci_low"],
                    "ci_high": record["ci_high"],
                    "adjusted_p_value": record["holm_adjusted_p_value"],
                    "decision": (
                        "supported_at_registered_margin"
                        if (
                            practical
                            and no_cap_violation
                            and no_quorum_conflict
                            and rights_conserved
                        )
                        else "not_supported_at_registered_margin"
                    ),
                }
            )
    for _, record in frontier.iterrows():
        rows.append(
            {
                "estimand": f"RQ2_HYPERVOLUME:{record['quorum_family']}",
                "mean_difference": record["mean_difference"],
                "ci_low": record["ci_low"],
                "ci_high": record["ci_high"],
                "adjusted_p_value": record["holm_adjusted_p_value"],
                "decision": (
                    "supported_positive_frontier_gain"
                    if record["ci_low"] > 0
                    and record["holm_adjusted_p_value"] < 0.05
                    and no_cap_violation
                    and rights_conserved
                    else "not_supported"
                ),
            }
        )
    return pd.DataFrame(rows)


def _write_figures(
    output: Path,
    *,
    contrasts: pd.DataFrame,
    frontier_points: pd.DataFrame,
    privacy: pd.DataFrame,
    allocation: pd.DataFrame,
    dtn: pd.DataFrame,
    evm: pd.DataFrame,
) -> list[Path]:
    paths: list[Path] = []
    plt.rcParams.update({"font.size": 9, "figure.facecolor": "white"})

    def save_publication_figure(fig: Any, stem: str) -> None:
        """Write a review PNG and a vector PDF from the same plotted evidence."""

        png_path = output / f"{stem}.png"
        pdf_path = output / f"{stem}.pdf"
        fig.savefig(png_path, dpi=240)
        fig.savefig(pdf_path, bbox_inches="tight")
        paths.extend((png_path, pdf_path))

    selected_ids = [
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
    effect = contrasts[
        contrasts["comparison_id"].isin(selected_ids) & (contrasts["metric"] == "completion_rate")
    ].copy()
    effect["comparison_id"] = pd.Categorical(
        effect["comparison_id"], categories=selected_ids, ordered=True
    )
    effect = effect.sort_values("comparison_id")
    y = np.arange(len(effect))
    fig, axis = plt.subplots(figsize=(8.0, 4.7), constrained_layout=True)
    axis.errorbar(
        effect["mean_difference"],
        y,
        xerr=np.vstack(
            [
                effect["mean_difference"] - effect["ci_low"],
                effect["ci_high"] - effect["mean_difference"],
            ]
        ),
        fmt="o",
        capsize=3,
        color="#1d4ed8",
    )
    axis.axvline(0, color="black", linewidth=0.8)
    axis.axvline(0.05, color="#b91c1c", linestyle="--", linewidth=1)
    effect_labels = [
        str(value).replace("RQ1_TRANSFER_VS_ONLINE_", "")
        for value in effect["comparison_id"]
    ]
    axis.set_yticks(y, effect_labels)
    axis.set_xlabel("Paired completion difference (transferable - online)")
    axis.grid(axis="x", alpha=0.25)
    save_publication_figure(fig, "rq1_completion_effects")
    plt.close(fig)

    fig, axes = plt.subplots(1, 3, figsize=(10.5, 3.5), sharey=True, constrained_layout=True)
    for axis, family in zip(axes, sorted(frontier_points["quorum_family"].unique()), strict=True):
        subset = frontier_points[
            (frontier_points["quorum_family"] == family)
            & frontier_points["method"].isin(
                ["aidmesh_static_rights", "aidmesh_transferable_rights"]
            )
        ]
        for method, color, marker in (
            ("aidmesh_static_rights", "#64748b", "s"),
            ("aidmesh_transferable_rights", "#dc2626", "o"),
        ):
            group = subset[subset["method"] == method].sort_values("cap")
            axis.plot(
                group["exposure_norm_mean"],
                group["completion_mean"],
                marker=marker,
                color=color,
                label=METHOD_LABELS[method],
            )
        axis.set_title(family)
        axis.set_xlabel("Duplicate exposure / maximum registered cap")
        axis.grid(alpha=0.25)
    axes[0].set_ylabel("Deadline completion")
    axes[-1].legend(frameon=False, loc="lower right")
    save_publication_figure(fig, "rq2_frontier")
    plt.close(fig)

    transfer_privacy = privacy[privacy["method"] == "aidmesh_transferable_rights"]
    fig, axis = plt.subplots(figsize=(5.8, 3.7), constrained_layout=True)
    axis.bar(
        transfer_privacy["vantage"],
        transfer_privacy["min_anonymity_set_mean"],
        color=["#2563eb", "#f59e0b", "#dc2626"],
    )
    axis.set_yscale("log")
    axis.set_ylabel("Mean minimum anonymity set (log scale)")
    axis.set_xlabel("Observer vantage")
    axis.grid(axis="y", alpha=0.25)
    save_publication_figure(fig, "rq6_privacy_vantage")
    plt.close(fig)

    fig, axis = plt.subplots(figsize=(6.2, 4.0), constrained_layout=True)
    axis.scatter(
        allocation["utility_ratio_mean"],
        allocation["minimum_service_ratio_mean"],
        s=55,
        color="#2563eb",
    )
    for _, row in allocation.iterrows():
        axis.annotate(row["label"], (row["utility_ratio_mean"], row["minimum_service_ratio_mean"]))
    axis.axvline(0.97, color="#b91c1c", linestyle="--", linewidth=1)
    axis.set_xlabel("Conversion-adjusted utility ratio")
    axis.set_ylabel("Mean minimum service ratio")
    axis.grid(alpha=0.25)
    save_publication_figure(fig, "allocation_tradeoff")
    plt.close(fig)

    fig, axis = plt.subplots(figsize=(8.0, 4.0), constrained_layout=True)
    width = 0.2
    contexts = list(dict.fromkeys(dtn["context"]))
    x = np.arange(len(contexts))
    for index, policy in enumerate(DTN_POLICIES):
        values = [
            float(
                dtn[(dtn["context"] == context) & (dtn["policy"] == policy)][
                    "delivery_ratio_mean"
                ].iloc[0]
            )
            for context in contexts
        ]
        axis.bar(x + (index - 1.5) * width, values, width, label=policy.replace("_", " "))
    axis.set_xticks(x, contexts)
    axis.set_ylim(0, 1)
    axis.set_ylabel("Delivery ratio")
    axis.set_xlabel("SocioPatterns context")
    axis.legend(frameon=False, ncol=2)
    axis.grid(axis="y", alpha=0.25)
    save_publication_figure(fig, "dtn_external_delivery")
    plt.close(fig)

    batches = evm[evm["case"] == "fresh_batch"]
    fig, axis = plt.subplots(figsize=(5.8, 3.7), constrained_layout=True)
    axis.plot(batches["batch_size"], batches["gas_per_receipt"], marker="o", color="#7c3aed")
    axis.set_xlabel("Batch size")
    axis.set_ylabel("Local EVM gas per receipt")
    axis.grid(alpha=0.25)
    save_publication_figure(fig, "evm_batch_gas")
    plt.close(fig)
    return paths


def _write_cross_layer_evidence_figures(results_root: Path, output: Path) -> list[Path]:
    """Plot formal negative controls and external-validity challenges.

    These figures summarize already completed standalone evidence.  They do not
    create new estimands or reinterpret the frozen confirmatory contrasts.
    """

    paths: list[Path] = []
    plt.rcParams.update({"font.size": 9, "figure.facecolor": "white"})

    formal = json.loads(
        _require(results_root / "formal" / "bounded_formal_evidence.json").read_text(
            encoding="utf-8"
        )
    )
    fault = json.loads(
        _require(results_root / "fault_campaign" / "fault_campaign.json").read_text(
            encoding="utf-8"
        )
    )
    fixback_root = results_root.parent / "fixback"
    durable = json.loads(
        _require(fixback_root / "durable_witness" / "durable_witness_summary.json").read_text(
            encoding="utf-8"
        )
    )
    rights = json.loads(
        _require(fixback_root / "rights_property" / "rights_property_summary.json").read_text(
            encoding="utf-8"
        )
    )

    labels = (
        "Safe TLC\ninvariants",
        "Unsafe quorum\ncounterexample",
        "Native faults\nfail closed",
        "SQLite restart\ntrials",
        "Escrow random\nsequences",
    )
    checked = np.array(
        [
            144_937,
            28_193,
            len(fault["results"]),
            durable["trials"],
            rights["sequences"],
        ],
        dtype=float,
    )
    expected = np.array([1, 1, 7, 100, 10_000], dtype=float)
    observed = np.array(
        [
            1 if formal["safe_quorum"]["counterexample"] is None else 0,
            1 if formal["unsafe_quorum_negative_control"]["counterexample"] else 0,
            sum(result["safety_preserved"] for result in fault["results"]),
            durable["conflict_rejections"],
            rights["sequences"] - rights["conservation_violations"],
        ],
        dtype=float,
    )
    ratios = observed / expected

    fig, axes = plt.subplots(
        1,
        2,
        figsize=(9.2, 3.8),
        gridspec_kw={"width_ratios": [1.25, 1]},
        constrained_layout=True,
    )
    y = np.arange(len(labels))
    axes[0].barh(y, checked, color=["#2563eb", "#dc2626", "#0f766e", "#7c3aed", "#d97706"])
    axes[0].set_xscale("log")
    axes[0].set_yticks(y, labels)
    axes[0].invert_yaxis()
    axes[0].set_xlabel("States, injections, trials, or sequences (log scale)")
    axes[0].grid(axis="x", alpha=0.25)
    for index, value in enumerate(checked):
        axes[0].text(value * 1.08, index, f"{int(value):,}", va="center", fontsize=8)

    colors = ["#15803d" if np.isclose(value, 1.0) else "#dc2626" for value in ratios]
    axes[1].barh(y, ratios, color=colors)
    axes[1].set_yticks(y, [])
    axes[1].invert_yaxis()
    axes[1].set_xlim(0, 1.08)
    axes[1].set_xlabel("Expected safety outcome reproduced")
    axes[1].axvline(1, color="black", linewidth=0.8)
    axes[1].grid(axis="x", alpha=0.25)
    for index, (value, numerator, denominator) in enumerate(
        zip(ratios, observed, expected, strict=True)
    ):
        axes[1].text(
            min(value + 0.02, 1.02),
            index,
            f"{int(numerator):,}/{int(denominator):,}",
            va="center",
            fontsize=8,
        )
    axes[0].set_title("(a) Exploration and fault evidence")
    axes[1].set_title("(b) Registered/expected outcome")
    for suffix in ("png", "pdf"):
        path = output / f"formal_fault_evidence.{suffix}"
        fig.savefig(path, dpi=240 if suffix == "png" else None, bbox_inches="tight")
        paths.append(path)
    plt.close(fig)

    calibration = pd.read_csv(
        _require(
            fixback_root
            / "abstract_delay_calibration"
            / "abstract_delay_calibration_summary.csv"
        )
    )
    external = pd.read_csv(
        _require(
            results_root.parent
            / "external_dtn"
            / "the_one_invs13"
            / "comparison.csv"
        )
    )
    fig, axes = plt.subplots(1, 2, figsize=(9.2, 3.7), constrained_layout=True)
    x = np.arange(len(calibration))
    axes[0].bar(x - 0.18, calibration["ks_delay_hours"], 0.36, label="KS distance")
    axes[0].bar(
        x + 0.18,
        calibration["wasserstein_delay_hours"] / 10.0,
        0.36,
        label="Wasserstein / 10 h",
    )
    axes[0].set_xticks(
        x,
        [
            value.replace("_", "\n")
            for value in calibration["scenario_id"]
        ],
    )
    axes[0].set_ylabel("Distribution discrepancy")
    axes[0].set_title("(a) Abstract vs temporal-contact delay")
    axes[0].legend(frameon=False, fontsize=8)
    axes[0].grid(axis="y", alpha=0.25)

    implementations = ["AidMesh", "The ONE"]
    delivery = external["delivery_ratio"].to_numpy(dtype=float)
    bars = axes[1].bar(implementations, delivery, color=["#2563eb", "#dc2626"])
    axes[1].set_ylim(0, 0.7)
    axes[1].set_ylabel("Delivery ratio")
    axes[1].set_title("(b) External Epidemic cross-check")
    axes[1].grid(axis="y", alpha=0.25)
    for bar, delivered, bundles, dropped in zip(
        bars,
        external["delivered"],
        external["bundles"],
        external["dropped"],
        strict=True,
    ):
        axes[1].text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 0.025,
            f"{int(delivered)}/{int(bundles)}\nreported drops={int(dropped):,}",
            ha="center",
            va="bottom",
            fontsize=8,
        )
    for suffix in ("png", "pdf"):
        path = output / f"external_validity_challenges.{suffix}"
        fig.savefig(path, dpi=240 if suffix == "png" else None, bbox_inches="tight")
        paths.append(path)
    plt.close(fig)
    return paths


def _latex_escape(value: str) -> str:
    return value.replace("_", r"\_").replace("%", r"\%")


def _write_latex_tables(
    output: Path,
    *,
    core: pd.DataFrame,
    frontier: pd.DataFrame,
    privacy: pd.DataFrame,
    allocation: pd.DataFrame,
    implementation: pd.DataFrame,
) -> list[Path]:
    paths: list[Path] = []

    lines = [
        r"\begin{tabular}{lrrrr}",
        r"\toprule",
        r"Method & Completion (95\% BCa CI) & Exposure/cap & RMST (h) & Bytes/settlement \\",
        r"\midrule",
    ]
    for _, row in core.iterrows():
        lines.append(
            f"{_latex_escape(row['label'])} & "
            f"{row['completion_mean']:.3f} [{row['completion_ci_low']:.3f}, "
            f"{row['completion_ci_high']:.3f}] & "
            f"{row['exposure_cap_ratio_mean']:.3f} & {row['rmst_hours_mean']:.2f} & "
            f"{row['bytes_per_settlement_mean']:.0f} \\\\"
        )
    lines.extend([r"\bottomrule", r"\end{tabular}"])
    path = output / "table_core_results.tex"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    paths.append(path)

    lines = [
        r"\begin{tabular}{lrrrr}",
        r"\toprule",
        r"Quorum & Transfer HV & Static HV & Difference (95\% BCa CI) & Holm $p$ \\",
        r"\midrule",
    ]
    for _, row in frontier.iterrows():
        lines.append(
            f"{row['quorum_family']} & {row['mean_hypervolume_treatment']:.4f} & "
            f"{row['mean_hypervolume_control']:.4f} & {row['mean_difference']:.4f} "
            f"[{row['ci_low']:.4f}, {row['ci_high']:.4f}] & "
            f"{row['holm_adjusted_p_value']:.4g} \\\\"
        )
    lines.extend([r"\bottomrule", r"\end{tabular}"])
    path = output / "table_frontier.tex"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    paths.append(path)

    transfer = privacy[privacy["method"] == "aidmesh_transferable_rights"]
    lines = [
        r"\begin{tabular}{lrrrr}",
        r"\toprule",
        r"Vantage & Min. anonymity & Posterior entropy & Top-1 success & Added delay (h) \\",
        r"\midrule",
    ]
    for _, row in transfer.iterrows():
        lines.append(
            f"{_latex_escape(row['vantage'].title())} & "
            f"{row['min_anonymity_set_mean']:.2f} & "
            f"{row['posterior_entropy_bits_mean']:.2f} & "
            f"{row['attacker_top1_success_mean']:.4f} & "
            f"{row['median_added_delay_hours_mean']:.2f} \\\\"
        )
    lines.extend([r"\bottomrule", r"\end{tabular}"])
    path = output / "table_privacy.tex"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    paths.append(path)

    lines = [
        r"\begin{tabular}{lrrr}",
        r"\toprule",
        r"Allocator & Minimum service & Utility ratio & Mean Gini \\",
        r"\midrule",
    ]
    for _, row in allocation.sort_values("minimum_service_ratio_mean").iterrows():
        lines.append(
            f"{_latex_escape(row['label'])} & {row['minimum_service_ratio_mean']:.3f} & "
            f"{row['utility_ratio_mean']:.3f} & {row['gini_mean']:.3f} \\\\"
        )
    lines.extend([r"\bottomrule", r"\end{tabular}"])
    path = output / "table_allocation.tex"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    paths.append(path)

    lookup = implementation.set_index(["profile", "metric"])["value"]
    lines = [
        r"\begin{tabular}{lr}",
        r"\toprule",
        r"Metric & Measured value \\",
        r"\midrule",
        f"P1 constraints & {int(float(lookup[('P1', 'constraint_count')])):,} \\\\",
        f"P1 median fullprove & {float(lookup[('P1', 'groth16_fullprove_ms')]):.1f} ms \\\\",
        f"P1 p95 fullprove & {float(lookup[('P1', 'groth16_fullprove_p95_ms')]):.1f} ms \\\\",
        f"P1 calldata & {int(float(lookup[('P1', 'groth16_calldata_bytes')])):,} bytes \\\\",
        f"P1 local verifyProof & {int(float(lookup[('P1', 'evm_verifyProof_gas')])):,} gas \\\\",
        f"P2 local deployment & {int(float(lookup[('P2', 'deployment_gas')])):,} gas \\\\",
        f"P2 batch-100 & "
        f"{float(lookup[('P2', 'gas_per_receipt_batch_100')]):,.0f} gas/receipt \\\\",
        r"\bottomrule",
        r"\end{tabular}",
    ]
    path = output / "table_implementation.tex"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    paths.append(path)
    return paths


def write_paper_assets(results_root: Path, output: Path) -> dict[str, Path]:
    """Generate paper-ready CSV, LaTeX, figures, and a hash manifest."""

    results_root = results_root.resolve()
    output.mkdir(parents=True, exist_ok=True)
    inputs = {
        "summaries": _require(
            results_root / "analysis" / "confirmatory_run_summaries.csv"
        ),
        "contrasts": _require(
            results_root / "analysis" / "confirmatory_registered_contrasts.csv"
        ),
        "frontier_points": _require(
            results_root / "frontier" / "frontier_point_summary.csv"
        ),
        "frontier_contrasts": _require(
            results_root / "frontier" / "frontier_hypervolume_contrasts.csv"
        ),
        "allocation": _require(results_root / "allocation.csv"),
        "zk": _require(results_root / "zk_p1.csv"),
        "evm": _require(results_root / "evm_p2.csv"),
        "p0": _require(results_root / "crypto_p0.csv"),
        "privacy_metrics": _require(
            results_root / "privacy_batch" / "privacy_metrics.csv"
        ),
        "privacy_delays": _require(results_root / "privacy_batch" / "delay_costs.csv"),
        "privacy_batches": _require(
            results_root / "privacy_batch" / "batch_manifest.csv"
        ),
    }
    for context in DTN_CONTEXTS:
        inputs[f"dtn_{context}"] = _require(
            results_root / f"dtn_{context}" / "dtn_temporal_contact_summary.csv"
        )

    summaries = pd.read_csv(inputs["summaries"])
    contrasts = pd.read_csv(inputs["contrasts"])
    frontier_points = pd.read_csv(inputs["frontier_points"])
    frontier = pd.read_csv(inputs["frontier_contrasts"])
    if "holm_adjusted_p_value" not in frontier:
        adjusted = multipletests(frontier["permutation_p_value_greater"], method="holm")[1]
        frontier["holm_adjusted_p_value"] = adjusted
    allocation_raw = pd.read_csv(inputs["allocation"])
    evm = pd.read_csv(inputs["evm"])

    core = _core_summary(summaries)
    allocation, allocation_contrasts = _allocation_assets(allocation_raw)
    privacy = _privacy_summary(results_root)
    dtn = _dtn_summary(results_root)
    implementation = _implementation_summary(results_root)
    decisions = _registered_decisions(summaries, contrasts, frontier)

    frames = {
        "core_summary": core,
        "registered_contrasts": contrasts,
        "frontier_contrasts": frontier,
        "allocation_summary": allocation,
        "allocation_contrasts": allocation_contrasts,
        "privacy_summary": privacy,
        "dtn_summary": dtn,
        "implementation_summary": implementation,
        "registered_decisions": decisions,
    }
    assets: dict[str, Path] = {}
    for name, frame in frames.items():
        path = output / f"{name}.csv"
        frame.to_csv(path, index=False)
        assets[name] = path

    figures = _write_figures(
        output,
        contrasts=contrasts,
        frontier_points=frontier_points,
        privacy=privacy,
        allocation=allocation,
        dtn=dtn,
        evm=evm,
    )
    figures.extend(_write_cross_layer_evidence_figures(results_root, output))
    for path in figures:
        assets[f"{path.stem}_{path.suffix.lstrip('.')}"] = path
    tables = _write_latex_tables(
        output,
        core=core,
        frontier=frontier,
        privacy=privacy,
        allocation=allocation,
        implementation=implementation,
    )
    for path in tables:
        assets[path.stem] = path

    manifest_path = output / "paper_assets_manifest.json"
    checksums_path = output / "CHECKSUMS.txt"
    manifest = {
        "schema_version": "1.0.0",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "analysis_class": (
            "presentation-only aggregation; registered RQ1/RQ2 contrasts are copied unchanged; "
            "allocation statistics are supporting and were not frozen primary estimands"
        ),
        "input_sha256": {
            name: {"path": str(path.relative_to(results_root)), "sha256": _sha256(path)}
            for name, path in sorted(inputs.items())
        },
        "output_sha256": {
            name: {"path": path.name, "sha256": _sha256(path)}
            for name, path in sorted(assets.items())
        },
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    assets["manifest"] = manifest_path
    checksums_path.write_text(
        "\n".join(
            f"{_sha256(path)}  {path.name}"
            for path in sorted(assets.values(), key=lambda item: item.name)
        )
        + "\n",
        encoding="utf-8",
    )
    assets["checksums"] = checksums_path
    return assets
