"""Device-class P1 proving benchmark with throttled CPU profiles."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from aidmesh.experiments.zk_benchmark import run_zk_benchmark


@dataclass(frozen=True)
class DeviceProfile:
    profile_id: str
    description: str
    affinity_mask: str | None = None
    nice_level: int | None = None


DEFAULT_PROFILES = (
    DeviceProfile("workstation", "Unthrottled developer workstation"),
    DeviceProfile("mid_range_android", "WSL CPU affinity mask simulating 4 mid cores", affinity_mask="0,1,2,3"),
    DeviceProfile("raspberry_pi4", "Nice-level throttle simulating low-power ARM", nice_level=10),
)


@dataclass(frozen=True)
class DeviceBenchmarkManifest:
    registry_path: str
    profiles: tuple[str, ...]
    samples: int


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run_profiled_benchmark(profile: DeviceProfile, samples: int, root: Path) -> dict[str, Any]:
    prefix: list[str] = []
    if profile.affinity_mask and platform.system().lower() == "linux":
        prefix = ["taskset", "-c", profile.affinity_mask]
    elif profile.nice_level is not None and platform.system().lower() == "linux":
        prefix = ["nice", "-n", str(profile.nice_level)]
    started = time.perf_counter()
    if prefix:
        subprocess.run(prefix + ["true"], check=False)
    result = run_zk_benchmark(root=root, iterations=samples, warmup=1)
    elapsed = time.perf_counter() - started
    prove = result[result["metric"] == "groth16_fullprove_ms"].iloc[0]
    p95 = result[result["metric"] == "groth16_fullprove_p95_ms"]
    p99 = result[result["metric"] == "groth16_fullprove_p99_ms"]
    return {
        "profile_id": profile.profile_id,
        "description": profile.description,
        "host": platform.platform(),
        "samples": samples,
        "median_ms": float(prove["value"]),
        "p95_ms": float(p95.iloc[0]["value"]) if not p95.empty else float("nan"),
        "p99_ms": float(p99.iloc[0]["value"]) if not p99.empty else float("nan"),
        "wall_seconds": elapsed,
        "throttle_prefix": " ".join(prefix) if prefix else "none",
    }


def run_device_benchmark(registry: dict[str, Any], root: Path | None = None) -> tuple[pd.DataFrame, DeviceBenchmarkManifest]:
    root = root or Path(__file__).resolve().parents[2]
    samples = int(registry.get("samples", 8))
    profiles = DEFAULT_PROFILES
    rows = [_run_profiled_benchmark(profile, samples, root) for profile in profiles]
    manifest = DeviceBenchmarkManifest(
        registry_path=str(registry.get("_path", "inline")),
        profiles=tuple(profile.profile_id for profile in profiles),
        samples=samples,
    )
    return pd.DataFrame(rows), manifest


def write_device_benchmark(
    frame: pd.DataFrame,
    manifest: DeviceBenchmarkManifest,
    output: Path,
) -> dict[str, Path]:
    output.mkdir(parents=True, exist_ok=True)
    csv_path = output / "device_benchmark.csv"
    frame.to_csv(csv_path, index=False)
    manifest_path = output / "manifest.json"
    manifest_path.write_text(json.dumps(asdict(manifest), indent=2) + "\n", encoding="utf-8")
    checksums = output / "CHECKSUMS.sha256"
    checksums.write_text(
        "\n".join(f"{_sha256(path)}  {path.name}" for path in sorted(output.iterdir()) if path.is_file())
        + "\n",
        encoding="utf-8",
    )
    return {"csv": csv_path, "manifest": manifest_path, "checksums": checksums}


def load_device_benchmark_registry(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"registry must be a mapping: {path}")
    payload["_path"] = str(path)
    return payload
