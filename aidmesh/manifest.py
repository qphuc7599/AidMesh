"""Create and verify immutable research manifests without requiring Git."""

from __future__ import annotations

import hashlib
import os
import platform
import subprocess
import sys
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from aidmesh.config import canonical_json

EXCLUDED_PARTS = {
    ".git",
    ".venv",
    ".pytest_cache",
    ".hypothesis",
    "__pycache__",
    "tmp",
    "raw",
    "processed",
    "results",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_files(root: Path) -> Iterable[Path]:
    """Yield artifact inputs in stable order, excluding generated and private state."""

    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if any(part in EXCLUDED_PARTS for part in path.parts):
            continue
        try:
            is_file = path.is_file()
        except OSError:
            # Broken or inaccessible external-runtime links are not source inputs.
            # Concrete executable inputs are recorded separately by each campaign.
            continue
        if not is_file:
            continue
        if path.name in {"MANIFEST.json", "CHECKSUMS.txt", "host.local.json"}:
            continue
        yield path


def tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in source_files(root):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(bytes.fromhex(sha256_file(path)))
    return digest.hexdigest()


def git_commit(root: Path) -> str | None:
    try:
        value = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, stderr=subprocess.DEVNULL, text=True
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return value or None


def hash_lines(values: Iterable[str]) -> str:
    return hashlib.sha256(canonical_json(sorted(values))).hexdigest()


def build_manifest(
    root: Path,
    *,
    phase: str,
    crypto_profile: str,
    crypto_gate_passed: bool,
    scenario_files: Iterable[Path],
    seeds: Iterable[int],
    primary_estimands: list[str],
    decision_rules: list[str],
) -> dict:
    lock = root / "requirements.lock"
    analysis_files = sorted((root / "aidmesh" / "analysis").glob("*.py"))
    manifest = {
        "schema_version": "2.0.0",
        "freeze_timestamp_utc": datetime.now(UTC).isoformat(),
        "git_commit": git_commit(root),
        "source_tree_hash": tree_hash(root),
        "dependency_lock_hash": sha256_file(lock) if lock.exists() else "0" * 64,
        "crypto_profile": crypto_profile,
        "crypto_gate_passed": crypto_gate_passed,
        "scenario_hash": hash_lines(
            f"{path.as_posix()}:{sha256_file(path)}" for path in scenario_files
        ),
        "seed_hash": hash_lines(str(seed) for seed in seeds),
        "analysis_hash": hash_lines(f"{path.name}:{sha256_file(path)}" for path in analysis_files),
        "phase": phase,
        "primary_estimands": primary_estimands,
        "decision_rules": decision_rules,
        "deviations": [],
    }
    return manifest


def write_manifest(path: Path, manifest: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json(manifest) + b"\n")


def host_report() -> dict:
    return {
        "platform": platform.platform(),
        "python": sys.version,
        "processor": platform.processor(),
        "machine": platform.machine(),
        "logical_cores": os.cpu_count(),
        "timezone_policy": "UTC timestamps in artifacts; host timezone recorded separately",
    }
