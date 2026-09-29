"""Audit scope, completeness, and provenance of baseline evidence.

The audit intentionally separates protocol-model comparators, component
reconstructions, and baselines that cannot be reproduced from the available
primary specification.  A component result never upgrades a full-protocol label.
"""

from __future__ import annotations

import csv
import hashlib
import json
from itertools import product
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results/baseline_validation_20260928"


def load(relative: str) -> dict:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def sha256(relative: str) -> str:
    return hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()


def protocol_model_checks() -> dict:
    registry_path = "configs/postreview_20260910/matched_protocol_baselines.json"
    output_root = "results/matched_protocol_baselines_20260910"
    registry = load(registry_path)
    manifest = load(f"{output_root}/manifest.json")
    summary = load(f"{output_root}/summary.json")
    with (ROOT / output_root / "runs.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    expected = {
        (Path(scenario).stem, seed, method)
        for scenario, seed, method in product(
            registry["scenarios"],
            range(registry["seedStart"], registry["seedStart"] + registry["seedCount"]),
            registry["methods"],
        )
    }
    # Scenario identifiers are stored inside the YAML rather than inferred from names.
    observed = {(row["scenario_id"], int(row["seed"]), row["method"]) for row in rows}
    expected_ids = {
        (
            next(
                line.split(":", 1)[1].strip()
                for line in (ROOT / scenario).read_text(encoding="utf-8").splitlines()
                if line.startswith("scenario_id:")
            ),
            seed,
            method,
        )
        for scenario, seed, method in product(
            registry["scenarios"],
            range(registry["seedStart"], registry["seedStart"] + registry["seedCount"]),
            registry["methods"],
        )
    }
    del expected
    output_hashes = {
        name: sha256(f"{output_root}/{name}") == digest
        for name, digest in manifest["outputs"].items()
    }
    provenance = {
        "registry": sha256(registry_path) == manifest["registrySha256"],
        "simulator": (
            sha256("aidmesh/simulator/experiment.py") == manifest["simulatorSha256"]
        ),
        "coconutComponent": (
            sha256(manifest["coconutComponentSummary"])
            == manifest["coconutComponentSummarySha256"]
        ),
        "outputs": all(output_hashes.values()),
    }
    checks = {
        "rows": len(rows) == 1080 == summary["runs"],
        "uniqueCells": len(observed) == len(rows),
        "completeCells": observed == expected_ids,
        "fourMethods": set(registry["methods"])
        == {
            "coconut_online",
            "first_seen_dtn",
            "aidmesh_quorum_only",
            "aidmesh_transferable_rights",
        },
        "safety": summary["allSafetyRulesPassed"] is True,
    }
    return {
        "classification": "matched end-to-end protocol-state model",
        "checks": checks,
        "provenance": provenance,
        "outputHashes": output_hashes,
        "passed": all(checks.values()) and all(provenance.values()),
        "boundary": summary["interpretation"],
    }


def routing_checks() -> dict:
    root = "results/matched_network_grid_20260906"
    complete = load(f"{root}/COMPLETE.json")
    protocol = load(f"{root}/protocol.json")
    paired = load(f"{root}/paired_analysis.json")
    source_checks = {
        relative.replace("\\", "/"): sha256(relative.replace("\\", "/")) == digest
        for relative, digest in protocol["source_sha256"].items()
    }
    with (ROOT / root / "runs.jsonl").open(encoding="utf-8") as handle:
        rows = sum(1 for _ in handle)
    checks = {
        "rows": rows == 5400 == complete["rows"],
        "rawHash": sha256(f"{root}/runs.jsonl") == complete["raw_sha256"],
        "matched": complete["matched"] is True,
        "pairedRuns": paired["paired_runs"] == 1350,
        "contrasts": paired["contrasts"] == 45,
        "sourceHashes": all(source_checks.values()),
    }
    return {
        "classification": "matched routing component",
        "checks": checks,
        "sourceHashes": source_checks,
        "passed": all(checks.values()),
        "finding": (
            "Risk-priority and equal-copy Binary Spray-and-Wait tie on both reported "
            "endpoints in every paired cell; this grid does not establish H4."
        ),
        "boundary": complete["scope"],
    }


def coconut_checks() -> dict:
    relative = "results/baseline_validation_20260928/coconut/summary.json"
    if not (ROOT / relative).exists():
        return {
            "classification": "cryptographic unit-token adapter",
            "passed": False,
            "checks": {"freshSummaryPresent": False},
            "boundary": "Fresh validation of the current adapter has not completed.",
        }
    summary = load(relative)
    checks = {
        "freshSummaryPresent": True,
        "adapterHash": sha256("aidmesh/protocol/coconut_online.py")
        == summary["adapter_sha256"],
        "uniqueTokens": summary["valid_unique_tokens"] == 100,
        "noDuplicateCredits": summary["duplicate_credits"] == 0,
        "negativeControls": summary["field_and_threshold_rejections"] == 36,
    }
    return {
        "classification": "cryptographic unit-token adapter",
        "checks": checks,
        "passed": all(checks.values()),
        "boundary": summary["scope"],
    }


def snappy_checks() -> dict:
    summary_path = OUT / "snappy/summary.json"
    if not summary_path.exists():
        return {
            "classification": "connected approval/accounting component",
            "passed": False,
            "checks": {"summaryPresent": False},
            "boundary": "Benchmark has not completed.",
        }
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    profiles = summary["profiles"]
    checks = {
        "summaryPresent": True,
        "threeCommitteeProfiles": [row["members"] for row in profiles] == [4, 7, 10],
        "majorityThresholds": [row["majority"] for row in profiles] == [3, 4, 6],
        "positivePayments": summary["coverage"]["positivePayments"] == 300,
        "fieldMutationRejections": summary["coverage"]["fieldMutationRejections"]
        == 1200,
        "connectedOnlyDeclared": summary["coverage"]["connectedOnly"] is True,
        "equalValueBoundaryRetained": bool(
            summary["accountingVectors"]["equalValueBoundary"]
        ),
    }
    return {
        "classification": "connected approval/accounting component",
        "checks": checks,
        "passed": all(checks.values()),
        "boundary": summary["boundary"],
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    eligibility = load("configs/development/original_baseline_validation.json")
    evidence = {
        "matchedProtocolModel": protocol_model_checks(),
        "matchedRouting": routing_checks(),
        "coconutOnline": coconut_checks(),
        "snappy": snappy_checks(),
    }
    blocked = {
        name: row["remaining"]
        for name, row in eligibility["baselines"].items()
        if row["eligible"] is not True
    }
    result = {
        "scope": "Baseline evidence and label-eligibility audit",
        "evidence": evidence,
        "allExecutedEvidencePassed": all(row["passed"] for row in evidence.values()),
        "eligibleMatchedProtocolLabels": sorted(
            name
            for name, row in eligibility["baselines"].items()
            if row["eligible"] is True
        ),
        "blockedOriginalProtocolLabels": blocked,
        "decision": (
            "CoconutOnline and FirstSeenDTN may be reported as matched protocol-model "
            "comparators. Snappy may be reported only as a connected component "
            "reconstruction. DPDRM and CSAR remain related-work comparators because "
            "their available primary specifications do not support faithful execution."
        ),
    }
    (OUT / "audit.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if result["allExecutedEvidencePassed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
