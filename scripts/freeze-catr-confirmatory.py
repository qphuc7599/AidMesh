"""Create the local cryptographic freeze before any Asturies AidMesh outcome run."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import yaml


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    registry_path = args.registry.resolve()
    registry = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    family_manifest_path = workspace / registry["family_manifest"]
    family_manifest = json.loads(family_manifest_path.read_text(encoding="utf-8"))
    contexts = [row["context"] for row in registry["contexts"]]
    if contexts != family_manifest["eligible_contexts"]:
        raise SystemExit("registry does not exactly match outcome-blind eligibility")
    fixed_sources = [
        Path("aidmesh/protocol/risk_escrow.py"),
        Path("aidmesh/policy/adatransfer.py"),
        Path("aidmesh/policy/conservative_reclaimable.py"),
        Path("aidmesh/simulator/shared_queue.py"),
        Path("aidmesh/experiments/bow_temporal.py"),
        Path("aidmesh/experiments/bow_factorial.py"),
        Path("aidmesh/experiments/catr_confirmatory.py"),
        Path("aidmesh/experiments/catr_full_runner.py"),
        Path("aidmesh/experiments/temporal_frontier.py"),
        Path("aidmesh/analysis/time_expanded_frontier.py"),
        Path("formal/AidMeshReclaimableTransfer.tla"),
        Path("formal/AidMeshReclaimableTransferSafe.cfg"),
        Path("formal/AidMeshReclaimableTransferUnsafeOverwrite.cfg"),
    ]
    files = [registry_path, family_manifest_path]
    files.extend(workspace / path for path in fixed_sources)
    for context in contexts:
        trace_root = workspace / "external" / "asturies_wildfires" / "normalized" / context
        files.extend((trace_root / "contacts.csv", trace_root / "provenance.json"))
    missing = [str(path) for path in files if not path.is_file()]
    if missing:
        raise SystemExit(f"missing frozen input: {missing[:3]}")
    records = [
        {
            "path": path.resolve().relative_to(workspace).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in files
    ]
    aggregate = hashlib.sha256()
    for record in sorted(records, key=lambda row: row["path"]):
        aggregate.update(record["path"].encode("utf-8"))
        aggregate.update(bytes.fromhex(record["sha256"]))
    freeze = {
        "schema_version": 1,
        "freeze_id": registry["registry_id"],
        "created_utc": datetime.now(UTC).isoformat(),
        "status": "locally cryptographically frozen before AidMesh outcomes",
        "independent_timestamp": False,
        "independent_timestamp_note": (
            "No OSF/notary account was available; this is a reproducible local hash "
            "freeze and must not be described as independently time-stamped."
        ),
        "outcome_blind_eligibility": True,
        "eligible_incidents": len(contexts),
        "declared_runtime_ceiling_hours": registry["declared_runtime_ceiling_hours"],
        "expected_primary_rows_nominal": (
            len(contexts)
            * len(registry["primary_seeds"])
            * len(registry["cap_fractions"])
            * len(registry["quorum_availabilities"])
            * len(registry["primary_policies"])
        ),
        "expected_factorial_rows": (
            len(contexts)
            * len(registry["factorial_seeds"])
            * 256
            * len(registry["factorial_policies"])
        ),
        "aggregate_sha256": aggregate.hexdigest(),
        "files": records,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    freeze_path = args.output / "freeze.json"
    freeze_path.write_text(
        json.dumps(freeze, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (args.output / "CHECKSUMS.sha256").write_text(
        f"{_sha256(freeze_path)}  {freeze_path.name}\n",
        encoding="ascii",
    )
    print(
        json.dumps(
            {
                "freeze": str(freeze_path),
                **{
                    k: freeze[k]
                    for k in (
                        "aggregate_sha256",
                        "eligible_incidents",
                        "expected_primary_rows_nominal",
                        "expected_factorial_rows",
                    )
                },
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
