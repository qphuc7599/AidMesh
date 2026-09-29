"""Run outcome-blind jitter, link-loss, bandwidth, and inclusion sensitivities."""

from __future__ import annotations

import argparse
import hashlib
import json
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import matplotlib
import numpy as np
import pandas as pd

from aidmesh.experiments.bow_temporal import run_bow_temporal_campaign

matplotlib.use("Agg")
matplotlib.rcParams.update({"pdf.fonttype": 42, "ps.fonttype": 42})
import matplotlib.pyplot as plt  # noqa: E402

TRACE_ROOT = Path("external/asturies_wildfires/normalized")
PRIMARY_MANIFEST = TRACE_ROOT / "family_manifest.json"
SCENARIOS: tuple[dict[str, Any], ...] = (
    {
        "scenario": "baseline",
        "temporal_jitter_us": 0,
        "loss_probability": 0.0,
        "contact_capacity_scale": 1.0,
    },
    {
        "scenario": "jitter_30s",
        "temporal_jitter_us": 30_000_000,
        "loss_probability": 0.0,
        "contact_capacity_scale": 1.0,
    },
    {
        "scenario": "jitter_300s",
        "temporal_jitter_us": 300_000_000,
        "loss_probability": 0.0,
        "contact_capacity_scale": 1.0,
    },
    {
        "scenario": "loss_10pct",
        "temporal_jitter_us": 0,
        "loss_probability": 0.10,
        "contact_capacity_scale": 1.0,
    },
    {
        "scenario": "loss_30pct",
        "temporal_jitter_us": 0,
        "loss_probability": 0.30,
        "contact_capacity_scale": 1.0,
    },
    {
        "scenario": "bandwidth_25pct",
        "temporal_jitter_us": 0,
        "loss_probability": 0.0,
        "contact_capacity_scale": 0.25,
    },
    {
        "scenario": "bandwidth_400pct",
        "temporal_jitter_us": 0,
        "loss_probability": 0.0,
        "contact_capacity_scale": 4.0,
    },
)
INCLUSION_RULES = (
    {
        "rule": "inclusive",
        "minimum_nodes": 4,
        "minimum_duration_hours": 1.0,
        "minimum_contacts_per_partition": 50,
    },
    {
        "rule": "registered_primary",
        "minimum_nodes": 8,
        "minimum_duration_hours": 2.0,
        "minimum_contacts_per_partition": 100,
    },
    {
        "rule": "stringent",
        "minimum_nodes": 10,
        "minimum_duration_hours": 4.0,
        "minimum_contacts_per_partition": 250,
    },
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _incident_from_context(context: str) -> int:
    return int(context.split("wf", 1)[1].split("_", 1)[0])


def _task(arguments: tuple[dict[str, Any], int, Path]) -> Path:
    scenario, incident, output = arguments
    context = f"asturies_wf{incident:02d}_r200"
    path = output / "shards" / scenario["scenario"] / f"{context}.csv"
    if path.is_file():
        return path
    frame, _ = run_bow_temporal_campaign(
        TRACE_ROOT,
        contexts=(context,),
        seeds=(9701,),
        cap_fractions=(0.15, 0.60),
        quorum_availabilities=(0.25, 0.90),
        policies=("robust_static", "adatransfer"),
        calibration_fraction=0.40,
        copy_budget=4,
        adatransfer_gate_mode="expected_value",
        oracle_policies=(),
        temporal_jitter_us=int(scenario["temporal_jitter_us"]),
        loss_probability=float(scenario["loss_probability"]),
        contact_capacity_scale=float(scenario["contact_capacity_scale"]),
        experiment_prefix=f"asturies-network-{scenario['scenario']}",
    )
    frame["scenario"] = scenario["scenario"]
    frame["incident"] = incident
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, lineterminator="\n")
    return path


def _cluster_summary(frame: pd.DataFrame) -> dict[str, float]:
    incident = frame.groupby("incident")["completion_rate_delta"].mean().to_numpy(float)
    rng = np.random.default_rng(20260904)
    draws = rng.choice(incident, size=(5000, len(incident)), replace=True).mean(axis=1)
    signs = rng.choice((-1.0, 1.0), size=(5000, len(incident)))
    centered = incident - incident.mean()
    wild = incident.mean() + np.mean(signs * centered, axis=1)
    return {
        "mean_completion_delta": float(incident.mean()),
        "ci95_low": float(np.quantile(draws, 0.025)),
        "ci95_high": float(np.quantile(draws, 0.975)),
        "wild_ci95_low": float(np.quantile(wild, 0.025)),
        "wild_ci95_high": float(np.quantile(wild, 0.975)),
    }


def _selection_rows() -> pd.DataFrame:
    manifest = json.loads(PRIMARY_MANIFEST.read_text(encoding="utf-8"))
    records = []
    for source in manifest["incidents"]:
        incident = _incident_from_context(source["context"])
        nodes = int(source["nodes_with_contacts"])
        prefix = int(source["calibration_contacts"])
        suffix = int(source["heldout_contacts"])
        duration_hours = float(source["duration_us"]) / 3_600_000_000
        for rule in INCLUSION_RULES:
            included = (
                nodes >= rule["minimum_nodes"]
                and duration_hours >= rule["minimum_duration_hours"]
                and prefix >= rule["minimum_contacts_per_partition"]
                and suffix >= rule["minimum_contacts_per_partition"]
            )
            records.append(
                {
                    "incident": incident,
                    "rule": rule["rule"],
                    "nodes": nodes,
                    "duration_hours": duration_hours,
                    "prefix_contacts": prefix,
                    "suffix_contacts": suffix,
                    "included": included,
                }
            )
    return pd.DataFrame(records)


