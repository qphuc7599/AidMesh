"""Post-confirmatory event-respecting oracle audit on Asturies incidents.

The audit is deliberately separated from the untouched hypothesis tests.  It
does not retune a policy: it reruns the frozen AdaTransfer primary design and
adds the integral, event-respecting offline upper comparator.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from aidmesh.experiments.bow_factorial import _requests_for_drift
from aidmesh.experiments.bow_temporal import (
    CausalCampaignConfig,
    calibration_path_reliability,
    evaluate_causal_policy,
)
from aidmesh.experiments.temporal_frontier import (
    _calibration_roles,
    generate_heldout_requests,
)
from aidmesh.simulator.dtn import load_contacts_csv


@dataclass(frozen=True)
class OracleAuditDesign:
    seed: int
    calibration_fraction: float
    cap_fractions: tuple[float, ...]
    quorum_availabilities: tuple[float, ...]
    oracle_slot_us: int


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_oracle_audit(
    registry_path: Path,
    output: Path,
    *,
    workspace: Path,
) -> dict[str, Path]:
    """Run the registered 23-incident event-respecting comparator audit."""

    registry = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    design = OracleAuditDesign(
        seed=int(registry["seed"]),
        calibration_fraction=float(registry["calibration_fraction"]),
        cap_fractions=tuple(float(x) for x in registry["cap_fractions"]),
        quorum_availabilities=tuple(float(x) for x in registry["quorum_availabilities"]),
        oracle_slot_us=int(registry["oracle_slot_us"]),
    )
    rows: list[dict[str, Any]] = []
    for ordinal, context_row in enumerate(registry["contexts"], start=1):
        trace_path = (workspace / context_row["path"]).resolve()
        contacts = load_contacts_csv(trace_path / "contacts.csv")
        start_us = min(row.start_us for row in contacts)
        end_us = max(row.end_us for row in contacts)
        cutoff_us = start_us + round(
            (end_us - start_us) * design.calibration_fraction
        )
        roles = _calibration_roles(contacts, cutoff_us)
        sites = roles["sites"]
        witness_pool = tuple(dict.fromkeys((*roles["relays"], *sites)))
        witnesses = witness_pool[: min(5, len(witness_pool))]
        threshold = min(3, len(witnesses))
        reliability = calibration_path_reliability(
            contacts, sites=sites, cutoff_us=cutoff_us
        )
        heldout = tuple(row for row in contacts if row.start_us >= cutoff_us)
        base_requests = generate_heldout_requests(
            contacts,
            roles,
            context=str(context_row["context"]),
            seed=design.seed,
            cutoff_us=cutoff_us,
        )
        for drift in ("stable", "shifting"):
            requests = _requests_for_drift(
                base_requests,
                sites=sites,
                context=str(context_row["context"]),
                seed=design.seed,
                level=drift,
            )
            for cap_fraction in design.cap_fractions:
                cap = max(len(sites), round(len(requests) * cap_fraction))
                for quorum_availability in design.quorum_availabilities:
                    config = CausalCampaignConfig(
                        cap=cap,
                        policy="adatransfer",
                        gateway=roles["relays"][0],
                        witnesses=witnesses,
                        quorum_threshold=threshold,
                        quorum_availability=quorum_availability,
                        transfer_witness_availability=quorum_availability,
                        adatransfer_gate_mode="expected_value",
                        oracle_slot_us=design.oracle_slot_us,
                        oracle_contact_timing="event_respecting",
                        experiment_id=(
                            f"catr-oracle:{context_row['context']}:{design.seed}:"
                            f"{drift}:{cap_fraction}:{quorum_availability}"
                        ),
                    )
                    result = evaluate_causal_policy(
                        contacts=heldout,
                        requests=requests,
                        sites=sites,
                        config=config,
                        path_reliability=reliability,
                        compute_oracle=True,
                    )
                    if result["online_to_oracle_ratio"] > 1.0 + 1e-12:
                        raise RuntimeError("online completion exceeded the upper comparator")
                    rows.append(
                        {
                            "family": str(context_row["family"]),
                            "context": str(context_row["context"]),
                            "seed": design.seed,
                            "demand_drift": drift,
                            "cap_fraction": cap_fraction,
                            "quorum_availability": quorum_availability,
                            "oracle_contact_timing": "event_respecting",
                            **result,
                        }
                    )
        print(
            json.dumps(
                {
                    "progress": f"{ordinal}/{len(registry['contexts'])}",
                    "context": context_row["context"],
                    "rows": len(rows),
                }
            ),
            flush=True,
        )

    frame = pd.DataFrame(rows)
    expected = (
        len(registry["contexts"])
        * 2
        * len(design.cap_fractions)
        * len(design.quorum_availabilities)
    )
    keys = [
        "context",
        "seed",
        "demand_drift",
        "cap_fraction",
        "quorum_availability",
    ]
    if len(frame) != expected or frame.duplicated(keys).any():
        raise RuntimeError("oracle-audit row integrity failure")
    if frame["oracle_total_completed"].isna().any():
        raise RuntimeError("missing oracle result")

    output.mkdir(parents=True, exist_ok=True)
    runs_path = output / "event_oracle_runs.csv"
    frame.to_csv(runs_path, index=False, lineterminator="\n")
    summary = frame.groupby("demand_drift", as_index=False).agg(
        incident_cells=("completed", "size"),
        online_completion_rate=("completion_rate", "mean"),
        mean_online_to_oracle_ratio=("online_to_oracle_ratio", "mean"),
        median_online_to_oracle_ratio=("online_to_oracle_ratio", "median"),
        min_online_to_oracle_ratio=("online_to_oracle_ratio", "min"),
        mean_oracle_gap=("oracle_gap", "mean"),
        max_oracle_gap=("oracle_gap", "max"),
        total_online_completed=("completed", "sum"),
        total_oracle_completed=("oracle_total_completed", "sum"),
        total_oracle_transfer_units=("oracle_transfer_units", "sum"),
    )
    summary_path = output / "event_oracle_summary.csv"
    summary.to_csv(summary_path, index=False, lineterminator="\n")
    manifest_path = output / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "design": "post-confirmatory event-respecting oracle audit",
                "registry": str(registry_path.resolve()),
                "registry_sha256": _sha256(registry_path),
                "rows": len(frame),
                "expected_rows": expected,
                "status": "complete",
                "interpretation": (
                    "integral upper comparator for the declared store-carry-forward "
                    "abstraction; not a radio-layer or adversarial oracle"
                ),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    checksums_path = output / "CHECKSUMS.sha256"
    checksums_path.write_text(
        "".join(
            f"{_sha256(path)}  {path.name}\n"
            for path in (runs_path, summary_path, manifest_path)
        ),
        encoding="utf-8",
    )
    return {
        "runs": runs_path,
        "summary": summary_path,
        "manifest": manifest_path,
        "checksums": checksums_path,
    }
