"""Build a failure-inclusive evidence ledger for the frozen original-registry reruns.

This is a post-run reporting step.  It does not alter the frozen simulator,
registries, endpoints, multiplicity rule, or confidence intervals.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results" / "postreview_original_registry_20260910"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def keyed_contrasts(rows: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = f"{row['comparison_id']}::{row['metric']}"
        result[key] = {
            "pairs": int(row["pairs"]),
            "mean_difference": float(row["mean_difference"]),
            "ci_low": float(row["ci_low"]),
            "ci_high": float(row["ci_high"]),
            "holm_adjusted_p_value": float(row["holm_adjusted_p_value"]),
            "alternative": row["alternative"],
            "scenarios": row["scenarios"],
        }
    return result


def frontier_rows(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    return [
        {
            "quorum_family": row["quorum_family"],
            "pairs": int(row["pairs"]),
            "mean_difference": float(row["mean_difference"]),
            "ci_low": float(row["ci_low"]),
            "ci_high": float(row["ci_high"]),
            "holm_adjusted_p_value": float(row["holm_adjusted_p_value"]),
            "passes_registered_rule": (
                float(row["ci_low"]) > 0.0
                and float(row["holm_adjusted_p_value"]) < 0.05
            ),
        }
        for row in rows
    ]


def integrity_passed(coverage: dict[str, Any]) -> bool:
    return (
        coverage["expected_cells"] == 2_650
        and coverage["completed_cells"] == 2_650
        and coverage["failed_cells"] == 0
        and coverage["missing_cells"] == 0
        and coverage["invalid_cells"] == 0
        and coverage["extra_cells"] == 0
        and coverage["safe_method_violations"] == 0
    )


def main() -> None:
    freeze_path = RESULTS / "PRE_RUN_FREEZE.json"
    replay_coverage_path = RESULTS / "replay_coverage.json"
    holdout_coverage_path = RESULTS / "holdout_coverage.json"
    replay_execution_path = RESULTS / "replay_raw" / "EXECUTION_REPORT.json"
    holdout_execution_path = RESULTS / "holdout_raw" / "EXECUTION_REPORT.json"
    replay_contrasts_path = (
        RESULTS / "replay_analysis" / "confirmatory_registered_contrasts.csv"
    )
    holdout_contrasts_path = (
        RESULTS / "holdout_analysis" / "confirmatory_registered_contrasts.csv"
    )
    replay_frontier_path = (
        RESULTS / "replay_frontier" / "frontier_hypervolume_contrasts.csv"
    )
    holdout_frontier_path = (
        RESULTS / "holdout_frontier" / "frontier_hypervolume_contrasts.csv"
    )

    freeze = load_json(freeze_path)
    replay_coverage = load_json(replay_coverage_path)
    holdout_coverage = load_json(holdout_coverage_path)
    replay_execution = load_json(replay_execution_path)
    holdout_execution = load_json(holdout_execution_path)
    replay_contrasts_raw = load_csv(replay_contrasts_path)
    holdout_contrasts_raw = load_csv(holdout_contrasts_path)
    replay_frontier = frontier_rows(load_csv(replay_frontier_path))
    holdout_frontier = frontier_rows(load_csv(holdout_frontier_path))
    holdout_contrasts = keyed_contrasts(holdout_contrasts_raw)

    mismatch_cells = replay_coverage["details"]["semantic_replay_mismatches"]
    mismatch_by_method: Counter[str] = Counter()
    mismatch_by_scenario: Counter[str] = Counter()
    for item in mismatch_cells:
        scenario, method, _seed = item["cell"].split("/")
        mismatch_by_method[method] += 1
        mismatch_by_scenario[scenario] += 1

    core = holdout_contrasts["RQ1_TRANSFER_VS_ONLINE_CORE::completion_rate"]
    non_home = holdout_contrasts[
        "RQ1_TRANSFER_VS_ONLINE_CORE::non_home_completion_rate"
    ]
    rmst = holdout_contrasts["RQ1_RMST_TRANSFER_VS_ONLINE_CORE::rmst_hours"]
    witness_06 = holdout_contrasts[
        "RQ1_TRANSFER_VS_ONLINE_WIT06_BOUNDARY::completion_rate"
    ]
    witness_07 = holdout_contrasts[
        "RQ1_TRANSFER_VS_ONLINE_WIT07::completion_rate"
    ]
    partition_06 = holdout_contrasts[
        "RQ1_TRANSFER_VS_ONLINE_P06::completion_rate"
    ]
    tight_caps = holdout_contrasts[
        "RQ2_TRANSFER_VS_STATIC_TIGHT_CAPS::completion_rate"
    ]
    first_seen_completion = holdout_contrasts[
        "RQ1_COMPLETION_TRANSFER_VS_FIRSTSEEN_CORE::completion_rate"
    ]
    first_seen_exposure = holdout_contrasts[
        "RQ1_SAFETY_TRANSFER_VS_FIRSTSEEN_CORE::duplicate_exposure"
    ]

    registered_decisions = {
        "rq1_core_completion": {
            "decision": "supported",
            "passes_holm_and_practical_margin": (
                core["holm_adjusted_p_value"] < 0.05 and core["ci_low"] > 0.05
            ),
            **core,
        },
        "rq1_non_home_completion": {
            "decision": "supported",
            "passes_holm_and_practical_margin": (
                non_home["holm_adjusted_p_value"] < 0.05
                and non_home["ci_low"] > 0.05
            ),
            **non_home,
        },
        "rq1_rmst": {
            "decision": "not_supported_wrong_direction",
            "passes_registered_direction": (
                rmst["holm_adjusted_p_value"] < 0.05 and rmst["ci_high"] < 0.0
            ),
            **rmst,
        },
        "rq1_witness_0_6_boundary": {
            "decision": "negative",
            **witness_06,
        },
        "rq1_witness_0_7": {
            "decision": "supported_barely_above_practical_margin",
            "passes_holm_and_practical_margin": (
                witness_07["holm_adjusted_p_value"] < 0.05
                and witness_07["ci_low"] > 0.05
            ),
            **witness_07,
        },
        "rq1_partition_6h": {
            "decision": "negative",
            **partition_06,
        },
        "rq1_first_seen_completion_tradeoff": {
            "decision": "lower_completion_than_unsafe_control",
            **first_seen_completion,
        },
        "rq1_first_seen_exposure_tradeoff": {
            "decision": "lower_duplicate_exposure_than_unsafe_control",
            **first_seen_exposure,
        },
        "rq2_tight_cap_completion": {
            "decision": "not_supported_null",
            **tight_caps,
        },
        "rq2_frontier_hypervolume": {
            "decision": "supported_all_registered_quorum_families",
            "all_families_pass": all(
                row["passes_registered_rule"] for row in holdout_frontier
            ),
            "families": holdout_frontier,
        },
    }

    report = {
        "schema_version": "1.0.0",
        "characterization": (
            "post-run deterministic ledger of prospectively specified local replay and "
            "fresh disjoint-seed holdout; not an external preregistration"
        ),
        "software_snapshot_sha256": freeze["software_snapshot_sha256"],
        "pre_run_freeze_sha256": sha256(freeze_path),
        "seed_overlap": freeze["holdout_seed_overlap_with_original"],
        "replay": {
            "scope": replay_coverage["scope"],
            "data_integrity_and_safety_passed": integrity_passed(replay_coverage),
            "completed_cells": replay_coverage["completed_cells"],
            "failed_cells": replay_coverage["failed_cells"],
            "analysis_contrasts": len(replay_contrasts_raw),
            "frontier_families": replay_frontier,
            "historical_semantic_equivalence": False,
            "historical_semantic_mismatches": replay_coverage[
                "semantic_replay_mismatches"
            ],
            "historical_semantic_matches": (
                replay_coverage["expected_cells"]
                - replay_coverage["semantic_replay_mismatches"]
            ),
            "mismatch_by_method": dict(sorted(mismatch_by_method.items())),
            "mismatch_by_scenario": dict(sorted(mismatch_by_scenario.items())),
            "interpretation": (
                "The post-review implementation changes outcomes on the original seed/design "
                "registry; this is version drift, not a fresh holdout or an exact reproduction "
                "of the pre-review binary. The discrepancy is retained and reported."
            ),
            "execution": {
                "wall_seconds": replay_execution["wall_seconds"],
                "software_snapshot": replay_execution["software_snapshot"],
            },
        },
        "holdout": {
            "scope": holdout_coverage["scope"],
            "data_integrity_and_safety_passed": (
                integrity_passed(holdout_coverage)
                and holdout_coverage["all_checks_passed"]
            ),
            "completed_cells": holdout_coverage["completed_cells"],
            "failed_cells": holdout_coverage["failed_cells"],
            "raw_event_bytes": holdout_coverage["raw_event_bytes"],
            "compressed_event_bytes": holdout_coverage["compressed_event_bytes"],
            "analysis_contrasts": len(holdout_contrasts_raw),
            "frontier_families": len(holdout_frontier),
            "execution": {
                "wall_seconds": holdout_execution["wall_seconds"],
                "software_snapshot": holdout_execution["software_snapshot"],
            },
        },
        "registered_decisions": registered_decisions,
        "input_sha256": {
            path.relative_to(ROOT).as_posix(): sha256(path)
            for path in (
                replay_coverage_path,
                holdout_coverage_path,
                replay_execution_path,
                holdout_execution_path,
                replay_contrasts_path,
                holdout_contrasts_path,
                replay_frontier_path,
                holdout_frontier_path,
            )
        },
    }

    required = [
        report["seed_overlap"] == 0,
        report["replay"]["data_integrity_and_safety_passed"],
        report["holdout"]["data_integrity_and_safety_passed"],
        report["replay"]["completed_cells"] == 2_650,
        report["holdout"]["completed_cells"] == 2_650,
        report["replay"]["analysis_contrasts"] == 18,
        report["holdout"]["analysis_contrasts"] == 18,
        registered_decisions["rq1_core_completion"][
            "passes_holm_and_practical_margin"
        ],
        registered_decisions["rq1_non_home_completion"][
            "passes_holm_and_practical_margin"
        ],
        not registered_decisions["rq1_rmst"]["passes_registered_direction"],
        registered_decisions["rq1_partition_6h"]["mean_difference"] < 0.0,
        registered_decisions["rq1_witness_0_6_boundary"]["mean_difference"] < 0.0,
        registered_decisions["rq2_tight_cap_completion"]["ci_low"] <= 0.0,
        registered_decisions["rq2_tight_cap_completion"]["ci_high"] >= 0.0,
        registered_decisions["rq2_frontier_hypervolume"]["all_families_pass"],
    ]
    report["all_required_integrity_and_reporting_checks_passed"] = all(required)

    output = RESULTS / "POST_RUN_EVIDENCE.json"
    output.write_bytes(canonical_json(report) + b"\n")
    print(json.dumps(report, indent=2))
    if not report["all_required_integrity_and_reporting_checks_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
