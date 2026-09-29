"""Validate and content-freeze the replay and unseen-seed registries before execution."""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aidmesh.config import canonical_json  # noqa: E402
from aidmesh.experiments.confirmatory import (  # noqa: E402
    load_confirmatory_registry,
    write_freeze_manifest,
)
from aidmesh.manifest import sha256_file  # noqa: E402

RESULT = ROOT / "results/postreview_original_registry_20260910"
ORIGINAL = ROOT / "configs/confirmatory/registry.yaml"
REPLAY = ROOT / "configs/postreview_20260910/original_registry_replay_v1.yaml"
HOLDOUT = ROOT / "configs/postreview_20260910/original_registry_holdout_v1.yaml"
PRE_RUN = RESULT / "PRE_RUN_FREEZE.json"


def _input_files() -> list[Path]:
    values = list((ROOT / "aidmesh").rglob("*.py"))
    values += list((ROOT / "schemas").rglob("*.json"))
    values += list((ROOT / "configs/confirmatory").rglob("*.yaml"))
    values += [
        REPLAY,
        HOLDOUT,
        ROOT / "requirements.lock",
        ROOT / "pyproject.toml",
        ROOT / "scripts/run-compressed-confirmatory-registry-20260910.py",
        ROOT / "scripts/audit-postreview-original-registry-20260910.py",
        Path(__file__).resolve(),
    ]
    return sorted(set(path.resolve() for path in values))


def _combined_hash(rows: list[dict[str, object]]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        digest.update(canonical_json(row))
        digest.update(b"\n")
    return digest.hexdigest()


def _shape(registry) -> list[tuple[str, int, tuple[str, ...]]]:
    return [
        (
            scenario.config_path.relative_to(ROOT).as_posix(),
            scenario.seed_count,
            scenario.methods,
        )
        for scenario in registry.scenarios
    ]


def main() -> int:
    if PRE_RUN.exists():
        raise SystemExit(f"refusing to overwrite existing pre-run freeze: {PRE_RUN}")
    original = load_confirmatory_registry(ORIGINAL, root=ROOT)
    replay = load_confirmatory_registry(REPLAY, root=ROOT)
    holdout = load_confirmatory_registry(HOLDOUT, root=ROOT)
    if _shape(original) != _shape(replay) or _shape(original) != _shape(holdout):
        raise SystemExit("registry scenario/method/count shape differs from the original")
    if original.raw["analysis"] != replay.raw["analysis"]:
        raise SystemExit("replay analysis differs from original")
    if original.raw["analysis"] != holdout.raw["analysis"]:
        raise SystemExit("holdout analysis differs from original")
    original_cells = {
        (scenario.config_path, seed) for scenario in original.scenarios for seed in scenario.seeds
    }
    holdout_cells = {
        (scenario.config_path, seed) for scenario in holdout.scenarios for seed in scenario.seeds
    }
    if original_cells & holdout_cells:
        raise SystemExit("holdout seeds overlap the previously analyzed original registry")

    rows = [
        {
            "path": path.relative_to(ROOT).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in _input_files()
    ]
    snapshot = _combined_hash(rows)
    RESULT.mkdir(parents=True, exist_ok=True)
    replay_freeze = write_freeze_manifest(replay, RESULT / "replay_freeze")
    holdout_freeze = write_freeze_manifest(holdout, RESULT / "holdout_freeze")
    payload = {
        "schema_version": "1.0",
        "frozen_at_utc": datetime.now(UTC).isoformat(),
        "registration_characterization": (
            "locally and prospectively specified; not an external preregistration"
        ),
        "original_registry_sha256": original.registry_hash,
        "replay_registry_sha256": replay.registry_hash,
        "holdout_registry_sha256": holdout.registry_hash,
        "original_expected_cells": sum(
            scenario.seed_count * len(scenario.methods) for scenario in original.scenarios
        ),
        "replay_expected_cells": sum(
            scenario.seed_count * len(scenario.methods) for scenario in replay.scenarios
        ),
        "holdout_expected_cells": sum(
            scenario.seed_count * len(scenario.methods) for scenario in holdout.scenarios
        ),
        "holdout_seed_overlap_with_original": 0,
        "software_snapshot_sha256": snapshot,
        "input_files": rows,
        "freeze_manifests": {
            "replay": replay_freeze.relative_to(ROOT).as_posix(),
            "holdout": holdout_freeze.relative_to(ROOT).as_posix(),
        },
    }
    PRE_RUN.write_bytes(canonical_json(payload) + b"\n")
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
