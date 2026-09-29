"""Reproduce the secondary mechanism decomposition for the Elsevier revision.

The registered direct estimand remains AdaTransfer minus robust static.  This
script additionally decomposes the older AdaTransfer-minus-optimized-static
contrast into robust-placement and transfer increments on the same complete
Asturies 2^8 matrix.  Incident clusters, rather than factorial rows, receive
equal weight in every estimate and bootstrap draw.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


PAIR_KEYS = [
    "registered_context",
    "seed",
    "cell",
    "demand_drift",
    "cap_tightness",
    "witness_availability",
    "transfer_reliability",
    "contact_predictability",
    "buffer_provisioning",
    "cross_class_traffic",
    "recovery_timing",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def paired_delta(frame: pd.DataFrame, candidate: str, reference: str) -> pd.DataFrame:
    columns = PAIR_KEYS + ["completion_rate"]
    left = frame.loc[frame["policy"].eq(candidate), columns].rename(
        columns={"completion_rate": "candidate_completion"}
    )
    right = frame.loc[frame["policy"].eq(reference), columns].rename(
        columns={"completion_rate": "reference_completion"}
    )
    paired = left.merge(right, on=PAIR_KEYS, validate="one_to_one")
    paired["completion_delta"] = (
        paired["candidate_completion"] - paired["reference_completion"]
    )
    return paired


def incident_cluster_interval(
    paired: pd.DataFrame, *, repetitions: int, seed: int
) -> tuple[float, float, float, int, int]:
    incident_means = (
        paired.groupby("registered_context", sort=True)["completion_delta"]
        .mean()
        .to_numpy(dtype=float)
    )
    if len(incident_means) != 23:
        raise ValueError(f"expected 23 incident clusters, found {len(incident_means)}")
    rng = np.random.default_rng(seed)
    draws = rng.choice(
        incident_means,
        size=(repetitions, len(incident_means)),
        replace=True,
    ).mean(axis=1)
    return (
        float(incident_means.mean()),
        float(np.quantile(draws, 0.025)),
        float(np.quantile(draws, 0.975)),
        len(incident_means),
        len(paired),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--oracle-summary", type=Path, required=True)
    parser.add_argument("--oracle-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bootstrap-repetitions", type=int, default=5000)
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=False)
    frame = pd.read_csv(args.input)
    required_policies = {"optimized_static", "robust_static", "adatransfer"}
    missing = required_policies - set(frame["policy"].unique())
    if missing:
        raise ValueError(f"missing required policies: {sorted(missing)}")

    comparisons = [
        ("AdaTransfer", "adatransfer", "optimized static", "optimized_static"),
        ("Robust static", "robust_static", "optimized static", "optimized_static"),
        ("AdaTransfer", "adatransfer", "robust static", "robust_static"),
    ]
    records: list[dict[str, object]] = []
    bootstrap_seeds = {
        ("adatransfer", "optimized_static", "stable"): 202608222,
        ("adatransfer", "optimized_static", "shifting"): 202608223,
        ("robust_static", "optimized_static", "stable"): 202608224,
        ("robust_static", "optimized_static", "shifting"): 202608225,
        ("adatransfer", "robust_static", "stable"): 202609011,
        ("adatransfer", "robust_static", "shifting"): 202609012,
    }
    for candidate_label, candidate, reference_label, reference in comparisons:
        paired = paired_delta(frame, candidate, reference)
        for drift in ("stable", "shifting"):
            subset = paired.loc[paired["demand_drift"].eq(drift)]
            estimate, low, high, clusters, cells = incident_cluster_interval(
                subset,
                repetitions=args.bootstrap_repetitions,
                seed=bootstrap_seeds[(candidate, reference, drift)],
            )
            records.append(
                {
                    "candidate": candidate_label,
                    "reference": reference_label,
                    "demand_drift": drift,
                    "incident_clusters": clusters,
                    "paired_cells": cells,
                    "mean_completion_delta": estimate,
                    "ci95_low": low,
                    "ci95_high": high,
                    "bootstrap_repetitions": args.bootstrap_repetitions,
                }
            )

    effects = pd.DataFrame(records)
    effects_path = args.output / "mechanism_decomposition.csv"
    effects.to_csv(effects_path, index=False)

    oracle = pd.read_csv(args.oracle_summary)
    oracle_records: list[dict[str, object]] = []
    for row in oracle.to_dict(orient="records"):
        total_online = int(row["total_online_completed"])
        total_oracle = int(row["total_oracle_completed"])
        oracle_records.append(
            {
                "demand_drift": row["demand_drift"],
                "event_cells": int(row["incident_cells"]),
                "aggregate_completed_count_ratio": total_online / total_oracle,
                "unweighted_mean_cell_ratio": float(row["mean_online_to_oracle_ratio"]),
                "median_cell_ratio": float(row["median_online_to_oracle_ratio"]),
                "total_online_completed": total_online,
                "total_relaxed_oracle_completed": total_oracle,
            }
        )
    oracle_table = pd.DataFrame(oracle_records)
    oracle_path = args.output / "relaxed_oracle_diagnostic.csv"
    oracle_table.to_csv(oracle_path, index=False)

    oracle_manifest = json.loads(args.oracle_manifest.read_text(encoding="utf-8"))
    manifest = {
        "schema_version": 1,
        "analysis": "elsevier_mechanism_decomposition_v2",
        "primary_claim_guard": (
            "AdaTransfer-minus-robust-static remains the primary direct estimand; "
            "optimized-static comparisons are secondary descriptive decomposition."
        ),
        "cluster_unit": "registered Asturies incident",
        "incident_clusters": 23,
        "input_rows": int(len(frame)),
        "bootstrap_repetitions": args.bootstrap_repetitions,
        "oracle_status": oracle_manifest.get("status"),
        "oracle_event_cells": oracle_manifest.get("rows"),
        "oracle_claim_guard": (
            "Event-respecting aggregate relaxed upper comparator for the declared "
            "store-carry-forward abstraction; not an exact radio-layer frontier and "
            "not an approximation guarantee."
        ),
        "inputs": {
            str(args.input): sha256(args.input),
            str(args.oracle_summary): sha256(args.oracle_summary),
            str(args.oracle_manifest): sha256(args.oracle_manifest),
        },
        "outputs": {
            effects_path.name: sha256(effects_path),
            oracle_path.name: sha256(oracle_path),
        },
    }
    manifest_path = args.output / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    checksums = args.output / "SHA256SUMS.txt"
    checksum_lines = [
        f"{sha256(path)}  {path.name}"
        for path in (effects_path, oracle_path, manifest_path)
    ]
    checksums.write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
