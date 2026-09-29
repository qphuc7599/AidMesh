"""Run a matched 10/50/200 m AdaTransfer-versus-static sensitivity."""

from __future__ import annotations

import argparse
import hashlib
import json
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from aidmesh.experiments.bow_temporal import run_bow_temporal_campaign

UPSTREAM_COMMIT = "6180235cfd63dbd78fdd4cb5eda9cc9e97ac21fe"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _task(arguments: tuple[int, int, Path, Path]) -> Path:
    range_m, incident, trace_root, shard_root = arguments
    context = f"asturies_wf{incident:02d}_r{range_m}"
    output = shard_root / f"{context}.csv"
    if output.is_file():
        return output
    frame, _ = run_bow_temporal_campaign(
        trace_root,
        contexts=(context,),
        seeds=(9401,),
        cap_fractions=(0.15, 0.60),
        quorum_availabilities=(0.25, 0.90),
        policies=("robust_static", "adatransfer"),
        calibration_fraction=0.40,
        adatransfer_gate_mode="expected_value",
        conservative_protection_fraction=0.10,
        oracle_policies=(),
        experiment_prefix=f"asturies-range-sensitivity-r{range_m}",
    )
    frame["radio_range_m"] = range_m
    frame["incident"] = incident
    output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output, index=False, lineterminator="\n")
    return output


def _cluster_bootstrap(values: pd.DataFrame, repetitions: int = 5000) -> tuple[float, float, float]:
    incident = (
        values.groupby("incident")["completion_rate_delta"].mean().to_numpy(dtype=float)
    )
    rng = np.random.default_rng(20260901)
    draws = rng.choice(incident, size=(repetitions, len(incident)), replace=True).mean(axis=1)
    return (
        float(incident.mean()),
        float(np.quantile(draws, 0.025)),
        float(np.quantile(draws, 0.975)),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("results/elsevier_remediation_20260901/range_sensitivity/policy"),
    )
    args = parser.parse_args()
    manifests = {
        10: Path("results/elsevier_remediation_20260901/range_sensitivity/r10"),
        50: Path("results/elsevier_remediation_20260901/range_sensitivity/r50"),
        200: Path("external/asturies_wildfires/normalized"),
    }
    eligible: dict[int, set[int]] = {}
    for range_m, root in manifests.items():
        manifest = json.loads((root / "family_manifest.json").read_text(encoding="utf-8"))
        if manifest["upstream_commit"] != UPSTREAM_COMMIT:
            raise RuntimeError("range manifest uses another upstream commit")
        eligible[range_m] = {
            int(context.split("wf", 1)[1].split("_", 1)[0])
            for context in manifest["eligible_contexts"]
        }
    common = sorted(set.intersection(*eligible.values()))
    tasks = [
        (range_m, incident, root, args.output / "shards")
        for range_m, root in manifests.items()
        for incident in common
    ]
    paths: list[Path] = []
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(_task, task) for task in tasks]
        for future in as_completed(futures):
            paths.append(future.result())
    frame = pd.concat([pd.read_csv(path) for path in sorted(paths)], ignore_index=True)
    keys = ["radio_range_m", "incident", "seed", "cap", "quorum_availability"]
    values = [
        "completion_rate",
        "end_to_end_settlement_probability",
        "exposure_per_1000_requests",
        "restricted_mean_latency_hours",
        "forwarded_bytes",
        "transfers_stranded",
    ]
    ada = frame.loc[frame["policy"] == "adatransfer", keys + values]
    static = frame.loc[frame["policy"] == "robust_static", keys + values]
    paired = ada.merge(static, on=keys, suffixes=("_ada", "_static"), validate="one_to_one")
    for value in values:
        paired[f"{value}_delta"] = paired[f"{value}_ada"] - paired[f"{value}_static"]
    records: list[dict[str, Any]] = []
    for range_m, subset in paired.groupby("radio_range_m"):
        estimate, low, high = _cluster_bootstrap(subset)
        records.append(
            {
                "radio_range_m": int(range_m),
                "matched_incidents": len(common),
                "paired_cells": len(subset),
                "mean_completion_delta": estimate,
                "ci95_low": low,
                "ci95_high": high,
                "mean_exposure_delta_per_1000": float(
                    subset["exposure_per_1000_requests_delta"].mean()
                ),
                "mean_stranded_transfer_delta": float(subset["transfers_stranded_delta"].mean()),
            }
        )
    args.output.mkdir(parents=True, exist_ok=True)
    runs_path = args.output / "matched_policy_runs.csv"
    paired_path = args.output / "matched_policy_pairs.csv"
    summary_path = args.output / "matched_range_effects.csv"
    frame.to_csv(runs_path, index=False, lineterminator="\n")
    paired.to_csv(paired_path, index=False, lineterminator="\n")
    pd.DataFrame(records).sort_values("radio_range_m").to_csv(
        summary_path, index=False, lineterminator="\n"
    )
    manifest_path = args.output / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "upstream_commit": UPSTREAM_COMMIT,
                "matched_incidents": common,
                "ranges_m": sorted(manifests),
                "seeds": [9401],
                "cap_fractions": [0.15, 0.60],
                "quorum_availabilities": [0.25, 0.90],
                "policies": ["robust_static", "adatransfer"],
                "rows": len(frame),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    checksum_path = args.output / "CHECKSUMS.sha256"
    checksum_path.write_text(
        "".join(
            f"{_sha256(path)}  {path.name}\n"
            for path in (runs_path, paired_path, summary_path, manifest_path)
        ),
        encoding="ascii",
    )
    print(json.dumps({"rows": len(frame), "matched_incidents": len(common)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
