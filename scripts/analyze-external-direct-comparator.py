"""Reanalyze the frozen external trace-family matrix with the paper's direct comparator.

This is a descriptive supporting analysis. The 20 seeds within a context are workload
overlays on one contact trace, not independent field replicates, so this script reports
paired point summaries and ranges without population confidence intervals.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
INPUT = (
    ROOT
    / "results"
    / "tdsc_full_20260822"
    / "primary_temporal_gate_v4"
    / "bow_primary_runs.csv"
)
OUTPUT = (
    ROOT
    / "results"
    / "elsevier_remediation_20260901"
    / "external_context_direct_comparator_v2"
)
POLICIES = ("adatransfer", "robust_static")
PAIR_KEYS = [
    "family",
    "registered_context",
    "context",
    "seed",
    "cap",
    "quorum_availability",
    "calibration_fraction",
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    frame = pd.read_csv(INPUT)
    selected = frame.loc[frame["policy"].isin(POLICIES)].copy()
    duplicates = selected.duplicated(PAIR_KEYS + ["policy"], keep=False)
    if duplicates.any():
        raise ValueError("direct-comparator keys are not unique")

    completion = selected.pivot(index=PAIR_KEYS, columns="policy", values="completion_rate")
    completion = completion.dropna(subset=list(POLICIES)).reset_index()
    completion["completion_delta"] = (
        completion["adatransfer"] - completion["robust_static"]
    )

    ada = selected.loc[selected["policy"] == "adatransfer"].set_index(PAIR_KEYS)
    ada = ada.loc[pd.MultiIndex.from_frame(completion[PAIR_KEYS])].reset_index()
    for column in (
        "transfers_started",
        "transfers_delivered",
        "transfers_stranded",
        "risk_cap_violation",
    ):
        completion[f"ada_{column}"] = ada[column].to_numpy()

    rows: list[dict[str, object]] = []
    for (family, context), group in completion.groupby(
        ["family", "registered_context"], sort=True
    ):
        started = int(group["ada_transfers_started"].sum())
        delivered = int(group["ada_transfers_delivered"].sum())
        stranded = int(group["ada_transfers_stranded"].sum())
        rows.append(
            {
                "family": family,
                "context": context,
                "paired_cells": int(len(group)),
                "overlay_seeds": int(group["seed"].nunique()),
                "adatransfer_completion": float(group["adatransfer"].mean()),
                "robust_static_completion": float(group["robust_static"].mean()),
                "direct_completion_delta": float(group["completion_delta"].mean()),
                "paired_delta_min": float(group["completion_delta"].min()),
                "paired_delta_max": float(group["completion_delta"].max()),
                "transfers_started": started,
                "transfers_delivered": delivered,
                "transfers_stranded": stranded,
                "delivered_fraction": delivered / started if started else 0.0,
                "stranded_fraction": stranded / started if started else 0.0,
                "risk_cap_violations": int(group["ada_risk_cap_violation"].sum()),
            }
        )

    summary = pd.DataFrame(rows).sort_values(["family", "context"])
    if not (summary["direct_completion_delta"] <= 1e-12).all():
        raise AssertionError("expected every external direct context effect to be nonpositive")
    if int(summary["risk_cap_violations"].sum()) != 0:
        raise AssertionError("external direct-comparator rows contain a risk-cap violation")

    OUTPUT.mkdir(parents=True, exist_ok=True)
    pairs_path = OUTPUT / "external_context_direct_pairs.csv"
    summary_path = OUTPUT / "external_context_direct_effects.csv"
    completion.to_csv(pairs_path, index=False)
    summary.to_csv(summary_path, index=False)

    manifest = {
        "schema_version": 2,
        "analysis_role": "descriptive supporting external-validity reanalysis",
        "source": str(INPUT.relative_to(ROOT)).replace("\\", "/"),
        "source_sha256": sha256(INPUT),
        "policies": list(POLICIES),
        "paired_cells": int(len(completion)),
        "contexts": int(len(summary)),
        "families": int(summary["family"].nunique()),
        "all_context_effects_nonpositive": True,
        "risk_cap_violations": 0,
        "inference_boundary": (
            "Seeds are paired workload overlays within a fixed context, not independent "
            "field replicates; no population confidence interval is reported."
        ),
    }
    manifest_path = OUTPUT / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    checksum_paths = (pairs_path, summary_path, manifest_path)
    checksum_text = "\n".join(
        f"{sha256(path)}  {path.name}" for path in checksum_paths
    )
    (OUTPUT / "CHECKSUMS.sha256").write_text(checksum_text + "\n", encoding="utf-8")
    print(summary.to_string(index=False))
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
