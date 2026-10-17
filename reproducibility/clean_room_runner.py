"""Run representative analysis reproduction in a fresh read-only container."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

REPO = Path("/repo")
OUT = Path("/out")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(command: list[str]) -> dict:
    started = time.monotonic()
    completed = subprocess.run(
        command,
        cwd=REPO,
        env={
            **os.environ,
            "PYTHONPATH": str(REPO),
            "MPLBACKEND": "Agg",
            "MPLCONFIGDIR": "/tmp/matplotlib",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        "command": command,
        "returncode": completed.returncode,
        "seconds": time.monotonic() - started,
        "stdout_tail": completed.stdout[-4000:],
        "stderr_tail": completed.stderr[-4000:],
    }


def verify_raw_checksums() -> dict:
    index = REPO / "raw" / "confirmatory" / "CHECKSUMS.txt"
    checked = 0
    mismatches: list[str] = []
    for line in index.read_text(encoding="utf-8").splitlines():
        expected, relative = line.split("  ", 1)
        path = index.parent / relative
        checked += 1
        if not path.is_file() or sha256(path) != expected:
            mismatches.append(relative)
    return {"checked": checked, "mismatches": mismatches}


def compare_assets(generated: Path, canonical: Path) -> list[dict]:
    comparisons: list[dict] = []
    for candidate in sorted(generated.iterdir()):
        if not candidate.is_file() or candidate.name in {
            "paper_assets_manifest.json",
            "CHECKSUMS.txt",
        }:
            continue
        reference = canonical / candidate.name
        generated_hash = sha256(candidate)
        reference_hash = sha256(reference) if reference.is_file() else None
        normalized_text_exact = None
        if candidate.suffix.lower() in {".csv", ".tex"} and reference.is_file():
            generated_text = candidate.read_text(encoding="utf-8").replace("\r\n", "\n")
            reference_text = reference.read_text(encoding="utf-8").replace("\r\n", "\n")
            normalized_text_exact = generated_text == reference_text
        comparisons.append(
            {
                "path": candidate.name,
                "kind": candidate.suffix.lower(),
                "generated_sha256": generated_hash,
                "canonical_sha256": reference_hash,
                "exact": generated_hash == reference_hash,
                "normalized_text_exact": normalized_text_exact,
            }
        )
    return comparisons


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    raw = verify_raw_checksums()
    tests = run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/unit/test_confirmatory.py",
            "tests/unit/test_frontier.py",
            "tests/unit/test_the_one_crosscheck.py",
        ]
    )
    generated = OUT / "paper_assets"
    assets = run(
        [
            sys.executable,
            "-m",
            "aidmesh.cli",
            "paper-assets",
            "--results",
            str(REPO / "results" / "confirmatory"),
            "--output",
            str(generated),
        ]
    )
    comparisons = (
        compare_assets(generated, REPO / "results" / "confirmatory" / "paper_assets")
        if assets["returncode"] == 0
        else []
    )
    data_exact = all(
        item["normalized_text_exact"]
        for item in comparisons
        if item["kind"] in {".csv", ".tex"}
    )
    report = {
        "schema_version": 1,
        "label": "single_host_clean_room_reproduction",
        "independent_reproduction": False,
        "completed_utc": datetime.now(UTC).isoformat(),
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
            "python_hash_seed": os.environ.get("PYTHONHASHSEED"),
            "pytest": importlib.metadata.version("pytest"),
            "requirements_lock_sha256": sha256(REPO / "requirements.lock"),
            "repo_read_only_mount": True,
            "image": "python:3.12.11-slim-bookworm",
            "image_digest": (
                "sha256:519591d6871b7bc437060736b9f7456b"
                "8731f1499a57e22e6c285135ae657bf7"
            ),
        },
        "raw_integrity": raw,
        "focused_tests": tests,
        "paper_assets": assets,
        "asset_comparisons": comparisons,
        "acceptance": {
            "raw_5300_clean": raw["checked"] == 5300 and not raw["mismatches"],
            "focused_tests_pass": tests["returncode"] == 0,
            "asset_generation_pass": assets["returncode"] == 0,
            "csv_and_tex_exact": data_exact,
        },
        "limitations": [
            "One physical host and operator; not independent or second-host evidence.",
            "Regenerates analysis/presentation assets from frozen processed evidence; "
            "does not rerun all 2,650 simulations.",
            "PNG hashes may differ across OS/font/rendering stacks without changing numeric data.",
        ],
    }
    (OUT / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    accepted = all(report["acceptance"].values())
    (OUT / "REPORT.md").write_text(
        "\n".join(
            [
                "# Single-host clean-room reproduction",
                "",
                f"Status: {'PASS' if accepted else 'FAIL'}",
                "",
                "This is `single_host_clean_room_reproduction`, not independent reproduction.",
                f"Raw files checked: {raw['checked']}; mismatches: {len(raw['mismatches'])}.",
                f"Focused tests return code: {tests['returncode']}.",
                f"Paper-assets return code: {assets['returncode']}.",
                f"CSV/TeX exact match: {data_exact}.",
                "",
                "See `report.json` for commands, environment, hashes, and per-file comparison.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
