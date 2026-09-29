"""Resumable multi-process runner for the final BOW evidence campaigns.

The scientific unit of parallelism is a registered trace/seed shard.  Primary
shards contain all cap, witness and policy cells for one seed.  Factorial
shards contain a contiguous, preregistered subset of the 128 cells.  Every
shard is checksummed before it can be reused or merged.
"""

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

from aidmesh.analysis.bow_advanced import write_advanced_analysis
from aidmesh.analysis.hierarchical_bow import (
    fit_factorial_mechanism_model,
    hierarchical_policy_effects,
)
from aidmesh.experiments.bow_factorial import (
    FACTOR_LEVELS,
    FACTORIAL_POLICIES,
    all_factorial_cells,
    factorial_policy_contrasts,
    run_bow_factorial,
)
from aidmesh.experiments.bow_temporal import (
    ONLINE_POLICIES,
    paired_policy_contrasts,
    run_bow_temporal_campaign,
)
from aidmesh.experiments.bow_trace_families import family_balanced_summary


@dataclass(frozen=True)
class _Trace:
    family: str
    context: str
    profile: str
    path: str
    primary: bool
    factorial: bool


@dataclass(frozen=True)
class _Task:
    kind: str
    trace: _Trace
    seed: int
    output: str
    cap_fractions: tuple[float, ...] = ()
    quorum_availabilities: tuple[float, ...] = ()
    calibration_fraction: float = 0.4
    buffer_bytes: int = 96 * 1024
    adatransfer_gate_mode: str = "confidence"
    cell_start: int = 0
    cell_stop: int = 0


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_registry(path: Path, workspace: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("contexts"), list):
        raise ValueError("full campaign registry requires a contexts list")
    traces = []
    seen = set()
    for item in data["contexts"]:
        trace = _Trace(
            family=str(item["family"]),
            context=str(item["context"]),
            profile=str(item["profile"]),
            path=str((workspace / str(item["path"])).resolve()),
            primary=bool(item.get("primary", False)),
            factorial=bool(item.get("factorial", False)),
        )
        if trace.context in seen:
            raise ValueError(f"registered context names must be globally unique: {trace.context}")
        if not (Path(trace.path) / "contacts.csv").is_file():
            raise FileNotFoundError(Path(trace.path) / "contacts.csv")
        seen.add(trace.context)
        traces.append(trace)
    return {**data, "contexts": traces}


def _shard_stem(task: _Task) -> str:
    stem = f"{task.trace.family}__{task.trace.context}__s{task.seed}"
    if task.kind == "factorial":
        stem += f"__c{task.cell_start:03d}-{task.cell_stop:03d}"
    return stem


def _shard_paths(task: _Task) -> tuple[Path, Path, Path]:
    root = Path(task.output) / "shards"
    stem = _shard_stem(task)
    return root / f"{stem}.csv", root / f"{stem}.json", root / f"{stem}.sha256"


def _valid_shard(task: _Task) -> bool:
    csv_path, manifest_path, checksum_path = _shard_paths(task)
    if not (csv_path.is_file() and manifest_path.is_file() and checksum_path.is_file()):
        return False
    expected = checksum_path.read_text(encoding="ascii").strip().split()
    if len(expected) != 2 or expected[1] != csv_path.name:
        return False
    if expected[0] != _sha256(csv_path):
        return False
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return (
        manifest.get("kind") == task.kind
        and manifest.get("context") == task.trace.context
        and manifest.get("seed") == task.seed
        and manifest.get("status") == "complete"
    )


def _atomic_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(text, encoding=encoding)
    os.replace(temporary, path)


def _write_frame_atomic(frame: pd.DataFrame, path: Path) -> None:
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    frame.to_csv(temporary, index=False, lineterminator="\n")
    os.replace(temporary, path)


