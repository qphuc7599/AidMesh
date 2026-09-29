"""Family-aware BOW campaign over provenance-registered public traces."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from aidmesh.experiments.bow_temporal import ONLINE_POLICIES, run_bow_temporal_campaign


def load_trace_family_registry(path: Path, *, workspace_root: Path) -> dict[str, Any]:
    registry = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(registry, dict) or not registry.get("registry_id"):
        raise ValueError("trace-family registry requires registry_id")
    contexts = registry.get("contexts")
    if not isinstance(contexts, list) or not contexts:
        raise ValueError("trace-family registry requires contexts")
    seen: set[tuple[str, str]] = set()
    normalized = []
    for item in contexts:
        if not isinstance(item, dict):
            raise ValueError("registry context entries must be mappings")
        family = str(item.get("family", ""))
        context = str(item.get("context", ""))
        trace_path = (workspace_root / str(item.get("path", ""))).resolve()
        key = (family, context)
        if not family or not context or key in seen:
            raise ValueError("family/context identifiers must be nonempty and unique")
        if not (trace_path / "contacts.csv").is_file():
            raise FileNotFoundError(trace_path / "contacts.csv")
        seen.add(key)
        normalized.append({**item, "resolved_path": trace_path})
    return {**registry, "contexts": normalized}


def run_bow_trace_family_campaign(
    registry_path: Path,
    *,
    workspace_root: Path,
    primary_only: bool = True,
    seeds: tuple[int, ...] | None = None,
    cap_fractions: tuple[float, ...] | None = None,
    quorum_availabilities: tuple[float, ...] = (0.35, 0.65, 0.90),
    policies: tuple[str, ...] = ONLINE_POLICIES,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    registry = load_trace_family_registry(registry_path, workspace_root=workspace_root)
    selected = [
        item
        for item in registry["contexts"]
        if not primary_only or bool(item.get("primary", False))
    ]
    campaign_seeds = seeds or tuple(int(value) for value in registry["seeds"])
    caps = cap_fractions or tuple(float(value) for value in registry["cap_fractions"])
    frames = []
    manifests = {}
    for item in selected:
        trace_path: Path = item["resolved_path"]
        frame, manifest = run_bow_temporal_campaign(
            trace_path.parent,
            contexts=(trace_path.name,),
            seeds=campaign_seeds,
            cap_fractions=caps,
            quorum_availabilities=quorum_availabilities,
            policies=policies,
            calibration_fraction=float(registry["calibration_fraction"]),
            buffer_bytes=int(registry["buffer_bytes"]),
        )
        frame["family"] = item["family"]
        frame["registered_context"] = item["context"]
        frame["profile"] = item["profile"]
        frames.append(frame)
        manifests[f"{item['family']}:{item['context']}"] = manifest
    combined = pd.concat(frames, ignore_index=True)
    manifest = {
        "schema_version": 1,
        "registry_id": registry["registry_id"],
        "registry_sha256": hashlib.sha256(registry_path.read_bytes()).hexdigest(),
        "primary_only": primary_only,
        "families": sorted(combined["family"].unique()),
        "contexts": [
            {
                "family": item["family"],
                "context": item["context"],
                "profile": item["profile"],
                "path": str(item["resolved_path"]),
            }
            for item in selected
        ],
        "seeds": list(campaign_seeds),
        "cap_fractions": list(caps),
        "quorum_availabilities": list(quorum_availabilities),
        "policies": list(policies),
        "family_inference_rule": (
            "average contexts within family before comparing families; contexts are "
            "not treated as independent dataset families"
        ),
        "nested_manifests": manifests,
        "rows": len(combined),
    }
    return combined, manifest


def family_balanced_summary(frame: pd.DataFrame) -> pd.DataFrame:
    context_level = (
        frame.groupby(["family", "registered_context", "policy"], as_index=False)[
            ["completion_rate", "end_to_end_settlement_probability"]
        ]
        .mean()
    )
    return (
        context_level.groupby(["family", "policy"], as_index=False)
        .agg(
            contexts=("registered_context", "nunique"),
            mean_completion_rate=("completion_rate", "mean"),
            mean_settlement_probability=("end_to_end_settlement_probability", "mean"),
        )
    )


def write_bow_trace_family_campaign(
    registry_path: Path,
    output: Path,
    *,
    workspace_root: Path,
    primary_only: bool = True,
    seeds: tuple[int, ...] | None = None,
    cap_fractions: tuple[float, ...] | None = None,
    quorum_availabilities: tuple[float, ...] = (0.35, 0.65, 0.90),
    policies: tuple[str, ...] = ONLINE_POLICIES,
) -> dict[str, Path]:
    output.mkdir(parents=True, exist_ok=True)
    frame, manifest = run_bow_trace_family_campaign(
        registry_path,
        workspace_root=workspace_root,
        primary_only=primary_only,
        seeds=seeds,
        cap_fractions=cap_fractions,
        quorum_availabilities=quorum_availabilities,
        policies=policies,
    )
    summary = family_balanced_summary(frame)
    runs = output / "bow_trace_family_runs.csv"
    summary_path = output / "bow_trace_family_balanced_summary.csv"
    manifest_path = output / "manifest.json"
    frame.to_csv(runs, index=False, lineterminator="\n")
    summary.to_csv(summary_path, index=False, lineterminator="\n")
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    checksums = output / "CHECKSUMS.sha256"
    checksums.write_text(
        "".join(
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
            for path in (runs, summary_path, manifest_path)
        ),
        encoding="ascii",
    )
    return {
        "runs": runs,
        "summary": summary_path,
        "manifest": manifest_path,
        "checksums": checksums,
    }
