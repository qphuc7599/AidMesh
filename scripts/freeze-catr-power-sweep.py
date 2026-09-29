"""Hash-freeze the post-confirmatory CATR power sweep before execution."""

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
    files = [
        registry_path,
        workspace / "aidmesh/experiments/catr_power_sweep.py",
        workspace / "aidmesh/experiments/bow_temporal.py",
        workspace / "aidmesh/policy/conservative_reclaimable.py",
    ]
    files.extend(workspace / row["path"] / "contacts.csv" for row in registry["contexts"])
    records = [
        {
            "path": path.relative_to(workspace).as_posix(),
            "sha256": _sha256(path),
            "bytes": path.stat().st_size,
        }
        for path in files
    ]
    aggregate = hashlib.sha256()
    for row in sorted(records, key=lambda value: value["path"]):
        aggregate.update(row["path"].encode())
        aggregate.update(bytes.fromhex(row["sha256"]))
    expected_rows = (
        len(registry["contexts"])
        * len(registry["request_multipliers"])
        * 2
        * len(registry["cap_fractions"])
        * len(registry["quorum_availabilities"])
        * len(registry["policies"])
    )
    freeze = {
        "schema_version": 1,
        "freeze_id": registry["registry_id"],
        "created_utc": datetime.now(UTC).isoformat(),
        "status": "locally frozen before power-sweep outcomes",
        "post_confirmatory": True,
        "independent_timestamp": False,
        "expected_rows": expected_rows,
        "aggregate_sha256": aggregate.hexdigest(),
        "files": records,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    path = args.output / "freeze.json"
    path.write_text(json.dumps(freeze, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (args.output / "CHECKSUMS.sha256").write_text(
        f"{_sha256(path)}  {path.name}\n",
        encoding="ascii",
    )
    print(
        json.dumps(
            {
                "freeze": str(path),
                "expected_rows": expected_rows,
                "aggregate_sha256": aggregate.hexdigest(),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
