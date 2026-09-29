"""Execute the prospective local receipt-controller matrix."""

from __future__ import annotations

import hashlib
import json
import platform
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from aidmesh.experiments.matched_receipt_queue import QueueJob, schedule  # noqa: E402


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_jobs(config: dict, seed: int, load: float, partition_h: float, invalid_fraction: float):
    rng = np.random.default_rng(
        seed + int(load * 1000) + int(partition_h * 10) + int(invalid_fraction * 100)
    )
    horizon_s = config["horizonHours"] * 3600
    copy_allowance = config["copyAllowance"]
    receipt_bytes = config["receiptWireBytesPerCopy"] * copy_allowance
    background_choices = (
        np.asarray(config["backgroundWireBytesPerCopy"], dtype=int) * copy_allowance
    )
    mean_wire = config["receiptFraction"] * receipt_bytes + (1 - config["receiptFraction"]) * float(
        background_choices.mean()
    )
    capacity_bytes = config["bandwidthBps"] * horizon_s / 8
    count = max(1, int(load * capacity_bytes / mean_wire))
    created = np.sort(rng.uniform(0, horizon_s, size=count))
    jobs = []
    invalid = 0
    for index, created_s in enumerate(created):
        is_receipt = bool(rng.random() < config["receiptFraction"])
        if is_receipt and rng.random() < invalid_fraction:
            invalid += 1
            continue
        packet_class = "receipt" if is_receipt else "background"
        wire_bytes = receipt_bytes if is_receipt else int(rng.choice(background_choices))
        ttl_h = config["receiptTtlHours"] if is_receipt else config["backgroundTtlHours"]
        jobs.append(
            QueueJob(
                job_id=f"S{seed}-{index:06d}",
                created_s=float(created_s),
                ready_s=float(max(created_s, partition_h * 3600)),
                deadline_s=float(created_s + ttl_h * 3600),
                wire_bytes=wire_bytes,
                packet_class=packet_class,
            )
        )
    return jobs, invalid


def bootstrap_ci(values: np.ndarray, resamples: int, rng: np.random.Generator):
    values = np.asarray(values, dtype=float)
    draws = rng.integers(0, len(values), size=(resamples, len(values)))
    distribution = values[draws].mean(axis=1)
    return [float(np.quantile(distribution, 0.025)), float(np.quantile(distribution, 0.975))]


registry_path = ROOT / "configs/postreview_20260910/matched_receipt_controller.json"
output = ROOT / "results/matched_receipt_controller_20260910"
config = json.loads(registry_path.read_text(encoding="utf-8"))
output.mkdir(parents=True, exist_ok=True)
rows = []
workload_rows = []
for load in config["loadFactors"]:
    for partition_h in config["partitionHours"]:
        for invalid_fraction in config["invalidPriorityFractions"]:
            for seed in range(
                config["seeds"]["start"], config["seeds"]["start"] + config["seeds"]["count"]
            ):
                jobs, invalid = make_jobs(config, seed, load, partition_h, invalid_fraction)
                workload_hash = hashlib.sha256(
                    json.dumps(
                        [job.__dict__ for job in jobs], sort_keys=True, separators=(",", ":")
                    ).encode()
                ).hexdigest()
                workload_rows.append(
                    {
                        "load_factor": load,
                        "partition_hours": partition_h,
                        "invalid_priority_fraction": invalid_fraction,
                        "seed": seed,
                        "admitted_jobs": len(jobs),
                        "invalid_priority_rejected": invalid,
                        "wire_bytes": sum(job.wire_bytes for job in jobs),
                        "workload_sha256": workload_hash,
                    }
                )
                for policy in config["policies"]:
                    scheduled = pd.DataFrame(
                        schedule(jobs, policy=policy, bandwidth_bps=config["bandwidthBps"])
                    )
                    receipt = scheduled[scheduled["packet_class"] == "receipt"]
                    background = scheduled[scheduled["packet_class"] == "background"]
                    rows.append(
                        {
                            "load_factor": load,
                            "partition_hours": partition_h,
                            "invalid_priority_fraction": invalid_fraction,
                            "seed": seed,
                            "policy": policy,
                            "jobs": len(scheduled),
                            "wire_bytes": int(scheduled["wire_bytes"].sum()),
                            "receipt_jobs": len(receipt),
                            "receipt_median_delay_s": float(receipt["end_to_end_delay_s"].median()),
                            "receipt_p95_delay_s": float(
                                receipt["end_to_end_delay_s"].quantile(0.95)
                            ),
                            "receipt_deadline_completion": float(receipt["deadline_met"].mean()),
                            "background_median_delay_s": float(
                                background["end_to_end_delay_s"].median()
                            ),
                            "background_deadline_completion": float(
                                background["deadline_met"].mean()
                            ),
                            "workload_sha256": workload_hash,
                        }
                    )

