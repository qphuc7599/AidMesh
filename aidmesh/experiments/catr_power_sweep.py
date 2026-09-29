"""Post-confirmatory detection-power sweep for the CATR mechanism boundary."""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from aidmesh.experiments.bow_factorial import _requests_for_drift
from aidmesh.experiments.bow_temporal import (
    CausalCampaignConfig,
    calibration_path_reliability,
    evaluate_causal_policy,
)
from aidmesh.experiments.temporal_frontier import (
    _calibration_roles,
    generate_heldout_requests,
)
from aidmesh.simulator.dtn import load_contacts_csv

POWER_SWEEP_POLICIES = (
    "optimized_static",
    "adatransfer",
    "conservative_transfer",
    "catr",
)


def expand_requests(requests: tuple[Any, ...], multiplier: int) -> tuple[Any, ...]:
    """Replicate arrivals without changing contact or destination evidence."""

    if multiplier <= 0:
        raise ValueError("request multiplier must be positive")
    expanded = []
    for request in requests:
        for replica in range(multiplier):
            bundle_id = f"{request.bundle.bundle_id}:load{replica}"
            expanded.append(
                replace(
                    request,
                    bundle=replace(request.bundle, bundle_id=bundle_id),
                    nullifier=f"{request.nullifier}:load{replica}",
                )
            )
    return tuple(
        sorted(
            expanded,
            key=lambda row: (row.bundle.created_us, row.bundle.bundle_id),
        )
    )


@dataclass(frozen=True)
class _SweepTask:
    family: str
    context: str
    trace_path: str
    seed: int
    calibration_fraction: float
    multipliers: tuple[int, ...]
    cap_fractions: tuple[float, ...]
    quorum_availabilities: tuple[float, ...]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run_task(task: _SweepTask) -> pd.DataFrame:
    trace_path = Path(task.trace_path)
    contacts = load_contacts_csv(trace_path / "contacts.csv")
    start_us = min(row.start_us for row in contacts)
    end_us = max(row.end_us for row in contacts)
    cutoff_us = start_us + round((end_us - start_us) * task.calibration_fraction)
    roles = _calibration_roles(contacts, cutoff_us)
    sites = roles["sites"]
    witness_pool = tuple(dict.fromkeys((*roles["relays"], *sites)))
    witnesses = witness_pool[: min(5, len(witness_pool))]
    threshold = min(3, len(witnesses))
    reliability = calibration_path_reliability(contacts, sites=sites, cutoff_us=cutoff_us)
    heldout = tuple(row for row in contacts if row.start_us >= cutoff_us)
    base = generate_heldout_requests(
        contacts,
        roles,
        context=task.context,
        seed=task.seed,
        cutoff_us=cutoff_us,
    )
    rows = []
    for multiplier in task.multipliers:
        expanded = expand_requests(base, multiplier)
        for drift in ("stable", "shifting"):
            requests = _requests_for_drift(
                expanded,
                sites=sites,
                context=task.context,
                seed=task.seed,
                level=drift,
            )
            for cap_fraction in task.cap_fractions:
                cap = max(len(sites), round(len(requests) * cap_fraction))
                for quorum_availability in task.quorum_availabilities:
                    for policy in POWER_SWEEP_POLICIES:
                        config = CausalCampaignConfig(
                            cap=cap,
                            policy=policy,
                            gateway=roles["relays"][0],
                            witnesses=witnesses,
                            quorum_threshold=threshold,
                            quorum_availability=quorum_availability,
                            transfer_witness_availability=quorum_availability,
                            adatransfer_gate_mode="expected_value",
                            experiment_id=(
                                f"catr-power:{task.context}:{task.seed}:{multiplier}:"
                                f"{drift}:{cap_fraction}:{quorum_availability}"
                            ),
                        )
                        result = evaluate_causal_policy(
                            contacts=heldout,
                            requests=requests,
                            sites=sites,
                            config=config,
                            path_reliability=reliability,
                            compute_oracle=False,
                        )
                        rows.append(
                            {
                                "family": task.family,
                                "registered_context": task.context,
                                "seed": task.seed,
                                "request_multiplier": multiplier,
                                "demand_drift": drift,
                                "cap_fraction": cap_fraction,
                                "quorum_availability": quorum_availability,
                                **result,
                            }
                        )
    return pd.DataFrame(rows)


