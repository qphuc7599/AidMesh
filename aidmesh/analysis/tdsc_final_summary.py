"""Generate the compact, family-balanced statistics quoted by the TDSC paper."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

from aidmesh.analysis.hierarchical_bow import hierarchical_policy_effects
from aidmesh.experiments.bow_factorial import FACTOR_LEVELS


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _adatransfer_contrasts(
    frame: pd.DataFrame, references: tuple[str, ...], repetitions: int
) -> pd.DataFrame:
    rows = []
    for reference in references:
        effects = hierarchical_policy_effects(
            frame, reference=reference, repetitions=repetitions
        )
        row = effects.loc[effects["policy"].eq("adatransfer")]
        if not row.empty:
            rows.append(row)
    return pd.concat(rows, ignore_index=True)


def _activity(frame: pd.DataFrame) -> dict[str, float | int]:
    ada = frame.loc[frame["policy"].eq("adatransfer")].copy()
    useful = ada["completed"] - ada["duplicate_exposure"]
    result: dict[str, float | int] = {
        "rows": int(len(ada)),
        "transfers_started": int(ada["transfers_started"].sum()),
        "transfers_delivered": int(ada["transfers_delivered"].sum()),
        "transfers_stranded": int(ada["transfers_stranded"].sum()),
        "active_rows": int(ada["transfers_started"].gt(0).sum()),
        "positive_margin_decisions": int(
            ada["transfer_positive_margin_decisions"].sum()
        ),
        "duplicate_exposure": int(ada["duplicate_exposure"].sum()),
        "cap_violations": int(ada["risk_cap_violation"].sum()),
    }
    result["transfer_delivery_rate"] = result["transfers_delivered"] / max(
        1, result["transfers_started"]
    )
    if (
        "oracle_total_completed" in ada
        and ada["oracle_total_completed"].notna().any()
    ):
        oracle_rows = ada["oracle_total_completed"].notna()
        oracle = ada.loc[oracle_rows, "oracle_total_completed"].clip(lower=1)
        ratio = useful.loc[oracle_rows] / oracle
        result.update(
            {
                "mean_useful_to_oracle_ratio": float(ratio.mean()),
                "p05_useful_to_oracle_ratio": float(ratio.quantile(0.05)),
                "minimum_oracle_regret": float(
                    (ada.loc[oracle_rows, "oracle_total_completed"] - useful.loc[oracle_rows]).min()
                ),
            }
        )
    return result


def _factor_levels(frame: pd.DataFrame) -> list[dict[str, object]]:
    keys = ["family", "registered_context", "seed", "cell"]
    baseline = frame.loc[
        frame["policy"].eq("optimized_static"), keys + ["completion_rate"]
    ].rename(columns={"completion_rate": "reference_rate"})
    paired = frame.loc[frame["policy"].eq("adatransfer")].merge(
        baseline, on=keys, validate="one_to_one"
    )
    paired["delta"] = paired["completion_rate"] - paired["reference_rate"]
    rows: list[dict[str, object]] = []
    for factor in FACTOR_LEVELS:
        for level, block in paired.groupby(factor, observed=True, sort=True):
            rows.append(
                {
                    "factor": factor,
                    "level": str(level),
                    "rows": int(len(block)),
                    "mean_delta": float(block["delta"].mean()),
                    "positive_cells": int(block["delta"].gt(0).sum()),
                    "transfers_started": int(block["transfers_started"].sum()),
                    "transfers_delivered": int(block["transfers_delivered"].sum()),
                    "transfers_stranded": int(block["transfers_stranded"].sum()),
                }
            )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--primary", type=Path, required=True)
    parser.add_argument("--factorial", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bootstrap-repetitions", type=int, default=2000)
    args = parser.parse_args()
    primary = pd.read_csv(args.primary)
    factorial = pd.read_csv(args.factorial)
    args.output.mkdir(parents=True, exist_ok=True)
    primary_contrasts = _adatransfer_contrasts(
        primary,
        (
            "optimized_static",
            "equal_static",
            "robust_static",
            "periodic_sync",
            "quorum_only",
            "rolling_relocation",
            "myopic_transfer",
        ),
        args.bootstrap_repetitions,
    )
    factorial_contrasts = _adatransfer_contrasts(
        factorial,
        (
            "optimized_static",
            "robust_static",
            "periodic_sync",
            "rolling_relocation",
            "myopic_transfer",
        ),
        args.bootstrap_repetitions,
    )
    primary_path = args.output / "tdsc_primary_adatransfer_contrasts.csv"
    factorial_path = args.output / "tdsc_factorial_adatransfer_contrasts.csv"
    primary_contrasts.to_csv(primary_path, index=False, lineterminator="\n")
    factorial_contrasts.to_csv(factorial_path, index=False, lineterminator="\n")
    summary_path = args.output / "tdsc_final_summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "bootstrap_repetitions": args.bootstrap_repetitions,
                "primary": {
                    "rows": int(len(primary)),
                    "families": int(primary["family"].nunique()),
                    "contexts": int(primary["registered_context"].nunique()),
                    "context_seed_clusters": int(
                        primary[["registered_context", "seed"]]
                        .drop_duplicates()
                        .shape[0]
                    ),
                    "adatransfer": _activity(primary),
                },
                "factorial": {
                    "rows": int(len(factorial)),
                    "families": int(factorial["family"].nunique()),
                    "contexts": int(factorial["registered_context"].nunique()),
                    "context_seed_clusters": int(
                        factorial[["registered_context", "seed"]]
                        .drop_duplicates()
                        .shape[0]
                    ),
                    "adatransfer": _activity(factorial),
                    "factor_levels": _factor_levels(factorial),
                },
                "inputs": {
                    "primary": str(args.primary),
                    "primary_sha256": _sha256(args.primary),
                    "factorial": str(args.factorial),
                    "factorial_sha256": _sha256(args.factorial),
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    checksums = args.output / "CHECKSUMS.sha256"
    checksums.write_text(
        "".join(
            f"{_sha256(path)}  {path.name}\n"
            for path in (primary_path, factorial_path, summary_path)
        ),
        encoding="ascii",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