def _run_task(task: _Task) -> dict[str, Any]:
    csv_path, manifest_path, checksum_path = _shard_paths(task)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    if _valid_shard(task):
        return {"stem": _shard_stem(task), "status": "reused", "seconds": 0.0}
    started = time.perf_counter()
    trace_path = Path(task.trace.path)
    if task.kind == "primary":
        frame, nested = run_bow_temporal_campaign(
            trace_path.parent,
            contexts=(trace_path.name,),
            seeds=(task.seed,),
            cap_fractions=task.cap_fractions,
            quorum_availabilities=task.quorum_availabilities,
            policies=ONLINE_POLICIES,
            calibration_fraction=task.calibration_fraction,
            buffer_bytes=task.buffer_bytes,
            adatransfer_gate_mode=task.adatransfer_gate_mode,
        )
        # Integer risk budgets can legitimately collapse adjacent target
        # fractions on short/sparse traces (for example, max(4 sites,
        # round(7 requests * 0.10)) and the 0.20 target are both four).
        # Treating those as independent replications would inflate evidence.
        # Validate the unique effective cap grid actually evaluated instead.
        effective_caps = tuple(sorted(int(value) for value in frame["cap"].unique()))
        expected_rows = (
            len(effective_caps)
            * len(task.quorum_availabilities)
            * len(ONLINE_POLICIES)
        )
        primary_keys = ["cap", "quorum_availability", "policy"]
        if frame.duplicated(primary_keys).any():
            raise RuntimeError(
                f"shard {_shard_stem(task)} contains duplicate primary cells"
            )
    elif task.kind == "factorial":
        all_cells = all_factorial_cells()
        cells = all_cells[task.cell_start : task.cell_stop]
        frame, nested = run_bow_factorial(
            trace_path.parent,
            contexts=(trace_path.name,),
            seeds=(task.seed,),
            policies=FACTORIAL_POLICIES,
            cells=cells,
            calibration_fraction=task.calibration_fraction,
            adatransfer_gate_mode=task.adatransfer_gate_mode,
        )
        cell_lookup = {
            tuple(cell[name] for name in FACTOR_LEVELS): index
            for index, cell in enumerate(all_cells)
        }
        frame["cell"] = [
            cell_lookup[tuple(row[name] for name in FACTOR_LEVELS)]
            for _, row in frame.iterrows()
        ]
        expected_rows = len(cells) * len(FACTORIAL_POLICIES)
    else:
        raise ValueError(f"unknown campaign kind: {task.kind}")
    if len(frame) != expected_rows:
        raise RuntimeError(
            f"shard {_shard_stem(task)} produced {len(frame)} rows, expected {expected_rows}"
        )
    frame["family"] = task.trace.family
    frame["registered_context"] = task.trace.context
    frame["profile"] = task.trace.profile
    elapsed = time.perf_counter() - started
    manifest = {
        "schema_version": 1,
        "kind": task.kind,
        "family": task.trace.family,
        "context": task.trace.context,
        "profile": task.trace.profile,
        "seed": task.seed,
        "cell_start": task.cell_start if task.kind == "factorial" else None,
        "cell_stop": task.cell_stop if task.kind == "factorial" else None,
        "trace_contacts_sha256": _sha256(trace_path / "contacts.csv"),
        "rows": len(frame),
        "effective_caps": list(effective_caps) if task.kind == "primary" else None,
        "elapsed_seconds": elapsed,
        "nested_manifest": nested,
        "status": "complete",
    }
    _write_frame_atomic(frame, csv_path)
    _atomic_text(manifest_path, json.dumps(manifest, indent=2) + "\n")
    _atomic_text(
        checksum_path,
        f"{_sha256(csv_path)}  {csv_path.name}\n",
        encoding="ascii",
    )
    return {"stem": _shard_stem(task), "status": "complete", "seconds": elapsed}


