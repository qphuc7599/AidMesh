"""Fail-closed verification of the claim-bearing post-review release files."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(relative: str):
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


atomic_root = "results/geth_eip7825_atomic100_attackmatrix_final4_20260910"
atomic = load(f"{atomic_root}/summary.json")
state = load(f"{atomic_root}/atomic_state_audit.json")
storage_audit = load(f"{atomic_root}/staging_storage_audit.json")
attacks = load(f"{atomic_root}/attack_matrix.json")
h5 = load(f"{atomic_root}/aggregate_pipeline_benchmark.json")
protocol = load("results/matched_protocol_baselines_20260910/summary.json")
controller = load("results/matched_receipt_controller_20260910/summary.json")
allocation = load("results/strong_allocation_20260910_v6/paired_analysis.json")
allocation_summary = load("results/strong_allocation_20260910_v6/summary.json")
equal = load("results/strong_allocation_equal_share_20260910/summary.json")
privacy = load("results/staged_privacy_20260909_v2/privacy_composition_summary.json")
formal = load("results/postreview_formal_20260910/portfolio.json")
settlement_ablation = load("results/no_ledger_reconciliation_ablation_20260910_v1/comparison.json")
archive_metadata = load("reproducibility/archive_metadata.json")
original_registry_root = "results/postreview_original_registry_20260910"
pre_run_freeze = load(f"{original_registry_root}/PRE_RUN_FREEZE.json")
replay_coverage = load(f"{original_registry_root}/replay_coverage.json")
holdout_coverage = load(f"{original_registry_root}/holdout_coverage.json")
replay_analysis = load(
    f"{original_registry_root}/replay_analysis/confirmatory_analysis_manifest.json"
)
holdout_analysis = load(
    f"{original_registry_root}/holdout_analysis/confirmatory_analysis_manifest.json"
)
post_run_evidence = load(f"{original_registry_root}/POST_RUN_EVIDENCE.json")

checks = {
    "atomicBatchCommitted": atomic["batchStatus"] == 1,
    "atomicVoucherCount": atomic["uniqueVouchers"] == atomic["actuatorInvocations"] == 100,
    "atomicCredits": atomic["credits"] == [100, 50],
    "atomicUnderEip7825": atomic["maxTransactionGas"] < atomic["eip7825Cap"] == 2**24,
    "atomicReplayAndIncompleteRejected": (
        atomic["replayStatus"] == 0 and atomic["incompleteFinalizeStatus"] == 0
    ),
    "atomicStateAudit": all(state["checks"].values()),
    "atomicStagingStorageAudit": (
        storage_audit["allChecksPassed"]
        and all(storage_audit["checks"].values())
        and storage_audit["logicalStagingStorage"]["totalWords"] == 306
        and storage_audit["logicalStagingStorage"]["logicalBytes"] == 9_792
    ),
    "attackMatrix": attacks["allPassed"] and attacks["negativeControls"] == 43,
    "h5JointCriterion": h5["jointCriterion"]["allPassed"],
    "matchedProtocolShape": (
        protocol["runs"] == 1080
        and protocol["scenarios"] == 9
        and protocol["pairedSeedsPerScenario"] == 30
    ),
    "matchedProtocolSafety": protocol["allSafetyRulesPassed"],
    "matchedController": (
        controller["runs"] == 1080
        and controller["matchedWorkloads"] == 360
        and controller["allPrimaryCellsFavorReceiptPriority"]
        and all(controller["matchingChecks"].values())
    ),
    "allocationExactSuite": (
        allocation_summary["instances"] == 270
        and allocation_summary["rows"] == 1620
        and len(allocation["strongAllocation"]) >= 3
    ),
    "equalShareRegeneration": (
        equal["instances"] == 270
        and equal["problemRegenerationChecks"] == 810
        and equal["allProblemRegenerationChecksPassed"]
        and equal["fullBoxLeximinMinusEqualShare"]["minimum"]["winsTiesLosses"][2] == 0
    ),
    "privacyNegativeControls": (
        privacy["negativeControls"]["privateWitnessMutationsRejected"] == 150
    ),
    "formalPortfolio": formal["allObligationsCovered"],
    "formalLemmaCount": (
        formal["evidenceCounts"]["valueTamarinSafeLemmas"]
        + formal["evidenceCounts"]["recoveryTamarinSafeLemmas"]
        + formal["evidenceCounts"]["compositionTamarinSafeLemmas"]
        == 14
    ),
    "matchedSettlementAblation": (
        settlement_ablation["allChecksPassed"]
        and settlement_ablation["records"] == 100
        and settlement_ablation["sameInputSha256"]
        and settlement_ablation["sameCredits"]
        and settlement_ablation["sameGethExecutionReceipts"]
        and settlement_ablation["sqliteAuthority"]["trials"] == 30
        and settlement_ablation["sqliteAuthority"]["atomicBatch"]
        and settlement_ablation["sqliteAuthority"]["replayRejected"]
        and settlement_ablation["sqliteAuthority"]["corruptBatchRolledBack"]
        and settlement_ablation["sqliteAuthority"]["gas"] == 0
        and settlement_ablation["geth"]["freshExecutions"] == 1
        and settlement_ablation["geth"]["totalGas"] == 85_734_940
        and settlement_ablation["geth"]["finalizerGas"] == 14_203_542
        and settlement_ablation["geth"]["submittedCalldataBytes"] == 253_900
    ),
    "archiveMetadataPreparedWithoutInventedAccession": (
        archive_metadata["version"] == "1.0.0"
        and archive_metadata["artifact_filename"] == "AidMesh_Q1_PostReview_Artifact_20260910.zip"
        and archive_metadata["doi"] is None
        and archive_metadata["stable_url"] is None
    ),
    "originalRegistryReplayCoverage": (
        replay_coverage["expected_cells"] == 2_650
        and replay_coverage["completed_cells"] == 2_650
        and replay_coverage["failed_cells"] == 0
        and replay_coverage["missing_cells"] == 0
        and replay_coverage["invalid_cells"] == 0
        and replay_coverage["extra_cells"] == 0
        and replay_coverage["safe_method_violations"] == 0
        and replay_coverage["semantic_replay_mismatches"] == 2_500
        and post_run_evidence["replay"]["data_integrity_and_safety_passed"]
        and not post_run_evidence["replay"]["historical_semantic_equivalence"]
        and post_run_evidence["replay"]["historical_semantic_mismatches"] == 2_500
        and replay_analysis["input_summary_count"] == 2_650
        and replay_analysis["registry_sha256"]
        == pre_run_freeze["replay_registry_sha256"]
    ),
    "freshOriginalRegistryHoldoutCoverage": (
        holdout_coverage["all_checks_passed"]
        and holdout_coverage["expected_cells"] == 2_650
        and holdout_coverage["completed_cells"] == 2_650
        and holdout_coverage["failed_cells"] == 0
        and holdout_coverage["safe_method_violations"] == 0
        and pre_run_freeze["holdout_seed_overlap_with_original"] == 0
        and holdout_analysis["input_summary_count"] == 2_650
        and holdout_analysis["registry_sha256"]
        == pre_run_freeze["holdout_registry_sha256"]
    ),
    "originalRegistryRegisteredDecisionsRetained": (
        post_run_evidence["all_required_integrity_and_reporting_checks_passed"]
        and post_run_evidence["seed_overlap"] == 0
        and post_run_evidence["holdout"]["analysis_contrasts"] == 18
        and post_run_evidence["registered_decisions"]["rq1_core_completion"][
            "passes_holm_and_practical_margin"
        ]
        and not post_run_evidence["registered_decisions"]["rq1_rmst"][
            "passes_registered_direction"
        ]
        and post_run_evidence["registered_decisions"]["rq1_partition_6h"][
            "mean_difference"
        ]
        < 0.0
        and post_run_evidence["registered_decisions"]["rq1_witness_0_6_boundary"][
            "mean_difference"
        ]
        < 0.0
        and post_run_evidence["registered_decisions"]["rq2_tight_cap_completion"][
            "ci_low"
        ]
        <= 0.0
        and post_run_evidence["registered_decisions"]["rq2_tight_cap_completion"][
            "ci_high"
        ]
        >= 0.0
        and post_run_evidence["registered_decisions"]["rq2_frontier_hypervolume"][
            "all_families_pass"
        ]
    ),
}

paper = (ROOT / "source/paper.tex").read_text(encoding="utf-8")
paper_flat = " ".join(paper.split())
bib = (ROOT / "source/references.bib").read_text(encoding="utf-8")
overleaf_paper = (ROOT / "overleaf/paper.tex").read_text(encoding="utf-8")
overleaf_files = sorted(
    path.relative_to(ROOT / "overleaf").as_posix()
    for path in (ROOT / "overleaf").rglob("*")
    if path.is_file()
)
figures = re.findall(r"\\includegraphics(?:\[[^]]*\])?\{([^}]+)\}", paper)
citation_keys = {
    key.strip() for group in re.findall(r"\\cite\w*\{([^}]+)\}", paper) for key in group.split(",")
}
bib_keys = set(re.findall(r"(?m)^@\w+\{\s*([^,\s]+)", bib))
abstract_match = re.search(r"\\begin\{abstract\}(.*?)\\end\{abstract\}", paper, re.S)
abstract_text = re.sub(
    r"\\[A-Za-z]+(?:\[[^\]]*\])?(?:\{[^{}]*\})?",
    " ",
    abstract_match.group(1) if abstract_match else "",
)
abstract_text = re.sub(r"[{}~_^]", " ", abstract_text)
abstract_words = re.findall(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)*", abstract_text)
checks.update(
    {
        "manuscriptFigurePaths": figures == ["figs/postreview_core_results.pdf"],
        "manuscriptNoWorkspacePaths": not re.search(r"(?:[A-Z]:\\|\.\./|results/)", paper),
        "manuscriptCitationIntegrity": len(citation_keys) == 29 and citation_keys <= bib_keys,
        "manuscriptAbstractWordLimit": len(abstract_words) == 250,
        "manuscriptOriginalRegistryEvidence": all(
            phrase in paper_flat
            for phrase in (
                "All 2,650 replay and 2,650 holdout cells complete",
                "Core and non-home holdout gains are 0.2131 and 0.1075",
                "all three quorum families",
            )
        ),
        "manuscriptRegisteredNegativeAndNullResults": all(
            phrase in paper_flat
            for phrase in (
                "witness-0.6 reversals",
                "AidMesh minus Online-Only is +13.24 h",
                "0.00005 [-0.00045, 0.00051]",
            )
        ),
        "manuscriptVersionDriftDisclosed": all(
            phrase in paper_flat
            for phrase in (
                "not presented as an exact reproduction",
                "changes 2,500 of 2,650 historical",
                "establishes version drift",
            )
        ),
        "originalRegistryReportPresent": (
            ROOT / "docs/ORIGINAL_REGISTRY_HOLDOUT_REPORT_20260910_VI.md"
        ).is_file(),
        "manuscriptScientificSectionsOnly": not any(
            heading in paper
            for heading in (
                "CRediT authorship contribution statement",
                "Data and code availability",
                "Acknowledgements",
                "Declaration of generative AI",
            )
        ),
        "manuscriptNumericBibliography": (
            r"\bibliographystyle{elsarticle-num}" in paper and r"\bibliography{references}" in paper
        ),
        "overleafMinimalAndSynchronized": (
            overleaf_files == ["figs/postreview_core_results.pdf", "paper.tex", "refs.bib"]
            and overleaf_paper
            == paper.replace(r"\bibliography{references}", r"\bibliography{refs}")
            and (ROOT / "overleaf/refs.bib").read_bytes()
            == (ROOT / "source/references.bib").read_bytes()
            and (ROOT / "overleaf/figs/postreview_core_results.pdf").read_bytes()
            == (ROOT / "source/figs/postreview_core_results.pdf").read_bytes()
        ),
    }
)

failed = [name for name, value in checks.items() if not value]
report = {"allPassed": not failed, "checks": checks, "failed": failed}
print(json.dumps(report, indent=2))
if failed:
    raise SystemExit(1)
