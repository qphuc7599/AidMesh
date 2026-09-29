"""Analyze the frozen T13 matched Wang-style seen-tag campaign."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from aidmesh.analysis.pipeline import load_run_summaries
from aidmesh.experiments.confirmatory import (
    analyze_confirmatory_registry,
    load_confirmatory_registry,
)

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "configs/postreview_20260929/t13_wang_seen_tag_matched_physical_v2.yaml"
RAW = ROOT / "results/t13_wang_seen_tag_matched_20260929/raw"
OUT = ROOT / "results/t13_wang_seen_tag_matched_20260929/analysis"
METHODS = (
    "aidmesh_transferable_rights",
    "wang_seen_tag_sync_1h",
    "wang_seen_tag_sync_6h",
    "wang_seen_tag_sync_24h",
    "first_seen_dtn",
)


def main() -> int:
    registry = load_confirmatory_registry(REGISTRY, root=ROOT)
    paths = analyze_confirmatory_registry(registry, input_root=RAW, output=OUT)
    frame = load_run_summaries(RAW)
    expected = sum(row.seed_count * len(row.methods) for row in registry.scenarios)
    grouped = (
        frame.groupby(["scenario_id", "method"], sort=True)
        .agg(
            seeds=("seed", "nunique"),
            completion_mean=("completion_rate", "mean"),
            exposure_mean=("physical_duplicate_exposure", "mean"),
            exposure_max=("physical_duplicate_exposure", "max"),
            sync_payload_mean=("seen_tag_payload_bytes", "mean"),
            sync_rounds_mean=("seen_tag_sync_rounds", "mean"),
        )
        .reset_index()
    )
    grouped.to_csv(OUT / "scenario_method_summary.csv", index=False)

    paired = frame.pivot(
        index=["scenario_id", "seed"], columns="method", values="physical_duplicate_exposure"
    )
    complete = paired.dropna(subset=list(METHODS))
    ordering = (
        (complete["wang_seen_tag_sync_1h"] <= complete["wang_seen_tag_sync_6h"])
        & (complete["wang_seen_tag_sync_6h"] <= complete["wang_seen_tag_sync_24h"])
        & (complete["wang_seen_tag_sync_24h"] <= complete["first_seen_dtn"])
    )
    seen = frame[frame["method"].str.startswith("wang_seen_tag_sync_")]
    seen_completion = seen.pivot(
        index=["scenario_id", "seed"], columns="method", values="completion_rate"
    )
    p60 = frame[frame["scenario_id"] == "CONF_PART_60H"].pivot(
        index="seed", columns="method", values="physical_duplicate_exposure"
    )
    contrasts = pd.read_csv(paths["contrasts"])
    report = {
        "registryId": registry.registry_id,
        "registrySha256": registry.registry_hash,
        "expectedCells": expected,
        "observedCells": int(len(frame)),
        "failedCells": int(expected - len(frame)),
        "methods": list(METHODS),
        "checks": {
            "completeCoverage": len(frame) == expected,
            "fiveMethodsEveryScenario": bool(
                frame.groupby("scenario_id")["method"].nunique().eq(len(METHODS)).all()
            ),
            "uniqueScenarioMethodSeed": not frame.duplicated(
                ["scenario_id", "method", "seed"]
            ).any(),
            "zeroRiskConservationFailures": bool(frame["risk_rights_conserved"].all()),
            "zeroSafeQuorumConflicts": bool(
                frame["conflicting_quorum_certificates"].fillna(0).eq(0).all()
            ),
            "seenTagCompletionInvariantAcrossFrequencies": bool(
                seen_completion.nunique(axis=1).eq(1).all()
            ),
            "exposureMonotoneWithSyncInterval": bool(ordering.all()),
            "sixtyHourPartitionEqualsUnsynchronizedFirstSeen": bool(
                p60[
                    [
                        "wang_seen_tag_sync_1h",
                        "wang_seen_tag_sync_6h",
                        "wang_seen_tag_sync_24h",
                        "first_seen_dtn",
                    ]
                ]
                .nunique(axis=1)
                .eq(1)
                .all()
            ),
        },
        "primaryCoreContrasts": contrasts[
            contrasts["comparison_id"].isin(
                ["T13_AIDMESH_VS_WANG6_CORE", "T13_WANG6_VS_FIRSTSEEN_CORE"]
            )
        ].to_dict(orient="records"),
        "interpretationBoundary": (
            "Wang-style reconstruction from the full-text multi-site discussion; "
            "not the original authors' implementation. Synchronization transport and "
            "frequency are explicit evaluation assumptions."
        ),
    }
    report["allChecksPassed"] = all(report["checks"].values())
    (OUT / "summary.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["allChecksPassed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