def _tasks(
    kind: str,
    registry: dict[str, Any],
    output: Path,
    *,
    factorial_chunk_cells: int,
) -> list[_Task]:
    traces = [trace for trace in registry["contexts"] if getattr(trace, kind)]
    seed_key = "seeds" if kind == "primary" else "factorial_seeds"
    seeds = tuple(int(value) for value in registry[seed_key])
    common = {
        "kind": kind,
        "output": str(output.resolve()),
        "cap_fractions": tuple(float(value) for value in registry["cap_fractions"]),
        "quorum_availabilities": tuple(
            float(value) for value in registry["quorum_availabilities"]
        ),
        "calibration_fraction": float(registry["calibration_fraction"]),
        "buffer_bytes": int(registry["buffer_bytes"]),
        "adatransfer_gate_mode": str(
            registry.get("adatransfer_gate_mode", "confidence")
        ),
    }
    tasks = []
    for trace in traces:
        for seed in seeds:
            if kind == "primary":
                tasks.append(_Task(trace=trace, seed=seed, **common))
                continue
            for start in range(0, len(all_factorial_cells()), factorial_chunk_cells):
                tasks.append(
                    _Task(
                        trace=trace,
                        seed=seed,
                        cell_start=start,
                        cell_stop=min(len(all_factorial_cells()), start + factorial_chunk_cells),
                        **common,
                    )
                )
    return tasks


def _write_checksums(output: Path, paths: list[Path]) -> Path:
    checksums = output / "CHECKSUMS.sha256"
    checksums.write_text(
        "".join(f"{_sha256(path)}  {path.name}\n" for path in paths),
        encoding="ascii",
    )
    return checksums


def _merge(
    kind: str,
    registry_path: Path,
    registry: dict[str, Any],
    output: Path,
    *,
    factorial_chunk_cells: int,
) -> None:
    tasks = _tasks(
        kind,
        registry,
        output,
        factorial_chunk_cells=factorial_chunk_cells,
    )
    missing = [task for task in tasks if not _valid_shard(task)]
    if missing:
        raise RuntimeError(f"cannot merge: {len(missing)} shards are missing or invalid")
    frames = [pd.read_csv(_shard_paths(task)[0]) for task in tasks]
    frame = pd.concat(frames, ignore_index=True)
    traces = [trace for trace in registry["contexts"] if getattr(trace, kind)]
    seed_key = "seeds" if kind == "primary" else "factorial_seeds"
    seeds = tuple(int(value) for value in registry[seed_key])
    if kind == "primary":
        # Sum the independently validated shard contracts.  The nominal cap
        # fractions are targets; sparse traces may have fewer unique integer
        # budgets, recorded in each shard manifest as ``effective_caps``.
        expected = sum(
            int(json.loads(_shard_paths(task)[1].read_text(encoding="utf-8"))["rows"])
            for task in tasks
        )
        unique_keys = [
            "family",
            "registered_context",
            "seed",
            "cap",
            "quorum_availability",
            "policy",
        ]
    else:
        expected = len(traces) * len(seeds) * 128 * len(FACTORIAL_POLICIES)
        unique_keys = ["family", "registered_context", "seed", "cell", "policy"]
    if len(frame) != expected or frame.duplicated(unique_keys).any():
        raise RuntimeError(
            f"merged campaign integrity failure: rows={len(frame)}, expected={expected}, "
            f"duplicate_keys={int(frame.duplicated(unique_keys).sum())}"
        )
    output.mkdir(parents=True, exist_ok=True)
    runs = output / f"bow_{kind}_runs.csv"
    _write_frame_atomic(frame, runs)
    assets = [runs]
    if kind == "primary":
        contrasts = paired_policy_contrasts(frame)
        effects = hierarchical_policy_effects(
            frame,
            reference="optimized_static",
            repetitions=int(registry.get("bootstrap_repetitions", 5000)),
        )
        family_summary = family_balanced_summary(frame)
        summary = (
            frame.groupby(["family", "registered_context", "policy"], as_index=False)
            .agg(
                runs=("completed", "size"),
                generated_requests=("generated_requests", "sum"),
                mean_completion_rate=("completion_rate", "mean"),
                mean_conditional_completion_rate=("conditional_completion_rate", "mean"),
                mean_settlement_probability=(
                    "end_to_end_settlement_probability",
                    "mean",
                ),
                total_exposure=("duplicate_exposure", "sum"),
                total_cap_violations=("risk_cap_violation", "sum"),
                total_transfers=("transfers_started", "sum"),
                total_stranded=("transfers_stranded", "sum"),
                total_forwarded_bytes=("forwarded_bytes", "sum"),
            )
        )
        named = {
            "contrasts": contrasts,
            "effects": effects,
            "family_balanced_summary": family_summary,
            "summary": summary,
        }
    else:
        contrasts = factorial_policy_contrasts(frame)
        effects = hierarchical_policy_effects(
            frame,
            reference="optimized_static",
            repetitions=int(registry.get("bootstrap_repetitions", 5000)),
        )
        coefficients, diagnostics = fit_factorial_mechanism_model(frame)
        named = {"contrasts": contrasts, "effects": effects, "coefficients": coefficients}
        diagnostics_path = output / "bow_factorial_mechanism_diagnostics.json"
        _atomic_text(diagnostics_path, json.dumps(diagnostics, indent=2) + "\n")
        assets.append(diagnostics_path)
    for name, data in named.items():
        path = output / f"bow_{kind}_{name}.csv"
        _write_frame_atomic(data, path)
        assets.append(path)
    advanced = write_advanced_analysis(
        frame,
        output,
        kind=kind,
        coefficients=coefficients if kind == "factorial" else None,
    )
    assets.extend(advanced.values())
    manifest = {
        "schema_version": 1,
        "campaign": kind,
        "registry": str(registry_path.resolve()),
        "registry_sha256": _sha256(registry_path),
        "families": sorted(frame["family"].unique()),
        "contexts": [trace.context for trace in traces],
        "seeds": list(seeds),
        "policies": list(ONLINE_POLICIES if kind == "primary" else FACTORIAL_POLICIES),
        "rows": len(frame),
        "expected_rows": expected,
        "shards": len(tasks),
        "status": "complete",
        "adatransfer_gate_mode": str(
            registry.get("adatransfer_gate_mode", "confidence")
        ),
    }
    manifest_path = output / "manifest.json"
    _atomic_text(manifest_path, json.dumps(manifest, indent=2) + "\n")
    assets.append(manifest_path)
    _write_checksums(output, assets)