def _plot(summary: pd.DataFrame, selection: pd.DataFrame, output: Path) -> None:
    order = [scenario["scenario"] for scenario in SCENARIOS]
    scenario_labels = {
        "baseline": "Baseline",
        "jitter_30s": "30 s jitter",
        "jitter_300s": "300 s jitter",
        "loss_10pct": "10% link loss",
        "loss_30pct": "30% link loss",
        "bandwidth_25pct": "0.25× bandwidth",
        "bandwidth_400pct": "4× bandwidth",
    }
    rule_labels = {
        "inclusive": "Inclusive",
        "registered_primary": "Registered primary",
        "stringent": "Stringent",
    }
    frame = summary.set_index("scenario").loc[order].reset_index()
    y = np.arange(len(frame))
    fig, axes = plt.subplots(1, 2, figsize=(10.5, 4.5), gridspec_kw={"width_ratios": [2, 1]})
    axes[0].errorbar(
        frame["mean_completion_delta"],
        y,
        xerr=np.vstack(
            (
                frame["mean_completion_delta"] - frame["ci95_low"],
                frame["ci95_high"] - frame["mean_completion_delta"],
            )
        ),
        fmt="o",
        color="#1261A0",
        capsize=3,
    )
    axes[0].axvline(0, color="black", linewidth=0.8)
    axes[0].set_yticks(y, [scenario_labels[value] for value in order])
    axes[0].invert_yaxis()
    axes[0].set_xlabel("AdaTransfer - robust-static completion")
    axes[0].set_title("Outcome-blind network sensitivities")
    counts = selection.groupby("rule")["included"].sum().reindex(
        [rule["rule"] for rule in INCLUSION_RULES]
    )
    axes[1].barh(np.arange(len(counts)), counts, color="#5B8C3A")
    axes[1].set_yticks(
        np.arange(len(counts)),
        [rule_labels.get(value, value.replace("_", " ")) for value in counts.index],
    )
    axes[1].invert_yaxis()
    axes[1].set_xlim(0, 30)
    axes[1].set_xlabel("Included incidents (of 30)")
    axes[1].set_title("Eligibility-rule sensitivity")
    for index, value in enumerate(counts):
        axes[1].text(value + 0.4, index, str(int(value)), va="center")
    fig.tight_layout()
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "results/elsevier_remediation_20260901/network_assumption_sensitivity_v2"
        ),
    )
    args = parser.parse_args()
    primary = json.loads(PRIMARY_MANIFEST.read_text(encoding="utf-8"))
    incidents = sorted(_incident_from_context(value) for value in primary["eligible_contexts"])
    tasks = [
        (scenario, incident, args.output)
        for scenario in SCENARIOS
        for incident in incidents
    ]
    paths = []
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(_task, task) for task in tasks]
        for future in as_completed(futures):
            paths.append(future.result())
    runs = pd.concat([pd.read_csv(path) for path in sorted(paths)], ignore_index=True)
    keys = ["scenario", "incident", "seed", "cap", "quorum_availability"]
    values = [
        "completion_rate",
        "exposure_per_1000_requests",
        "restricted_mean_latency_hours",
        "forwarded_bytes",
        "peak_buffer_bytes",
        "transfers_stranded",
    ]
    ada = runs.loc[runs["policy"] == "adatransfer", keys + values]
    static = runs.loc[runs["policy"] == "robust_static", keys + values]
    paired = ada.merge(static, on=keys, suffixes=("_ada", "_static"), validate="one_to_one")
    for value in values:
        paired[f"{value}_delta"] = paired[f"{value}_ada"] - paired[f"{value}_static"]
    summaries = []
    for scenario, subset in paired.groupby("scenario"):
        summaries.append(
            {
                "scenario": scenario,
                "incident_clusters": len(incidents),
                "paired_cells": len(subset),
                **_cluster_summary(subset),
                "mean_exposure_delta_per_1000": float(
                    subset["exposure_per_1000_requests_delta"].mean()
                ),
                "mean_stranded_transfer_delta": float(
                    subset["transfers_stranded_delta"].mean()
                ),
            }
        )
    summary = pd.DataFrame(summaries)
    selection = _selection_rows()
    args.output.mkdir(parents=True, exist_ok=True)
    runs_path = args.output / "network_assumption_runs.csv"
    pairs_path = args.output / "network_assumption_pairs.csv"
    summary_path = args.output / "network_assumption_effects.csv"
    selection_path = args.output / "eligibility_threshold_sensitivity.csv"
    figure_path = args.output / "network_assumption_sensitivity.pdf"
    manifest_path = args.output / "manifest.json"
    runs.to_csv(runs_path, index=False, lineterminator="\n")
    paired.to_csv(pairs_path, index=False, lineterminator="\n")
    summary.to_csv(summary_path, index=False, lineterminator="\n")
    selection.to_csv(selection_path, index=False, lineterminator="\n")
    _plot(summary, selection, figure_path)
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "primary_incidents": incidents,
                "seed": 9701,
                "scenarios": SCENARIOS,
                "inclusion_rules": INCLUSION_RULES,
                "paired_rows": len(paired),
                "interpretation": (
                    "One-factor, outcome-blind sensitivity; not a replacement confirmatory study."
                ),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    checksum_path = args.output / "CHECKSUMS.sha256"
    artifacts = (
        runs_path,
        pairs_path,
        summary_path,
        selection_path,
        figure_path,
        manifest_path,
    )
    checksum_path.write_text(
        "".join(f"{_sha256(path)}  {path.name}\n" for path in artifacts),
        encoding="ascii",
    )
    print(json.dumps({"runs": len(runs), "paired_rows": len(paired), "incidents": len(incidents)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
