"""Run a frozen confirmatory registry while retaining raw events as deterministic gzip.

The core simulator first validates and writes canonical JSONL.  This wrapper then
checks the event checksum recorded in the summary, compresses the JSONL with a
zero gzip timestamp, and removes only that per-cell uncompressed copy.  Worker
failures are retained as machine-readable records so coverage cannot silently
drop a failed cell.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aidmesh.config import canonical_json, load_scenario  # noqa: E402
from aidmesh.experiments.confirmatory import (  # noqa: E402
    load_confirmatory_registry,
    require_frozen,
    write_freeze_manifest,
)
from aidmesh.manifest import sha256_file  # noqa: E402
from aidmesh.simulator import run_experiment  # noqa: E402


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json(value) + b"\n")


def _compress(source: Path, destination: Path) -> None:
    with source.open("rb") as raw, destination.open("wb") as target:
        with gzip.GzipFile(fileobj=target, mode="wb", compresslevel=6, mtime=0) as archive:
            for block in iter(lambda: raw.read(1024 * 1024), b""):
                archive.write(block)


def _run_cell(task: tuple[str, int, str, str, str, str]) -> dict[str, Any]:
    config_path_text, seed, method, run_dir_text, software_snapshot, registry_hash = task
    run_dir = Path(run_dir_text)
    events = run_dir / "events.jsonl"
    compressed = run_dir / "events.jsonl.gz"
    summary_path = run_dir / "summary.json"
    failure_path = run_dir / "failure.json"
    try:
        config = load_scenario(Path(config_path_text))
        config["seed"] = seed
        result = run_experiment(
            config,
            method,
            raw_path=events,
            software_commit=software_snapshot,
        )
        summary = {
            **result.to_dict(),
            "phase": config["phase"],
            "registry_sha256": registry_hash,
        }
        if sha256_file(events) != summary["event_checksum"]:
            raise RuntimeError("canonical event file does not match recorder checksum")
        _write_json(summary_path, summary)
        _compress(events, compressed)
        events.unlink()
        if failure_path.exists():
            failure_path.unlink()
        return {"status": "completed", "run_dir": run_dir_text}
    except Exception as exc:  # retained failure is part of registered coverage
        _write_json(
            failure_path,
            {
                "status": "failed",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "traceback": traceback.format_exc(),
                "config": config_path_text,
                "seed": seed,
                "method": method,
                "registry_sha256": registry_hash,
                "recorded_at_utc": datetime.now(UTC).isoformat(),
            },
        )
        return {"status": "failed", "run_dir": run_dir_text, "error": str(exc)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--software-snapshot", required=True)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--no-resume", action="store_true")
    args = parser.parse_args()
    started_at = datetime.now(UTC)
    started_clock = time.perf_counter()

    if args.workers < 1:
        raise SystemExit("--workers must be at least one")
    registry = load_confirmatory_registry(args.registry, root=ROOT)
    require_frozen(registry)
    output = args.output.resolve()
    if "pilot" in {part.lower() for part in output.parts}:
        raise SystemExit("registered output path must not contain 'pilot'")
    output.mkdir(parents=True, exist_ok=True)
    freeze_manifest = write_freeze_manifest(registry, output)
    freeze_done_clock = time.perf_counter()

    tasks: list[tuple[str, int, str, str, str, str]] = []
    skipped = 0
    for scheduled in registry.scenarios:
        scenario_id = load_scenario(scheduled.config_path)["scenario_id"]
        for seed in scheduled.seeds:
            for method in scheduled.methods:
                run_dir = output / registry.registry_id / scenario_id / method / f"seed-{seed:05d}"
                complete = (run_dir / "summary.json").is_file() and (
                    run_dir / "events.jsonl.gz"
                ).is_file()
                if complete and not args.no_resume:
                    skipped += 1
                    continue
                tasks.append(
                    (
                        str(scheduled.config_path),
                        seed,
                        method,
                        str(run_dir),
                        args.software_snapshot,
                        registry.registry_hash,
                    )
                )

    results: list[dict[str, Any]] = []
    if args.workers == 1:
        results = [_run_cell(task) for task in tasks]
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            results = list(pool.map(_run_cell, tasks, chunksize=2))
    execution_done_clock = time.perf_counter()

    tracked = sorted(output.rglob("events.jsonl.gz"))
    tracked += sorted(output.rglob("summary.json"))
    tracked += sorted(output.rglob("failure.json"))
    checksums = output / "CHECKSUMS.txt"
    checksums.write_text(
        "".join(
            f"{sha256_file(path)}  {path.relative_to(output).as_posix()}\n"
            for path in tracked
        ),
        encoding="ascii",
    )
    report = {
        "registry_id": registry.registry_id,
        "registry_sha256": registry.registry_hash,
        "software_snapshot": args.software_snapshot,
        "scheduled_this_invocation": len(tasks),
        "completed_this_invocation": sum(row["status"] == "completed" for row in results),
        "failed_this_invocation": sum(row["status"] == "failed" for row in results),
        "skipped_complete": skipped,
        "freeze_manifest": str(freeze_manifest),
        "checksums": str(checksums),
        "workers": args.workers,
        "started_at_utc": started_at.isoformat(),
        "ended_at_utc": datetime.now(UTC).isoformat(),
        "wall_seconds": time.perf_counter() - started_clock,
        "freeze_seconds": freeze_done_clock - started_clock,
        "cell_execution_seconds": execution_done_clock - freeze_done_clock,
    }
    _write_json(output / "EXECUTION_REPORT.json", report)
    print(json.dumps(report, indent=2))
    return 1 if report["failed_this_invocation"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
