"""Incident-clustered analysis for the frozen Asturies CATR campaign."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _cluster_bootstrap_mean(
    values: pd.DataFrame,
    *,
    cluster: str,
    value: str,
    repetitions: int,
    seed: int,
) -> tuple[float, float, float, np.ndarray]:
    cluster_means = values.groupby(cluster)[value].mean().to_numpy(dtype=float)
    if not len(cluster_means):
        raise ValueError("bootstrap input has no clusters")
    rng = np.random.default_rng(seed)
    draws = rng.choice(
        cluster_means,
        size=(repetitions, len(cluster_means)),
        replace=True,
    ).mean(axis=1)
    return (
        float(cluster_means.mean()),
        float(np.quantile(draws, 0.025)),
        float(np.quantile(draws, 0.975)),
        draws,
    )


def _paired(frame: pd.DataFrame, candidate: str) -> pd.DataFrame:
    keys = [
        "registered_context",
        "seed",
        "cell",
        "demand_drift",
        "cap_tightness",
        "witness_availability",
        "transfer_reliability",
        "contact_predictability",
        "buffer_provisioning",
        "cross_class_traffic",
        "recovery_timing",
    ]
    baseline = frame.loc[frame["policy"] == "optimized_static", keys + ["completion_rate"]].rename(
        columns={"completion_rate": "baseline_completion_rate"}
    )
    selected = frame.loc[
        frame["policy"] == candidate,
        keys
        + [
            "completion_rate",
            "transfers_started",
            "transfers_accepted",
            "transfers_reclaimed",
            "transfers_stranded",
        ],
    ]
    paired = selected.merge(baseline, on=keys, validate="one_to_one")
    paired["policy"] = candidate
    paired["completion_delta"] = paired["completion_rate"] - paired["baseline_completion_rate"]
    return paired


def _hypothesis_rows(
    factorial: pd.DataFrame, *, repetitions: int
) -> tuple[pd.DataFrame, dict[str, Any]]:
    records = []
    draws_by_name: dict[str, np.ndarray] = {}
    for index, (policy, drift) in enumerate(
        (
            ("catr", "stable"),
            ("catr", "shifting"),
            ("adatransfer", "stable"),
            ("adatransfer", "shifting"),
            ("robust_static", "stable"),
            ("robust_static", "shifting"),
            ("reclaimable_transfer", "stable"),
            ("reclaimable_transfer", "shifting"),
        )
    ):
        paired = _paired(factorial, policy)
        subset = paired.loc[paired["demand_drift"] == drift]
        estimate, lower, upper, draws = _cluster_bootstrap_mean(
            subset,
            cluster="registered_context",
            value="completion_delta",
            repetitions=repetitions,
            seed=202608220 + index,
        )
        name = f"{policy}:{drift}"
        draws_by_name[name] = draws
        records.append(
            {
                "policy": policy,
                "demand_drift": drift,
                "incident_clusters": subset["registered_context"].nunique(),
                "paired_cells": len(subset),
                "mean_completion_delta": estimate,
                "ci95_low": lower,
                "ci95_high": upper,
                "beneficial_cell_fraction": float((subset["completion_delta"] > 0.01).mean()),
                "neutral_cell_fraction": float((subset["completion_delta"].abs() <= 0.01).mean()),
                "harmful_cell_fraction": float((subset["completion_delta"] < -0.01).mean()),
            }
        )
    effects = pd.DataFrame(records)
    catr = factorial.loc[factorial["policy"] == "catr"]
    ada = factorial.loc[factorial["policy"] == "adatransfer"]
    rights_rows = factorial.loc[
        factorial["policy"].isin(
            ["optimized_static", "robust_static", "adatransfer", "reclaimable_transfer", "catr"]
        )
    ]
    ada_stranded = int(ada["transfers_stranded"].sum())
    catr_stranded = int(catr["transfers_stranded"].sum())
    summary = {
        "h1_stable_noninferiority": {
            "margin": -0.01,
            "estimate": float(
                effects.loc[
                    (effects["policy"] == "catr") & (effects["demand_drift"] == "stable"),
                    "mean_completion_delta",
                ].iloc[0]
            ),
            "lower_95": float(
                effects.loc[
                    (effects["policy"] == "catr") & (effects["demand_drift"] == "stable"),
                    "ci95_low",
                ].iloc[0]
            ),
            "passed": bool(
                effects.loc[
                    (effects["policy"] == "catr") & (effects["demand_drift"] == "stable"),
                    "ci95_low",
                ].iloc[0]
                > -0.01
            ),
        },
        "h2_shifting_superiority": {
            "margin": 0.0,
            "estimate": float(
                effects.loc[
                    (effects["policy"] == "catr") & (effects["demand_drift"] == "shifting"),
                    "mean_completion_delta",
                ].iloc[0]
            ),
            "lower_95": float(
                effects.loc[
                    (effects["policy"] == "catr") & (effects["demand_drift"] == "shifting"),
                    "ci95_low",
                ].iloc[0]
            ),
            "passed": bool(
                effects.loc[
                    (effects["policy"] == "catr") & (effects["demand_drift"] == "shifting"),
                    "ci95_low",
                ].iloc[0]
                > 0.0
            ),
        },
        "h3_stranding_reduction": {
            "catr_final_locks": catr_stranded,
            "adatransfer_final_locks": ada_stranded,
            "ratio": catr_stranded / max(1, ada_stranded),
            "passed": catr_stranded / max(1, ada_stranded) <= 0.25,
            "interpretation_guard": (
                "CATR initiated no transfers; this is abstention, not successful recovery."
            ),
        },
        "h4_safety": {
            "cap_violations": int(rights_rows["risk_cap_violation"].sum()),
            "conservation_failures": int(
                (~rights_rows["rights_conservation_holds"].astype(bool)).sum()
            ),
            "passed": bool(
                rights_rows["risk_cap_violation"].sum() == 0
                and rights_rows["rights_conservation_holds"].astype(bool).all()
            ),
        },
        "activation": {
            "catr_started": int(catr["transfers_started"].sum()),
            "catr_positive_margin_decisions": int(catr["transfer_positive_margin_decisions"].sum()),
            "catr_candidate_decisions": int(catr["transfer_candidate_decisions"].sum()),
        },
    }
    return effects, summary


def _write_plots(effects: pd.DataFrame, factorial: pd.DataFrame, output: Path) -> list[Path]:
    output.mkdir(parents=True, exist_ok=True)
    policies = ["catr", "adatransfer", "robust_static", "reclaimable_transfer"]
    labels = {
        "catr": "CATR",
        "adatransfer": "AdaTransfer",
        "robust_static": "Robust static",
        "reclaimable_transfer": "Reclaimable transfer",
    }
    figure, axis = plt.subplots(figsize=(7.2, 3.8))
    positions = np.arange(len(policies))
    for offset, drift in ((-0.16, "stable"), (0.16, "shifting")):
        subset = effects.set_index(["policy", "demand_drift"])
        means = np.array(
            [subset.loc[policy, drift]["mean_completion_delta"] for policy in policies]
        )
        lower = np.array([subset.loc[policy, drift]["ci95_low"] for policy in policies])
        upper = np.array([subset.loc[policy, drift]["ci95_high"] for policy in policies])
        axis.errorbar(
            positions + offset,
            means,
            yerr=np.vstack((means - lower, upper - means)),
            fmt="o",
            capsize=3,
            label=drift.capitalize(),
        )
    axis.axhline(0, color="black", linewidth=0.8)
    axis.axhline(-0.01, color="gray", linewidth=0.8, linestyle="--")
    axis.set_xticks(positions, [labels[policy] for policy in policies], rotation=12)
    axis.set_ylabel("Completion delta vs optimized static")
    axis.legend(frameon=False, ncol=2)
    figure.tight_layout()
    effect_path = output / "catr_confirmatory_effects.pdf"
    figure.savefig(effect_path, bbox_inches="tight")
    plt.close(figure)

    transfer = (
        factorial.loc[factorial["policy"].isin(policies)]
        .groupby("policy", as_index=False)
        .agg(
            accepted=("transfers_accepted", "sum"),
            reclaimed=("transfers_reclaimed", "sum"),
            locked=("transfers_stranded", "sum"),
        )
        .set_index("policy")
        .reindex(policies)
        .fillna(0)
    )
    figure, axis = plt.subplots(figsize=(7.2, 3.8))
    bottom = np.zeros(len(policies))
    for column, color in (
        ("accepted", "#2b8cbe"),
        ("reclaimed", "#7bccc4"),
        ("locked", "#d7301f"),
    ):
        values = transfer[column].to_numpy(dtype=float)
        axis.bar(positions, values, bottom=bottom, label=column.capitalize(), color=color)
        bottom += values
    axis.set_xticks(positions, [labels[policy] for policy in policies], rotation=12)
    axis.set_ylabel("Transfer terminal outcomes (count)")
    axis.legend(frameon=False, ncol=3)
    figure.tight_layout()
    transfer_path = output / "catr_transfer_terminalization.pdf"
    figure.savefig(transfer_path, bbox_inches="tight")
    plt.close(figure)
    return [effect_path, transfer_path]


def write_catr_confirmatory_analysis(
    primary_path: Path,
    factorial_path: Path,
    output: Path,
    *,
    repetitions: int = 5000,
) -> dict[str, Path]:
    primary = pd.read_csv(primary_path)
    factorial = pd.read_csv(factorial_path)
    effects, hypotheses = _hypothesis_rows(factorial, repetitions=repetitions)
    output.mkdir(parents=True, exist_ok=True)
    effects_path = output / "incident_clustered_effects.csv"
    effects.to_csv(effects_path, index=False, lineterminator="\n")
    mechanism = factorial.groupby(["demand_drift", "policy"], as_index=False).agg(
        runs=("completed", "size"),
        mean_completion_rate=("completion_rate", "mean"),
        transfers_started=("transfers_started", "sum"),
        transfers_delivered=("transfers_delivered", "sum"),
        transfers_accepted=("transfers_accepted", "sum"),
        transfers_reclaimed=("transfers_reclaimed", "sum"),
        transfers_final_locked=("transfers_stranded", "sum"),
        cap_violations=("risk_cap_violation", "sum"),
    )
    mechanism_path = output / "mechanism_summary.csv"
    mechanism.to_csv(mechanism_path, index=False, lineterminator="\n")
    incident_effects = pd.concat(
        [
            _paired(factorial, policy)
            for policy in ("catr", "adatransfer", "robust_static", "reclaimable_transfer")
        ],
        ignore_index=True,
    )
    incident_effects = incident_effects.groupby(
        ["registered_context", "policy", "demand_drift"], as_index=False
    )["completion_delta"].mean()
    incident_effects["regime_class"] = np.select(
        (
            incident_effects["completion_delta"] < -0.01,
            incident_effects["completion_delta"] > 0.01,
        ),
        ("harmful", "beneficial"),
        default="neutral",
    )
    incident_path = output / "incident_regime_effects.csv"
    incident_effects.to_csv(incident_path, index=False, lineterminator="\n")
    plots = _write_plots(effects, factorial, output)
    hypotheses["campaign_integrity"] = {
        "primary_rows": len(primary),
        "factorial_rows": len(factorial),
        "incidents": factorial["registered_context"].nunique(),
        "factorial_cells": factorial["cell"].nunique(),
        "factorial_seeds": factorial["seed"].nunique(),
    }
    report_path = output / "hypothesis_report.json"
    report_path.write_text(
        json.dumps(hypotheses, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    assets = [effects_path, mechanism_path, incident_path, report_path, *plots]
    checksums = output / "CHECKSUMS.sha256"
    checksums.write_text(
        "".join(f"{_sha256(path)}  {path.name}\n" for path in assets),
        encoding="ascii",
    )
    return {path.stem: path for path in (*assets, checksums)}
