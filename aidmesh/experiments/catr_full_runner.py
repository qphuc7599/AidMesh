"""Resumable multiprocessing runner for the frozen CATR confirmatory campaign."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from aidmesh.experiments.bow_temporal import run_bow_temporal_campaign
from aidmesh.experiments.catr_confirmatory import (
    CATR_FACTOR_LEVELS,
    CATR_FACTORIAL_POLICIES,
    CATR_PRIMARY_POLICIES,
    all_catr_factorial_cells,
    catr_factorial_contrasts,
    run_catr_factorial,
)


@dataclass(frozen=True)
class _Trace:
    family: str
    context: str
    profile: str
    path: str


@dataclass(frozen=True)
class _Task:
    kind: str
    trace: _Trace
    seed: int
    output: str
    calibration_fraction: float
    cap_fractions: tuple[float, ...]
    quorum_availabilities: tuple[float, ...]
    conservative_protection_fraction: float
    cell_start: int = 0
    cell_stop: int = 0


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(content, encoding=encoding)
    os.replace(temporary, path)


def _atomic_frame(frame: pd.DataFrame, path: Path) -> None:
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    frame.to_csv(temporary, index=False, lineterminator="\n")
    os.replace(temporary, path)


def _load_registry(path: Path, workspace: Path) -> dict[str, Any]:
    registry = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(registry, dict) or not isinstance(registry.get("contexts"), list):
        raise ValueError("CATR registry requires a contexts list")
    traces = []
    seen = set()
    for item in registry["contexts"]:
        context = str(item["context"])
        if context in seen:
            raise ValueError(f"duplicate registered context: {context}")
        trace_path = (workspace / str(item["path"])).resolve()
        if not (trace_path / "contacts.csv").is_file():
            raise FileNotFoundError(trace_path / "contacts.csv")
        seen.add(context)
        traces.append(
            _Trace(
                family=str(item["family"]),
                context=context,
                profile=str(item["profile"]),
                path=str(trace_path),
            )
        )
    if tuple(registry.get("primary_policies", ())) != CATR_PRIMARY_POLICIES:
        raise ValueError("registry primary policy order differs from frozen code")
    if tuple(registry.get("factorial_policies", ())) != CATR_FACTORIAL_POLICIES:
        raise ValueError("registry factorial policy order differs from frozen code")
    if tuple(registry.get("factor_order", ())) != tuple(CATR_FACTOR_LEVELS):
        raise ValueError("registry factor order differs from frozen code")
    family_manifest_path = workspace / str(registry["family_manifest"])
    family_manifest = json.loads(family_manifest_path.read_text(encoding="utf-8"))
    registered = [trace.context for trace in traces]
    if registered != family_manifest["eligible_contexts"]:
        raise ValueError("registered contexts differ from outcome-blind eligible set")
    if registry["upstream_commit"] != family_manifest["upstream_commit"]:
        raise ValueError("registry upstream commit differs from family manifest")
    return {**registry, "contexts": traces}


def _stem(task: _Task) -> str:
    value = f"{task.trace.context}__s{task.seed}"
    if task.kind == "factorial":
        value += f"__c{task.cell_start:03d}-{task.cell_stop:03d}"
    return value


def _paths(task: _Task) -> tuple[Path, Path, Path]:
    root = Path(task.output) / task.kind / "shards"
    stem = _stem(task)
    return root / f"{stem}.csv", root / f"{stem}.json", root / f"{stem}.sha256"


def _valid(task: _Task) -> bool:
    csv_path, manifest_path, checksum_path = _paths(task)
    if not all(path.is_file() for path in (csv_path, manifest_path, checksum_path)):
        return False
    checksum = checksum_path.read_text(encoding="ascii").strip().split()
    if len(checksum) != 2 or checksum[0] != _sha256(csv_path):
        return False
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return (
        manifest.get("status") == "complete"
        and manifest.get("kind") == task.kind
        and manifest.get("context") == task.trace.context
        and manifest.get("seed") == task.seed
        and manifest.get("cell_start") == (task.cell_start if task.kind == "factorial" else None)
        and manifest.get("cell_stop") == (task.cell_stop if task.kind == "factorial" else None)
    )


def _run_task(task: _Task) -> dict[str, Any]:
    csv_path, manifest_path, checksum_path = _paths(task)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    if _valid(task):
        return {"stem": _stem(task), "status": "reused", "seconds": 0.0}
    started = time.perf_counter()
    trace_path = Path(task.trace.path)
    if task.kind == "primary":
        frame, nested = run_bow_temporal_campaign(
            trace_path.parent,
            contexts=(trace_path.name,),
            seeds=(task.seed,),
            cap_fractions=task.cap_fractions,
            quorum_availabilities=task.quorum_availabilities,
            policies=CATR_PRIMARY_POLICIES,
            calibration_fraction=task.calibration_fraction,
            adatransfer_gate_mode="expected_value",
            conservative_protection_fraction=(task.conservative_protection_fraction),
            oracle_policies=(),
            experiment_prefix="catr-primary-confirmatory",
        )
        effective_caps = tuple(sorted(int(value) for value in frame["cap"].unique()))
        expected = (
            len(effective_caps) * len(task.quorum_availabilities) * len(CATR_PRIMARY_POLICIES)
        )
        if frame.duplicated(["cap", "quorum_availability", "policy"]).any():
            raise RuntimeError(f"duplicate primary cell in {_stem(task)}")
    elif task.kind == "factorial":
        all_cells = all_catr_factorial_cells()
        selected = all_cells[task.cell_start : task.cell_stop]
        frame, nested = run_catr_factorial(
            trace_path.parent,
            contexts=(trace_path.name,),
            seeds=(task.seed,),
            policies=CATR_FACTORIAL_POLICIES,
            cells=selected,
            calibration_fraction=task.calibration_fraction,
            conservative_protection_fraction=(task.conservative_protection_fraction),
        )
        lookup = {
            tuple(cell[name] for name in CATR_FACTOR_LEVELS): index
            for index, cell in enumerate(all_cells)
        }
        frame["cell"] = [
            lookup[tuple(row[name] for name in CATR_FACTOR_LEVELS)] for _, row in frame.iterrows()
        ]
        expected = len(selected) * len(CATR_FACTORIAL_POLICIES)
        effective_caps = ()
    else:  # pragma: no cover - CLI restricts this value
        raise ValueError(f"unknown campaign kind: {task.kind}")
    if len(frame) != expected:
        raise RuntimeError(f"{_stem(task)} produced {len(frame)} rows, expected {expected}")
    frame["family"] = task.trace.family
    frame["registered_context"] = task.trace.context
    frame["profile"] = task.trace.profile
    elapsed = time.perf_counter() - started
    manifest = {
        "schema_version": 1,
        "status": "complete",
        "kind": task.kind,
        "family": task.trace.family,
        "context": task.trace.context,
        "profile": task.trace.profile,
        "seed": task.seed,
        "cell_start": task.cell_start if task.kind == "factorial" else None,
        "cell_stop": task.cell_stop if task.kind == "factorial" else None,
        "trace_contacts_sha256": _sha256(trace_path / "contacts.csv"),
        "rows": len(frame),
        "effective_caps": list(effective_caps),
        "elapsed_seconds": elapsed,
        "nested_manifest": nested,
    }
    _atomic_frame(frame, csv_path)
    _atomic_text(manifest_path, json.dumps(manifest, indent=2) + "\n")
    _atomic_text(
        checksum_path,
        f"{_sha256(csv_path)}  {csv_path.name}\n",
        encoding="ascii",
    )
    return {"stem": _stem(task), "status": "complete", "seconds": elapsed}


def _tasks(
    kind: str,
    registry: dict[str, Any],
    output: Path,
    *,
    factorial_chunk_cells: int,
) -> list[_Task]:
    seed_key = "primary_seeds" if kind == "primary" else "factorial_seeds"
    seeds = tuple(int(seed) for seed in registry[seed_key])
    common = {
        "kind": kind,
        "output": str(output.resolve()),
        "calibration_fraction": float(registry["calibration_fraction"]),
        "cap_fractions": tuple(float(x) for x in registry["cap_fractions"]),
        "quorum_availabilities": tuple(float(x) for x in registry["quorum_availabilities"]),
        "conservative_protection_fraction": float(registry["conservative_protection_fraction"]),
    }
    tasks = []
    for trace in registry["contexts"]:
        for seed in seeds:
            if kind == "primary":
                tasks.append(_Task(trace=trace, seed=seed, **common))
                continue
            for start in range(0, len(all_catr_factorial_cells()), factorial_chunk_cells):
                tasks.append(
                    _Task(
                        trace=trace,
                        seed=seed,
                        cell_start=start,
                        cell_stop=min(
                            len(all_catr_factorial_cells()),
                            start + factorial_chunk_cells,
                        ),
                        **common,
                    )
                )
    return tasks


def _paired_primary_contrasts(frame: pd.DataFrame) -> pd.DataFrame:
    keys = [
        "family",
        "registered_context",
        "seed",
        "cap",
        "quorum_availability",
    ]
    values = [
        "completion_rate",
        "end_to_end_settlement_probability",
        "transfers_stranded",
        "transfers_reclaimed",
        "forwarded_bytes",
    ]
    reference = frame.loc[frame["policy"] == "optimized_static", keys + values]
    reference = reference.rename(columns={name: f"{name}_reference" for name in values})
    result = frame.merge(reference, on=keys, how="inner", validate="many_to_one")
    result["reference"] = "optimized_static"
    result["completion_delta"] = result["completion_rate"] - result["completion_rate_reference"]
    return result


def _merge(kind: str, tasks: list[_Task], registry_path: Path, output: Path) -> None:
    missing = [task for task in tasks if not _valid(task)]
    if missing:
        raise RuntimeError(f"cannot merge: {len(missing)} invalid or missing shards")
    frame = pd.concat([pd.read_csv(_paths(task)[0]) for task in tasks], ignore_index=True)
    if kind == "primary":
        keys = [
            "registered_context",
            "seed",
            "cap",
            "quorum_availability",
            "policy",
        ]
        contrasts = _paired_primary_contrasts(frame)
    else:
        keys = ["registered_context", "seed", "cell", "policy"]
        contrasts = catr_factorial_contrasts(frame)
    expected = sum(
        int(json.loads(_paths(task)[1].read_text(encoding="utf-8"))["rows"]) for task in tasks
    )
    if len(frame) != expected or frame.duplicated(keys).any():
        raise RuntimeError(
            f"merge integrity failure rows={len(frame)} expected={expected} "
            f"duplicates={int(frame.duplicated(keys).sum())}"
        )
    target = output / kind
    target.mkdir(parents=True, exist_ok=True)
    runs_path = target / f"catr_{kind}_runs.csv"
    contrasts_path = target / f"catr_{kind}_contrasts.csv"
    summary_path = target / f"catr_{kind}_summary.csv"
    _atomic_frame(frame, runs_path)
    _atomic_frame(contrasts, contrasts_path)
    summary = frame.groupby(["family", "registered_context", "policy"], as_index=False).agg(
        runs=("completed", "size"),
        generated_requests=("generated_requests", "sum"),
        mean_completion_rate=("completion_rate", "mean"),
        mean_settlement_probability=(
            "end_to_end_settlement_probability",
            "mean",
        ),
        transfers_started=("transfers_started", "sum"),
        transfers_delivered=("transfers_delivered", "sum"),
        transfers_accepted=("transfers_accepted", "sum"),
        transfers_reclaimed=("transfers_reclaimed", "sum"),
        transfers_stranded=("transfers_stranded", "sum"),
        cap_violations=("risk_cap_violation", "sum"),
        conservation_failures=(
            "rights_conservation_holds",
            lambda values: int((values == False).sum()),  # noqa: E712
        ),
    )
    _atomic_frame(summary, summary_path)
    manifest = {
        "schema_version": 1,
        "campaign": f"CATR confirmatory {kind}",
        "registry": str(registry_path.resolve()),
        "registry_sha256": _sha256(registry_path),
        "rows": len(frame),
        "expected_rows": expected,
        "shards": len(tasks),
        "contexts": sorted(frame["registered_context"].unique()),
        "seeds": sorted(int(value) for value in frame["seed"].unique()),
        "policies": sorted(frame["policy"].unique()),
        "status": "complete",
    }
    manifest_path = target / "manifest.json"
    _atomic_text(manifest_path, json.dumps(manifest, indent=2) + "\n")
    assets = [runs_path, contrasts_path, summary_path, manifest_path]
    _atomic_text(
        target / "CHECKSUMS.sha256",
        "".join(f"{_sha256(path)}  {path.name}\n" for path in assets),
        encoding="ascii",
    )


def run(args: argparse.Namespace) -> int:
    workspace = args.workspace.resolve()
    registry = _load_registry(args.registry.resolve(), workspace)
    tasks = _tasks(
        args.kind,
        registry,
        args.output.resolve(),
        factorial_chunk_cells=args.factorial_chunk_cells,
    )
    pending = [task for task in tasks if not _valid(task)]
    print(
        json.dumps(
            {
                "kind": args.kind,
                "workers": args.workers,
                "tasks": len(tasks),
                "reused": len(tasks) - len(pending),
                "pending": len(pending),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    failures = []
    if pending:
        with ProcessPoolExecutor(max_workers=args.workers) as executor:
            futures = {executor.submit(_run_task, task): task for task in pending}
            complete = len(tasks) - len(pending)
            for future in as_completed(futures):
                task = futures[future]
                try:
                    result = future.result()
                    complete += 1
                    print(
                        json.dumps({"progress": f"{complete}/{len(tasks)}", **result}),
                        flush=True,
                    )
                except Exception as exc:  # pragma: no cover - campaign failure path
                    failures.append((_stem(task), repr(exc)))
                    print(
                        json.dumps({"failed": _stem(task), "error": repr(exc)}),
                        flush=True,
                    )
    if failures:
        raise RuntimeError(f"{len(failures)} shards failed: {failures[:3]}")
    _merge(args.kind, tasks, args.registry.resolve(), args.output.resolve())
    print(json.dumps({"status": "complete", "output": str(args.output.resolve())}))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("primary", "factorial"))
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--workers", type=int, default=max(1, min(10, os.cpu_count() or 2)))
    parser.add_argument("--factorial-chunk-cells", type=int, default=16)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.workers <= 0 or args.factorial_chunk_cells <= 0:
        raise SystemExit("workers and factorial chunk size must be positive")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
