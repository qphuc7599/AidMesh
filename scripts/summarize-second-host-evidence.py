"""Validate and summarize second-host evidence without identifying paths."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"expected JSON object: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_checksums(root: Path) -> dict[str, Any]:
    ledger = root / "CHECKSUMS.sha256"
    failures: list[str] = []
    checked = 0
    for number, line in enumerate(ledger.read_text(encoding="ascii").splitlines(), 1):
        try:
            expected, relative = line.split("  ./", 1)
        except ValueError:
            failures.append(f"malformed-line-{number}")
            continue
        path = root / Path(relative)
        if not path.is_file():
            failures.append(f"missing:{Path(relative).as_posix()}")
            continue
        if _sha256(path) != expected:
            failures.append(f"mismatch:{Path(relative).as_posix()}")
        checked += 1
    return {
        "status": "pass" if not failures else "fail",
        "checked": checked,
        "failures": failures,
    }


def _environment(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def _steps(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    return [
        {
            "name": row["name"],
            "status": row["status"],
            "elapsed_seconds": int(row["elapsed_seconds"]),
        }
        for row in rows
    ]


def summarize(
    root: Path,
    comparison_path: Path,
    archive: Path,
    expected_archive_checksum: Path,
) -> dict[str, Any]:
    result = _json(root / "second_host_result.json")
    comparison = _json(comparison_path)
    invariants = _json(
        root / "e2e_p1_v2_tagged_full/analysis/e2e_invariants.json"
    )
    refinement = _json(
        root / "formal/integrated_trace_refinement/integrated_refinement_summary.json"
    )
    privacy = _json(root / "privacy_sparse_cover/manifest.json")
    distributed = _json(root / "distributed_rights/distributed_rights_results.json")
    preflight = _json(root / "input_preflight.json")
    checksums = _verify_checksums(root)
    environment = _environment(root / "environment.txt")
    steps = _steps(root / "steps.tsv")

    expected_archive = (
        expected_archive_checksum.read_text(encoding="ascii").split()[0].lower()
    )
    actual_archive = _sha256(archive)
    conditions = {
        "runner_passed": result.get("status") == "pass",
        "all_steps_passed": all(row["status"] == "pass" for row in steps),
        "full_e2e_executed": result.get("full_e2e_skipped") is False,
        "host_fingerprint_differs": result.get("fingerprint_differs_from_reference")
        is True,
        "internal_checksums_passed": checksums["status"] == "pass",
        "cross_host_comparison_passed": comparison.get("status") == "pass"
        and comparison.get("failed_checks") == 0,
        "archive_transport_checksum_passed": expected_archive == actual_archive,
        "integrated_refinement_passed": refinement.get(
            "all_concrete_traces_refine_composed_model"
        )
        is True,
    }
    status = "pass" if all(conditions.values()) else "fail"
    summary = {
        "schema_version": 1,
        "status": status,
        "claim": "author-operated cross-host reproduction",
        "not_claimed": [
            "independent reproduction",
            "independent formal or security audit",
            "production deployment",
        ],
        "conditions": conditions,
        "host": {
            "platform": "Linux SLURM compute host",
            "kernel": environment.get("kernel", ""),
            "cpu": environment.get("cpu", ""),
            "python": environment.get("python", ""),
            "node": environment.get("node", ""),
            "java": environment.get("java", ""),
            "solc": environment.get("solc", ""),
            "host_fingerprint_sha256": result.get("host_fingerprint_sha256"),
            "reference_host_fingerprint_sha256": result.get(
                "reference_host_fingerprint_sha256"
            ),
        },
        "steps": steps,
        "artifact_integrity": {
            "transport_archive_sha256": actual_archive,
            "internal_files_checked": checksums["checked"],
            "internal_checksum_failures": len(checksums["failures"]),
        },
        "cross_host_equivalence": {
            "checks": comparison.get("checks"),
            "failed_checks": comparison.get("failed_checks"),
            "claim_boundary": comparison.get("claim_boundary"),
        },
        "e2e": invariants,
        "refinement": {
            key: refinement.get(key)
            for key in (
                "all_concrete_traces_refine_composed_model",
                "event_logs",
                "events",
                "prefix_simulation_holds",
                "authorized",
                "released",
                "settled",
                "duplicate_physical_releases",
                "scope",
            )
        },
        "privacy": {
            "sampled_runs": privacy.get("sampled_runs"),
            "method_counts": privacy.get("method_counts"),
            "selected_input_sha256": privacy.get("selected_input_sha256"),
        },
        "distributed_rights": {
            "trials": distributed.get("trials"),
            "processes_observed": distributed.get("processes_observed"),
            "all_passed": distributed.get("all_passed"),
            "shared_ledger": distributed.get("participant_databases", {}).get(
                "shared_ledger"
            ),
            "global_lock": distributed.get("participant_databases", {}).get(
                "global_lock"
            ),
        },
        "preflight": {
            "status": preflight.get("status"),
            "fixed_input_count": preflight.get("fixed_input_count"),
            "privacy_runs": preflight.get("privacy", {}).get("runs"),
            "privacy_input_sha256": preflight.get("privacy", {}).get(
                "selected_input_sha256"
            ),
        },
    }
    if status != "pass":
        raise RuntimeError(f"second-host evidence validation failed: {conditions}")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--comparison", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--archive-checksum", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    summary = summarize(
        args.root,
        args.comparison,
        args.archive,
        args.archive_checksum,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
