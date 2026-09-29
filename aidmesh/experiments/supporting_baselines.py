"""Strong baseline pack and operating-envelope figure for supporting 2026Q3."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml

from aidmesh.config import load_scenario
from aidmesh.simulator.experiment import run_experiment


BASELINE_METHODS = (
    "online_only",
    "fixed_station",
    "periodic_sync",
    "optimized_static_placement",
    "aidmesh_static_rights",
    "aidmesh_transferable_rights",
)


@dataclass(frozen=True)
class BaselineManifest:
    registry_path: str
    scenarios: tuple[str, ...]
    methods: tuple[str, ...]
    seed_start: int
    seeds: int


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_baseline_pack(registry: dict[str, Any]) -> tuple[pd.DataFrame, BaselineManifest]:
    scenarios = tuple(registry.get("scenarios", ()))
    methods = tuple(registry.get("methods", BASELINE_METHODS))
    seed_start = int(registry.get("seed_start", 8100))
    seeds = int(registry.get("seeds", 25))
    rows: list[dict[str, Any]] = []
    for scenario_path in scenarios:
        base = load_scenario(Path(scenario_path))
        for offset in range(seeds):
            config = {**base, "seed": seed_start + offset}
            for method in methods:
                result = run_experiment(config, method, experiment_id="SUPPORT-BL")
                rows.append(
                    {
                        "scenario_id": result.scenario_id,
                        "scenario_config": scenario_path,
                        "seed": config["seed"],
                        "method": method,
                        "completion_rate": result.completion_rate,
                        "exposure_cap_ratio": result.exposure_cap_ratio,
                        "rmst_hours": result.rmst_hours,
                        "witness_availability": base["quorum"]["witness_availability"],
                        "partition_hours": base["network"]["partition_us"] / 3_600_000_000,
                    }
                )
    manifest = BaselineManifest(
        registry_path=str(registry.get("_path", "inline")),
        scenarios=scenarios,
        methods=methods,
        seed_start=seed_start,
        seeds=seeds,
    )
    return pd.DataFrame(rows), manifest


def write_baseline_pack(
    frame: pd.DataFrame,
    manifest: BaselineManifest,
    output: Path,
) -> dict[str, Path]:
    output.mkdir(parents=True, exist_ok=True)
    runs_path = output / "baseline_runs.csv"
    frame.to_csv(runs_path, index=False)
    summary = (
        frame.groupby(["scenario_id", "method"], as_index=False)
        .agg(
            completion_mean=("completion_rate", "mean"),
            completion_std=("completion_rate", "std"),
            exposure_mean=("exposure_cap_ratio", "mean"),
            rmst_mean=("rmst_hours", "mean"),
        )
        .sort_values(["scenario_id", "completion_mean"], ascending=[True, False])
    )
    summary_path = output / "baseline_summary.csv"
    summary.to_csv(summary_path, index=False)
    manifest_path = output / "manifest.json"
    manifest_path.write_text(json.dumps(asdict(manifest), indent=2) + "\n", encoding="utf-8")

    envelope = frame.groupby(["witness_availability", "partition_hours", "method"], as_index=False).agg(
        completion_mean=("completion_rate", "mean")
    )
    aidmesh = envelope[envelope["method"] == "aidmesh_transferable_rights"].rename(
        columns={"completion_mean": "aidmesh_completion"}
    )
    merged = envelope.merge(
        aidmesh[["witness_availability", "partition_hours", "aidmesh_completion"]],
        on=["witness_availability", "partition_hours"],
        how="left",
    )
    merged["aidmesh_wins"] = merged["completion_mean"] >= merged["aidmesh_completion"]
    envelope_path = output / "operating_envelope.csv"
    merged.to_csv(envelope_path, index=False)

    fig, axis = plt.subplots(figsize=(7.5, 5))
    methods = sorted(frame["method"].unique())
    colors = plt.cm.tab10(np.linspace(0, 1, len(methods)))
    for color, method in zip(colors, methods, strict=True):
        subset = frame[frame["method"] == method]
        axis.scatter(
            subset["partition_hours"],
            subset["witness_availability"],
            s=35 + 180 * subset["completion_rate"],
            alpha=0.55,
            label=method,
            c=[color],
        )
    axis.set_xlabel("Partition hours")
    axis.set_ylabel("Witness availability")
    axis.set_title("Operating envelope (marker size ∝ completion)")
    axis.legend(fontsize=7, loc="best")
    fig.tight_layout()
    figure_path = output / "operating_envelope.pdf"
    fig.savefig(figure_path, bbox_inches="tight")
    plt.close(fig)

    checksums = output / "CHECKSUMS.sha256"
    checksums.write_text(
        "\n".join(f"{_sha256(path)}  {path.name}" for path in sorted(output.iterdir()) if path.is_file())
        + "\n",
        encoding="utf-8",
    )
    return {
        "runs": runs_path,
        "summary": summary_path,
        "envelope": envelope_path,
        "figure": figure_path,
        "manifest": manifest_path,
        "checksums": checksums,
    }


def load_baseline_registry(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"registry must be a mapping: {path}")
    payload["_path"] = str(path)
    return payload