def run_power_sweep(
    registry_path: Path,
    output: Path,
    *,
    workspace: Path,
    workers: int,
) -> dict[str, Path]:
    registry = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    tasks = [
        _SweepTask(
            family=str(row["family"]),
            context=str(row["context"]),
            trace_path=str((workspace / row["path"]).resolve()),
            seed=int(registry["seed"]),
            calibration_fraction=float(registry["calibration_fraction"]),
            multipliers=tuple(int(x) for x in registry["request_multipliers"]),
            cap_fractions=tuple(float(x) for x in registry["cap_fractions"]),
            quorum_availabilities=tuple(float(x) for x in registry["quorum_availabilities"]),
        )
        for row in registry["contexts"]
    ]
    frames = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_run_task, task): task for task in tasks}
        for index, future in enumerate(as_completed(futures), start=1):
            task = futures[future]
            frame = future.result()
            frames.append(frame)
            print(
                json.dumps(
                    {
                        "progress": f"{index}/{len(tasks)}",
                        "context": task.context,
                        "rows": len(frame),
                    }
                ),
                flush=True,
            )
    runs = pd.concat(frames, ignore_index=True)
    keys = [
        "registered_context",
        "seed",
        "request_multiplier",
        "demand_drift",
        "cap_fraction",
        "quorum_availability",
        "policy",
    ]
    expected = (
        len(tasks)
        * len(registry["request_multipliers"])
        * 2
        * len(registry["cap_fractions"])
        * len(registry["quorum_availabilities"])
        * len(POWER_SWEEP_POLICIES)
    )
    if len(runs) != expected or runs.duplicated(keys).any():
        raise RuntimeError("power-sweep row integrity failure")
    output.mkdir(parents=True, exist_ok=True)
    runs_path = output / "catr_power_sweep_runs.csv"
    runs.to_csv(runs_path, index=False, lineterminator="\n")
    baseline = runs.loc[
        runs["policy"] == "optimized_static",
        keys[:-1] + ["completion_rate"],
    ].rename(columns={"completion_rate": "baseline_completion_rate"})
    contrasts = runs.merge(baseline, on=keys[:-1], validate="many_to_one")
    contrasts["completion_delta"] = (
        contrasts["completion_rate"] - contrasts["baseline_completion_rate"]
    )
    contrasts_path = output / "catr_power_sweep_contrasts.csv"
    contrasts.to_csv(contrasts_path, index=False, lineterminator="\n")
    summary = contrasts.groupby(
        ["request_multiplier", "demand_drift", "policy"], as_index=False
    ).agg(
        runs=("completed", "size"),
        mean_completion_rate=("completion_rate", "mean"),
        mean_completion_delta=("completion_delta", "mean"),
        transfers_started=("transfers_started", "sum"),
        transfers_accepted=("transfers_accepted", "sum"),
        transfers_reclaimed=("transfers_reclaimed", "sum"),
        transfers_stranded=("transfers_stranded", "sum"),
        positive_margin_decisions=(
            "transfer_positive_margin_decisions",
            "sum",
        ),
    )
    summary_path = output / "catr_power_sweep_summary.csv"
    summary.to_csv(summary_path, index=False, lineterminator="\n")
    manifest_path = output / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "design": "post-confirmatory registered detection-power sweep",
                "registry": str(registry_path.resolve()),
                "registry_sha256": _sha256(registry_path),
                "policies": list(POWER_SWEEP_POLICIES),
                "tasks": [asdict(task) for task in tasks],
                "rows": len(runs),
                "expected_rows": expected,
                "workers": workers,
                "status": "complete",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    assets = [runs_path, contrasts_path, summary_path, manifest_path]
    checksums = output / "CHECKSUMS.sha256"
    checksums.write_text(
        "".join(f"{_sha256(path)}  {path.name}\n" for path in assets),
        encoding="ascii",
    )
    return {path.stem: path for path in (*assets, checksums)}
