"""Compare deterministic scientific outputs from two full E2E campaign roots.

Host-dependent proving time is intentionally excluded. Safety/accounting invariants,
registered workload identity, outcome counts, and non-timing grouped estimands must
match exactly (floating-point columns use a 1e-12 absolute tolerance).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _manifest(root: Path) -> dict[str, Any]:
    path = root / "e2e_campaign_manifest.json"
    if not path.exists():
        raise FileNotFoundError(f"missing E2E manifest: {path}")
    value = _load_json(path)
    if not isinstance(value, dict):
        raise TypeError(f"manifest is not an object: {path}")
    return value


def _rows(root: Path) -> list[dict[str, str]]:
    path = root / "analysis" / "e2e_grouped_results.csv"
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _compare(label: str, left: Any, right: Any, findings: list[dict[str, Any]]) -> None:
    findings.append(
        {
            "check": label,
            "passed": left == right,
            "reference": left,
            "candidate": right,
        }
    )


def _compare_grouped(
    reference: list[dict[str, str]],
    candidate: list[dict[str, str]],
    findings: list[dict[str, Any]],
) -> None:
    keys = ("context", "method", "witness_regime")
    deterministic = (
        "runs",
        "completion_mean",
        "physical_release_mean",
        "duplicate_release_mean",
        "receipt_delivery_mean",
        "cap_violations",
    )
    ref_index = {tuple(row[key] for key in keys): row for row in reference}
    cand_index = {tuple(row[key] for key in keys): row for row in candidate}
    _compare("grouped_keys", sorted(ref_index), sorted(cand_index), findings)
    for key in sorted(set(ref_index) & set(cand_index)):
        for column in deterministic:
            left = ref_index[key][column]
            right = cand_index[key][column]
            try:
                passed = math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=1e-12)
            except ValueError:
                passed = left == right
            findings.append(
                {
                    "check": f"grouped:{'/'.join(key)}:{column}",
                    "passed": passed,
                    "reference": left,
                    "candidate": right,
                }
            )


def compare(reference_root: Path, candidate_root: Path) -> dict[str, Any]:
    reference = _manifest(reference_root)
    candidate = _manifest(candidate_root)
    findings: list[dict[str, Any]] = []

    for field in (
        "registry_hash",
        "runs",
        "all_cap_safe",
        "all_risk_conserved",
        "all_python_evm_credit_match",
        "all_python_evm_escrow_match",
    ):
        _compare(f"manifest:{field}", reference.get(field), candidate.get(field), findings)

    for field in ("registry_id", "profile", "domain_tags", "compiler_version", "evm_version"):
        _compare(
            f"registry:{field}",
            reference.get("registry", {}).get(field),
            candidate.get("registry", {}).get(field),
            findings,
        )

    for field in ("cases", "passed", "all_passed", "cases_by_name"):
        _compare(
            f"fault_controls:{field}",
            reference.get("fault_controls", {}).get(field),
            candidate.get("fault_controls", {}).get(field),
            findings,
        )

    reference_hashes = reference.get("implementation", {}).get("sha256", {})
    candidate_hashes = candidate.get("implementation", {}).get("sha256", {})
    labels = sorted((set(reference_hashes) | set(candidate_hashes)) - {"solc_binary"})
    for label in labels:
        _compare(
            f"implementation_sha256:{label}",
            reference_hashes.get(label),
            candidate_hashes.get(label),
            findings,
        )

    ref_invariants = _load_json(reference_root / "analysis" / "e2e_invariants.json")
    cand_invariants = _load_json(candidate_root / "analysis" / "e2e_invariants.json")
    for field in sorted(set(ref_invariants) | set(cand_invariants)):
        _compare(
            f"analysis_invariant:{field}",
            ref_invariants.get(field),
            cand_invariants.get(field),
            findings,
        )

    _compare_grouped(_rows(reference_root), _rows(candidate_root), findings)
    failed = [finding for finding in findings if not finding["passed"]]
    return {
        "schema_version": 1,
        "status": "pass" if not failed else "fail",
        "claim_boundary": (
            "Deterministic cross-host outcome equivalence; proving time and solc binary "
            "hash are host-dependent and excluded."
        ),
        # Preserve caller-supplied relative artifact locators.  Absolute paths
        # leak workstation user names and are not suitable for double-blind
        # evidence packages.
        "reference_root": reference_root.as_posix(),
        "candidate_root": candidate_root.as_posix(),
        "checks": len(findings),
        "failed_checks": len(failed),
        "findings": findings,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = compare(args.reference, args.candidate)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(args.output)
    if report["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