frame = pd.DataFrame(rows)
workloads = pd.DataFrame(workload_rows)
frame.to_csv(output / "runs.csv", index=False)
workloads.to_csv(output / "workloads.csv", index=False)

rng = np.random.default_rng(20260910)
contrasts = []
group_fields = ["load_factor", "partition_hours", "invalid_priority_fraction"]
for cell, group in frame.groupby(group_fields):
    wide = group.pivot(index="seed", columns="policy")
    for control in ("fifo", "edf"):
        for endpoint in (
            "receipt_median_delay_s",
            "receipt_p95_delay_s",
            "receipt_deadline_completion",
            "background_median_delay_s",
            "background_deadline_completion",
        ):
            values = (wide[endpoint]["receipt_priority"] - wide[endpoint][control]).to_numpy()
            contrasts.append(
                {
                    "load_factor": float(cell[0]),
                    "partition_hours": float(cell[1]),
                    "invalid_priority_fraction": float(cell[2]),
                    "treatment": "receipt_priority",
                    "control": control,
                    "endpoint": endpoint,
                    "meanDifference": float(values.mean()),
                    "ci95": bootstrap_ci(values, config["bootstrapResamples"], rng),
                    "wins": int((values < 0).sum() if "delay" in endpoint else (values > 0).sum()),
                    "ties": int((values == 0).sum()),
                    "losses": int(
                        (values > 0).sum() if "delay" in endpoint else (values < 0).sum()
                    ),
                }
            )
(output / "paired_contrasts.json").write_text(json.dumps(contrasts, indent=2), encoding="utf-8")
primary = [
    row
    for row in contrasts
    if row["control"] == "fifo" and row["endpoint"] == "receipt_median_delay_s"
]
matching = {
    "allPolicyWorkloadHashesMatch": bool(
        frame.groupby(group_fields + ["seed"])["workload_sha256"].nunique().eq(1).all()
    ),
    "allPolicyByteBudgetsMatch": bool(
        frame.groupby(group_fields + ["seed"])["wire_bytes"].nunique().eq(1).all()
    ),
    "allInvalidPriorityPacketsRejectedBeforeQueue": True,
}
summary = {
    "profile": "matched local receipt scheduling under reconnection bursts",
    "runs": len(frame),
    "matchedWorkloads": len(workloads),
    "primaryContrasts": primary,
    "allPrimaryCellsFavorReceiptPriority": all(row["ci95"][1] < 0 for row in primary),
    "matchingChecks": matching,
    "privacyBoundary": (
        "Authenticated packet class is visible to the local controller; timing and class "
        "confidentiality are not claimed."
    ),
}
(output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
manifest = {
    "registry": str(registry_path.relative_to(ROOT)).replace("\\", "/"),
    "registrySha256": sha256(registry_path),
    "implementationSha256": sha256(ROOT / "aidmesh/experiments/matched_receipt_queue.py"),
    "runnerSha256": sha256(Path(__file__)),
    "python": sys.version,
    "platform": platform.platform(),
    "outputs": {
        name: sha256(output / name)
        for name in ("runs.csv", "workloads.csv", "paired_contrasts.json", "summary.json")
    },
}
(output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
print(json.dumps(summary, indent=2))
