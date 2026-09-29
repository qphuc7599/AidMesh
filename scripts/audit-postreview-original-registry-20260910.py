"""Audit registered cell coverage, compressed raw events, safety, and replay parity."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aidmesh.config import canonical_json, load_scenario  # noqa: E402
from aidmesh.experiments.confirmatory import load_confirmatory_registry  # noqa: E402

SAFE_METHODS = {
    "online_only",
    "aidmesh_quorum_only",
    "aidmesh_static_rights",
    "aidmesh_transferable_rights",
}
NON_SEMANTIC_SUMMARY_FIELDS = {"event_checksum", "registry_sha256"}


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json(value) + b"\n")


def _decompressed_sha256(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    byte_count = 0
    with gzip.open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
            byte_count += len(block)
    return digest.hexdigest(), byte_count


def _semantic_summary(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key not in NON_SEMANTIC_SUMMARY_FIELDS}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--historical", type=Path)
    args = parser.parse_args()

    registry = load_confirmatory_registry(args.registry, root=ROOT)
    campaign_root = args.raw / registry.registry_id
    expected: dict[tuple[str, str, int], Path] = {}
    scenario_hashes: dict[str, str] = {}
    for scheduled in registry.scenarios:
        config = load_scenario(scheduled.config_path)
        scenario_id = config["scenario_id"]
        scenario_hashes[scenario_id] = hashlib.sha256(
            scheduled.config_path.read_bytes()
        ).hexdigest()
        for seed in scheduled.seeds:
            for method in scheduled.methods:
                expected[(scenario_id, method, seed)] = (
                    campaign_root / scenario_id / method / f"seed-{seed:05d}"
                )

    missing: list[str] = []
    failed: list[str] = []
    invalid: list[dict[str, Any]] = []
    safety_violations: list[dict[str, Any]] = []
    replay_mismatches: list[dict[str, Any]] = []
    completed = 0
    event_bytes_raw = 0
    event_bytes_compressed = 0
    for identity, run_dir in expected.items():
        scenario_id, method, seed = identity
        summary_path = run_dir / "summary.json"
        events_path = run_dir / "events.jsonl.gz"
        failure_path = run_dir / "failure.json"
        label = f"{scenario_id}/{method}/{seed}"
        if failure_path.is_file():
            failed.append(label)
        if not summary_path.is_file() or not events_path.is_file():
            missing.append(label)
            continue
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        identity_ok = (
            summary.get("scenario_id") == scenario_id
            and summary.get("method") == method
            and int(summary.get("seed", -1)) == seed
            and summary.get("phase") == "confirmatory"
            and summary.get("registry_sha256") == registry.registry_hash
        )
        event_hash, raw_bytes = _decompressed_sha256(events_path)
        checksum_ok = event_hash == summary.get("event_checksum")
        if not identity_ok or not checksum_ok:
            invalid.append(
                {"cell": label, "identity_ok": identity_ok, "event_checksum_ok": checksum_ok}
            )
        event_bytes_raw += raw_bytes
        event_bytes_compressed += events_path.stat().st_size
        if method in SAFE_METHODS and (
            not bool(summary.get("risk_rights_conserved"))
            or int(summary.get("conflicting_quorum_certificates", 0)) != 0
            or float(summary.get("exposure_cap_ratio", 0.0)) > 1.0
        ):
            safety_violations.append(
                {
                    "cell": label,
                    "risk_rights_conserved": summary.get("risk_rights_conserved"),
                    "conflicting_quorum_certificates": summary.get(
                        "conflicting_quorum_certificates"
                    ),
                    "exposure_cap_ratio": summary.get("exposure_cap_ratio"),
                }
            )
        if args.historical is not None:
            old_dir = args.historical / scenario_id / method / f"seed-{seed:04d}"
            old_summary_path = old_dir / "summary.json"
            if not old_summary_path.is_file():
                replay_mismatches.append({"cell": label, "reason": "historical summary missing"})
            else:
                old_summary = json.loads(old_summary_path.read_text(encoding="utf-8"))
                if _semantic_summary(summary) != _semantic_summary(old_summary):
                    replay_mismatches.append({"cell": label, "reason": "semantic summary differs"})
        completed += 1

    actual_dirs = {
        path.parent
        for path in campaign_root.rglob("summary.json")
    } | {path.parent for path in campaign_root.rglob("failure.json")}
    expected_dirs = set(expected.values())
    extras = sorted(
        path.relative_to(campaign_root).as_posix() for path in actual_dirs - expected_dirs
    )
    report = {
        "registry_id": registry.registry_id,
        "registry_sha256": registry.registry_hash,
        "scope": (
            "prospectively specified fresh Monte Carlo seed holdout"
            if args.historical is None
            else "current-source replay of the previously analyzed original registry"
        ),
        "expected_cells": len(expected),
        "completed_cells": completed,
        "failed_cells": len(failed),
        "missing_cells": len(missing),
        "invalid_cells": len(invalid),
        "extra_cells": len(extras),
        "safe_method_violations": len(safety_violations),
        "semantic_replay_mismatches": (
            len(replay_mismatches) if args.historical is not None else None
        ),
        "scenario_config_sha256": scenario_hashes,
        "raw_event_bytes": event_bytes_raw,
        "compressed_event_bytes": event_bytes_compressed,
        "compression_ratio": (
            event_bytes_compressed / event_bytes_raw if event_bytes_raw else None
        ),
        "all_checks_passed": not (
            missing or failed or invalid or extras or safety_violations or replay_mismatches
        ),
        "details": {
            "missing": missing,
            "failed": failed,
            "invalid": invalid,
            "extras": extras,
            "safety_violations": safety_violations,
            "semantic_replay_mismatches": replay_mismatches,
        },
    }
    _write_json(args.output, report)
    print(json.dumps(report, indent=2))
    return 0 if report["all_checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
