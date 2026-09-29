"""Retrospective non-zero practical-effect sensitivity for frontier evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd
import yaml


def evaluate_frontier_practicality(registry_path: Path, output: Path) -> dict[str, Any]:
    registry = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    if registry["status"] != "retrospective_sensitivity_not_preregistered":
        raise ValueError("frontier practicality amendment must retain retrospective label")
    margin = float(registry["normalized_hypervolume_margin"])
    rows: list[dict[str, Any]] = []

    controlled = pd.read_csv(registry["inputs"]["controlled_quorum"])
    for row in controlled.to_dict("records"):
        rows.append(
            {
                "evidence_level": "frozen_controlled_quorum",
                "stratum": row["quorum_family"],
                "mean_effect": float(row["mean_difference"]),
                "ci_lower": float(row["ci_low"]),
                "ci_upper": float(row["ci_high"]),
            }
        )

    heldout = pd.read_csv(registry["inputs"]["heldout_context"])
    heldout = heldout[heldout["context"] != "pooled_descriptive"]
    for row in heldout.to_dict("records"):
        rows.append(
            {
                "evidence_level": "heldout_temporal_context",
                "stratum": row["context"],
                "mean_effect": float(
                    row["mean_transferable_minus_static_hypervolume"]
                ),
                "ci_lower": float(row["bootstrap_95ci_lower"]),
                "ci_upper": float(row["bootstrap_95ci_upper"]),
            }
        )

    families = pd.read_csv(registry["inputs"]["independent_family"])
    for row in families.to_dict("records"):
        rows.append(
            {
                "evidence_level": "independent_trace_family",
                "stratum": row["family"],
                "mean_effect": float(row["mean_effect"]),
                "ci_lower": float(row["bootstrap_95ci_lower"]),
                "ci_upper": float(row["bootstrap_95ci_upper"]),
            }
        )

    for row in rows:
        row["practical_margin"] = margin
        row["statistically_directional"] = row["ci_lower"] > 0
        row["practically_meaningful"] = row["ci_lower"] > margin
        row["decision"] = (
            "passes_retrospective_practical_margin"
            if row["practically_meaningful"]
            else "does_not_pass_retrospective_practical_margin"
        )

    output.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(rows)
    csv_path = output / "frontier_practicality.csv"
    frame.to_csv(csv_path, index=False, lineterminator="\n")
    payload = {
        "schema_version": 1,
        "analysis_id": registry["analysis_id"],
        "status": registry["status"],
        "margin": margin,
        "decision_rule": registry["decision_rule"],
        "rows": len(rows),
        "directional_positive": int(frame["statistically_directional"].sum()),
        "practically_meaningful": int(frame["practically_meaningful"].sum()),
        "decisions": rows,
    }
    manifest_path = output / "manifest.json"
    manifest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    checksums = []
    for path in (csv_path, manifest_path):
        checksums.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}")
    (output / "CHECKSUMS.sha256").write_text("\n".join(checksums) + "\n", encoding="ascii")
    return payload