def run(args: argparse.Namespace) -> int:
    workspace = args.workspace.resolve()
    registry = _load_registry(args.registry.resolve(), workspace)
    tasks = _tasks(
        args.kind,
        registry,
        args.output,
        factorial_chunk_cells=args.factorial_chunk_cells,
    )
    pending = [task for task in tasks if not _valid_shard(task)]
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
            completed = len(tasks) - len(pending)
            for future in as_completed(futures):
                task = futures[future]
                try:
                    result = future.result()
                    completed += 1
                    print(
                        json.dumps({"progress": f"{completed}/{len(tasks)}", **result}),
                        flush=True,
                    )
                except Exception as exc:  # pragma: no cover - exercised in campaigns
                    failures.append((_shard_stem(task), repr(exc)))
                    print(json.dumps({"failed": _shard_stem(task), "error": repr(exc)}), flush=True)
    if failures:
        raise RuntimeError(f"{len(failures)} shards failed: {failures[:3]}")
    _merge(
        args.kind,
        args.registry.resolve(),
        registry,
        args.output.resolve(),
        factorial_chunk_cells=args.factorial_chunk_cells,
    )
    print(json.dumps({"status": "complete", "output": str(args.output.resolve())}), flush=True)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("primary", "factorial"))
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument(
        "--workers",
        type=int,
        default=max(1, min(10, (os.cpu_count() or 2) - 2)),
    )
    parser.add_argument("--factorial-chunk-cells", type=int, default=8)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.workers <= 0 or args.factorial_chunk_cells <= 0:
        raise SystemExit("workers and factorial chunk size must be positive")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
