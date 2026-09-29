"""Post-freeze temporal-matched core campaign.

Replays the frozen confirmatory core subset under abstract and temporal-contact
settlement on identical seeds, scenarios, and methods.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import pandas as pd
import yaml

from aidmesh.config import load_scenario
from aidmesh.simulator.experiment import HOUR_US, run_experiment


DEFAULT_SCENARIOS = (
    "configs/confirmatory/core_w08_p24.yaml",
    "configs/confirmatory/wit_06.yaml",
    "configs/confirmatory/part_06h.yaml",
    "configs/confirmatory/part_48h.yaml",
)
DEFAULT_METHODS = (
    "online_only",
    "aidmesh_static_rights",
    "aidmesh_transferable_rights",
)


@dataclass(frozen=True)
class TemporalMatchedManifest:
    registry_path: str
    scenarios: tuple[str, ...]
    methods: tuple[str, ...]
    seed_start: int
    seeds: int
    rows: int


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_temporal_matched_core(
    registry: dict[str, Any],
    *,
    write_raw: bool = False,
) -> tuple[pd.DataFrame, pd.DataFrame, TemporalMatchedManifest]:
    scenarios = tuple(registry.get("scenarios", DEFAULT_SCENARIOS))
    methods = tuple(registry.get("methods", DEFAULT_METHODS))
    seed_start = int(registry.get("seed_start", 8000))
    seeds = int(registry.get("seeds", 20))
    trace_contacts = registry.get("trace_contacts")
    rows: list[dict[str, Any]] = []
    for scenario_path in scenarios:
        base = load_scenario(Path(scenario_path))
        trace_path = None
        if trace_contacts and scenario_path in trace_contacts:
            trace_path = Path(trace_contacts[scenario_path])
        elif trace_contacts and "default" in trace_contacts:
            trace_path = Path(trace_contacts["default"])
        for offset in range(seeds):
            seed = seed_start + offset
            config = {**base, "seed": seed}
            for method in methods:
                for settlement_mode in ("abstract", "temporal"):
                    result = run_experiment(
                        config,
                        method,
                        experiment_id="SUPPORT-TMC",
                        settlement_mode=settlement_mode,
                        trace_path=trace_path if settlement_mode == "temporal" else None,
                    )
                    rows.append(
                        {
                            "scenario_id": result.scenario_id,
                            "scenario_config": scenario_path,
                            "seed": seed,
                            "method": method,
                            "settlement_mode": settlement_mode,
                            "completion_rate": result.completion_rate,
                            "mean_settlement_hours": result.mean_settlement_hours,
                            "rmst_hours": result.rmst_hours,
                            "exposure_cap_ratio": result.exposure_cap_ratio,
                            "settled_receipts": result.settled_receipts,
                            "pending_receipts": result.pending_receipts,
                        }
                    )
    frame = pd.DataFrame(rows)
    summary_rows: list[dict[str, Any]] = []
    for (scenario_id, method), group in frame.groupby(["scenario_id", "method"]):
        abstract = group[group["settlement_mode"] == "abstract"]
        temporal = group[group["settlement_mode"] == "temporal"]
        abstract_mean = float(abstract["completion_rate"].mean())
        temporal_mean = float(temporal["completion_rate"].mean())
        delta = temporal_mean - abstract_mean
        sign_match = (delta >= 0) == (
            float(group[group["settlement_mode"] == "abstract"]["completion_rate"].mean())
            >= float(
                group[group["method"] == "online_only"]["completion_rate"].mean()
                if "online_only" in set(group["method"])
                else abstract_mean
            )
        )
        summary_rows.append(
            {
                "scenario_id": scenario_id,
                "method": method,
                "abstract_completion_mean": abstract_mean,
                "temporal_completion_mean": temporal_mean,
                "completion_delta_temporal_minus_abstract": delta,
                "abstract_rmst_mean": float(abstract["rmst_hours"].mean()),
                "temporal_rmst_mean": float(temporal["rmst_hours"].mean()),
                "abstract_delay_mean_hours": float(
                    abstract["mean_settlement_hours"].dropna().mean()
                    if abstract["mean_settlement_hours"].notna().any()
                    else float("nan")
                ),
                "temporal_delay_mean_hours": float(
                    temporal["mean_settlement_hours"].dropna().mean()
                    if temporal["mean_settlement_hours"].notna().any()
                    else float("nan")
                ),
                "paired_seeds": seeds,
                "directionally_robust": delta >= -0.02,
            }
        )
    summary = pd.DataFrame(summary_rows)
    manifest = TemporalMatchedManifest(
        registry_path=str(registry.get("_path", "inline")),
        scenarios=scenarios,
        methods=methods,
        seed_start=seed_start,
        seeds=seeds,
        rows=len(frame),
    )
    if write_raw:
        pass
    return frame, summary, manifest


def write_temporal_matched_core(
    frame: pd.DataFrame,
    summary: pd.DataFrame,
    manifest: TemporalMatchedManifest,
    output: Path,
) -> dict[str, Path]:
    output.mkdir(parents=True, exist_ok=True)
    runs_path = output / "temporal_matched_runs.csv"
    summary_path = output / "temporal_matched_summary.csv"
    frame.to_csv(runs_path, index=False)
    summary.to_csv(summary_path, index=False)
    manifest_path = output / "manifest.json"
    manifest_path.write_text(
        json.dumps(asdict(manifest), indent=2) + "\n",
        encoding="utf-8",
    )
    fig, axis = plt.subplots(figsize=(8, 4.5))
    pivot = summary.pivot(index="scenario_id", columns="method", values="completion_delta_temporal_minus_abstract")
    pivot.plot(kind="bar", ax=axis, rot=20)
    axis.axhline(0.0, color="black", linewidth=0.8)
    axis.set_ylabel("Temporal minus abstract completion")
    axis.set_title("Temporal-matched core drift (supporting 2026Q3)")
    axis.legend(fontsize=8, loc="best")
    fig.tight_layout()
    figure_path = output / "temporal_matched_drift.pdf"
    fig.savefig(figure_path, bbox_inches="tight")
    plt.close(fig)
    checksums = output / "CHECKSUMS.sha256"
    lines = []
    for path in sorted(output.iterdir()):
        if path.is_file():
            lines.append(f"{_sha256(path)}  {path.name}")
    checksums.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {
        "runs": runs_path,
        "summary": summary_path,
        "manifest": manifest_path,
        "figure": figure_path,
        "checksums": checksums,
    }


def load_temporal_matched_registry(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"registry must be a mapping: {path}")
    payload["_path"] = str(path)
    return payload
