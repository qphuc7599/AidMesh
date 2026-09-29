"""Machine-check the scientific integrity and activity of a BOW campaign."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

from aidmesh.experiments.bow_factorial import FACTOR_LEVELS


def audit_campaign(runs_path: Path, manifest_path: Path) -> dict[str, Any]:
    frame = pd.read_csv(runs_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    kind = str(manifest["campaign"])
    base_keys = ["family", "registered_context", "seed"]
    keys = (
        [*base_keys, "cap", "quorum_availability", "policy"]
        if kind == "primary"
        else [*base_keys, "cell", "policy"]
    )
    ada = frame.loc[frame["policy"] == "adatransfer"]
    factor_activity: dict[str, Any] = {}
    if kind == "factorial":
        for factor in FACTOR_LEVELS:
            factor_activity[factor] = {
                str(level): {
                    "rows": int(len(group)),
                    "transfers_started": int(group["transfers_started"].sum()),
                    "transfers_delivered": int(group["transfers_delivered"].sum()),
                    "transfers_stranded": int(group["transfers_stranded"].sum()),
                }
                for level, group in ada.groupby(factor, observed=True)
            }
    checks = {
        "row_count_matches_manifest": len(frame) == int(manifest["expected_rows"]),
        "unique_scientific_keys": not frame.duplicated(keys).any(),
        "three_independent_families": frame["family"].nunique() >= 3,
        "zero_risk_cap_violation": int(frame["risk_cap_violation"].sum()) == 0,
        "rights_conservation_always_holds": bool(
            (
                frame["rights_conservation_holds"].isna()
                | frame["rights_conservation_holds"].eq(True)
            ).all()
        ),
        "adatransfer_gate_is_active": int(ada["transfers_started"].sum()) > 0,
        "at_least_one_transfer_is_delivered": int(ada["transfers_delivered"].sum()) > 0,
    }
    if kind == "primary":
        oracle_rows = ada.loc[ada["oracle_total_completed"].notna()].copy()
        useful = oracle_rows["completed"] - oracle_rows["duplicate_exposure"]
        checks["oracle_dominates_useful_online_completion"] = bool(
            (oracle_rows["oracle_total_completed"] >= useful).all()
        )
        checks["online_to_oracle_ratio_is_bounded"] = bool(
            (useful / oracle_rows["oracle_total_completed"].clip(lower=1) <= 1.0).all()
        )
    if kind == "factorial":
        checks["all_seven_factors_balanced"] = all(
            frame[factor].nunique() == 2 for factor in FACTOR_LEVELS
        )
        checks["transfer_quality_factor_is_exercised"] = all(
            cell["transfers_started"] > 0
            for cell in factor_activity["transfer_reliability"].values()
        )
    started = int(ada["transfers_started"].sum())
    delivered = int(ada["transfers_delivered"].sum())
    audit = {
        "schema_version": 1,
        "kind": kind,
        "runs": str(runs_path),
        "manifest": str(manifest_path),
        "rows": int(len(frame)),
        "families": sorted(frame["family"].unique()),
        "contexts": sorted(frame["registered_context"].unique()),
        "seeds": int(frame[["registered_context", "seed"]].drop_duplicates().shape[0]),
        "adatransfer": {
            "rows": int(len(ada)),
            "decision_attempts": int(ada["transfer_decisions"].sum()),
            "candidate_decisions": int(ada["transfer_candidate_decisions"].sum()),
            "transfers_started": started,
            "transfers_delivered": delivered,
            "transfers_stranded": int(ada["transfers_stranded"].sum()),
            "delivery_rate": delivered / max(1, started),
            "active_rows": int((ada["transfers_started"] > 0).sum()),
        },
        "factor_activity": factor_activity,
        "checks": checks,
        "all_checks_pass": all(checks.values()),
    }
    return audit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    audit = audit_campaign(args.runs, args.manifest)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(audit, sort_keys=True))
    return 0 if audit["all_checks_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
