"""Run matched shared-queue routing-algorithm sensitivity at 200 m."""

from __future__ import annotations

import argparse
import hashlib
import json
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

from aidmesh.experiments.bow_temporal import run_bow_temporal_campaign
from aidmesh.simulator.dtn import RoutingPolicy

ROUTING_POLICIES = (
    RoutingPolicy.FIFO,
    RoutingPolicy.EPIDEMIC,
    RoutingPolicy.SPRAY_WAIT,
    RoutingPolicy.RISK_PRIORITY,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _task(arguments: tuple[RoutingPolicy, int, Path]) -> Path:
    routing, incident, output = arguments
    context = f"asturies_wf{incident:02d}_r200"
    path = output / "shards" / f"{context}_{routing.value}.csv"
    if path.is_file():
        return path
    frame, _ = run_bow_temporal_campaign(
        Path("external/asturies_wildfires/normalized"),
        contexts=(context,),
        seeds=(9601,),
        cap_fractions=(0.15, 0.60),
        quorum_availabilities=(0.25, 0.90),
        policies=("robust_static", "adatransfer"),
        calibration_fraction=0.40,
        copy_budget=4,
        routing_policy=routing,
        adatransfer_gate_mode="expected_value",
        conservative_protection_fraction=0.10,
        oracle_policies=(),
        experiment_prefix=f"asturies-routing-algorithm-{routing.value}",
    )
    frame["routing_policy"] = routing.value
    frame["incident"] = incident
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, lineterminator="\n")
    return path


def _interval(frame: pd.DataFrame) -> tuple[float, float, float]:
    means = frame.groupby("incident")["completion_rate_delta"].mean().to_numpy(dtype=float)
    rng = np.random.default_rng(20260903)
    draws = rng.choice(means, size=(5000, len(means)), replace=True).mean(axis=1)
    return float(means.mean()), float(np.quantile(draws, 0.025)), float(np.quantile(draws, 0.975))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "results/elsevier_remediation_20260901/routing_algorithm_sensitivity"
        ),
    )
    args = parser.parse_args()
    range_manifest = json.loads(
        Path(
            "results/elsevier_remediation_20260901/range_sensitivity/r10/family_manifest.json"
        ).read_text(encoding="utf-8")
    )
    incidents = sorted(
        int(context.split("wf", 1)[1].split("_", 1)[0])
        for context in range_manifest["eligible_contexts"]
    )
    tasks = [
        (routing, incident, args.output)
        for routing in ROUTING_POLICIES
        for incident in incidents
    ]
    paths = []
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(_task, task) for task in tasks]
        for future in as_completed(futures):
            paths.append(future.result())

    runs = pd.concat([pd.read_csv(path) for path in sorted(paths)], ignore_index=True)
    keys = ["routing_policy", "incident", "seed", "cap", "quorum_availability"]
    values = [
        "completion_rate",
        "exposure_per_1000_requests",
        "forwarded_bytes",
        "peak_buffer_bytes",
        "transfers_stranded",
    ]
    ada = runs.loc[runs["policy"] == "adatransfer", keys + values]
    static = runs.loc[runs["policy"] == "robust_static", keys + values]
    paired = ada.merge(static, on=keys, suffixes=("_ada", "_static"), validate="one_to_one")
    for value in values:
        paired[f"{value}_delta"] = paired[f"{value}_ada"] - paired[f"{value}_static"]

    records = []
    for routing, subset in paired.groupby("routing_policy"):
        estimate, low, high = _interval(subset)
        records.append(
            {
                "routing_policy": routing,
                "matched_incidents": len(incidents),
                "paired_cells": len(subset),
                "mean_completion_delta": estimate,
                "ci95_low": low,
                "ci95_high": high,
                "mean_exposure_delta": float(
                    subset["exposure_per_1000_requests_delta"].mean()
                ),
                "mean_forwarded_bytes_delta": float(subset["forwarded_bytes_delta"].mean()),
                "mean_peak_buffer_bytes_delta": float(
                    subset["peak_buffer_bytes_delta"].mean()
                ),
                "mean_stranded_transfer_delta": float(
                    subset["transfers_stranded_delta"].mean()
                ),
            }
        )

    args.output.mkdir(parents=True, exist_ok=True)
    runs_path = args.output / "routing_algorithm_runs.csv"
    paired_path = args.output / "routing_algorithm_pairs.csv"
    summary_path = args.output / "routing_algorithm_effects.csv"
    runs.to_csv(runs_path, index=False, lineterminator="\n")
    paired.to_csv(paired_path, index=False, lineterminator="\n")
    pd.DataFrame(records).sort_values("routing_policy").to_csv(
        summary_path, index=False, lineterminator="\n"
    )
    manifest_path = args.output / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "routing_policies": [policy.value for policy in ROUTING_POLICIES],
                "copy_budget": 4,
                "buffer_bytes_per_node": 96 * 1024,
                "matched_incidents": incidents,
                "seed": 9601,
                "rows": len(runs),
                "capacity_semantics": "one shared bidirectional byte budget per contact",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    checksums = args.output / "CHECKSUMS.sha256"
    checksums.write_text(
        "".join(
            f"{_sha256(path)}  {path.name}\n"
            for path in (runs_path, paired_path, summary_path, manifest_path)
        ),
        encoding="ascii",
    )
    print(json.dumps({"rows": len(runs), "matched_incidents": len(incidents)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
