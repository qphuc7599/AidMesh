"""Corrective replication for expiring, uncommitted terminal prepares."""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import yaml

from aidmesh.experiments.catr_confirmatory import run_catr_factorial


RECOVERY_POLICIES = (
    "optimized_static",
    "adatransfer",
    "reclaimable_transfer",
    "reclaimable_transfer_v2",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run_context(
    family: str,
    context: str,
    path: str,
    seed: int,
) -> pd.DataFrame:
    trace_path = Path(path)
    frame, _ = run_catr_factorial(
        trace_path.parent,
        contexts=(trace_path.name,),
        seeds=(seed,),
        policies=RECOVERY_POLICIES,
    )
    frame["family"] = family
    frame["registered_context"] = context
    return frame


def run_recovery_replication(
    registry_path: Path,
    output: Path,
    *,
    workspace: Path,
    workers: int,
) -> dict[str, Path]:
    registry = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    if tuple(registry["policies"]) != RECOVERY_POLICIES:
        raise ValueError("recovery policy order differs from frozen code")
    base_path = workspace / registry["base_registry"]
    base = yaml.safe_load(base_path.read_text(encoding="utf-8"))
    seed = int(registry["seed"])
    frames = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                _run_context,
                str(row["family"]),
                str(row["context"]),
                str((workspace / row["path"]).resolve()),
                seed,
            ): row
            for row in base["contexts"]
        }
        for index, future in enumerate(as_completed(futures), start=1):
            row = futures[future]
            frame = future.result()
            frames.append(frame)
            print(
                json.dumps(
                    {
                        "progress": f"{index}/{len(futures)}",
                        "context": row["context"],
                        "rows": len(frame),
                    }
                ),
                flush=True,
            )
    runs = pd.concat(frames, ignore_index=True)
    expected = len(base["contexts"]) * 256 * len(RECOVERY_POLICIES)
    keys = ["registered_context", "seed", "cell", "policy"]
    if len(runs) != expected or runs.duplicated(keys).any():
        raise RuntimeError("recovery replication row integrity failure")
    output.mkdir(parents=True, exist_ok=True)
    runs_path = output / "catr_recovery_v2_runs.csv"
    runs.to_csv(runs_path, index=False, lineterminator="\n")
    summary = runs.groupby(["demand_drift", "recovery_timing", "policy"], as_index=False).agg(
        runs=("completed", "size"),
        mean_completion_rate=("completion_rate", "mean"),
        transfers_started=("transfers_started", "sum"),
        transfers_delivered=("transfers_delivered", "sum"),
        transfers_accepted=("transfers_accepted", "sum"),
        transfers_reclaimed=("transfers_reclaimed", "sum"),
        transfers_final_locked=("transfers_stranded", "sum"),
        cap_violations=("risk_cap_violation", "sum"),
    )
    summary_path = output / "catr_recovery_v2_summary.csv"
    summary.to_csv(summary_path, index=False, lineterminator="\n")
    v1 = runs.loc[runs["policy"] == "reclaimable_transfer"]
    v2 = runs.loc[runs["policy"] == "reclaimable_transfer_v2"]
    report = {
        "schema_version": 1,
        "design": "post-confirmatory corrective replication",
        "registry": str(registry_path.resolve()),
        "registry_sha256": _sha256(registry_path),
        "rows": len(runs),
        "expected_rows": expected,
        "r1_final_lock_ratio": float(
            v2["transfers_stranded"].sum() / max(1, v1["transfers_stranded"].sum())
        ),
        "r2_reclaim_delta": int(v2["transfers_reclaimed"].sum() - v1["transfers_reclaimed"].sum()),
        "r3_cap_violations": int(
            runs.loc[
                runs["policy"].isin(
                    ["adatransfer", "reclaimable_transfer", "reclaimable_transfer_v2"]
                ),
                "risk_cap_violation",
            ].sum()
        ),
        "r3_conservation_failures": int(
            (
                ~runs.loc[
                    runs["policy"].isin(
                        ["adatransfer", "reclaimable_transfer", "reclaimable_transfer_v2"]
                    ),
                    "rights_conservation_holds",
                ].astype(bool)
            ).sum()
        ),
        "status": "complete",
    }
    report_path = output / "report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    assets = [runs_path, summary_path, report_path]
    checksums = output / "CHECKSUMS.sha256"
    checksums.write_text(
        "".join(f"{_sha256(path)}  {path.name}\n" for path in assets),
        encoding="ascii",
    )
    return {path.stem: path for path in (*assets, checksums)}
