"""Operational metrics for the held-out temporal-contact request layer."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd

from aidmesh.experiments.temporal_frontier import (
    HOUR_US,
    _calibration_roles,
    generate_heldout_requests,
    load_contacts_csv,
)
from aidmesh.simulator.dtn import RoutingPolicy, TemporalContactSimulator

PROFILES: dict[str, tuple[int, int]] = {
    "contended": (16 * 1024, 2),
    "provisioned": (96 * 1024, 8),
}


def run_operational_metrics(
    trace_root: Path,
    *,
    contexts: tuple[str, ...] = ("invs13", "lh10", "sfhh"),
    seeds: tuple[int, ...] = tuple(range(7301, 7309)),
    calibration_fraction: float = 0.40,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for context in contexts:
        contacts_path = trace_root / context / "contacts.csv"
        contacts = load_contacts_csv(contacts_path)
        start_us = min(contact.start_us for contact in contacts)
        end_us = max(contact.end_us for contact in contacts)
        cutoff_us = start_us + round((end_us - start_us) * calibration_fraction)
        roles = _calibration_roles(contacts, cutoff_us)
        nodes = {node for contact in contacts for node in (contact.node_a, contact.node_b)}
        for seed in seeds:
            requests = generate_heldout_requests(
                contacts,
                roles,
                context=context,
                seed=seed,
                cutoff_us=cutoff_us,
            )
            bundles = tuple(request.bundle for request in requests)
            ttl_hours = bundles[0].ttl_us / HOUR_US if bundles else 0.0
            for profile, (buffer_bytes, copy_budget) in PROFILES.items():
                result = TemporalContactSimulator(
                    contacts=contacts,
                    bundles=bundles,
                    policy=RoutingPolicy.SPRAY_WAIT,
                    buffer_bytes=buffer_bytes,
                    copy_budget=copy_budget,
                ).run()
                delivered_delays = [delivery.delay_us / HOUR_US for delivery in result.deliveries]
                failure_count = result.bundles - result.delivered
                rmst_hours = (
                    (sum(delivered_delays) + failure_count * ttl_hours) / result.bundles
                    if result.bundles
                    else 0.0
                )
                hops = [delivery.hops for delivery in result.deliveries]
                rows.append(
                    {
                        "context": context,
                        "seed": seed,
                        "profile": profile,
                        "buffer_bytes_per_node": buffer_bytes,
                        "copy_budget": copy_budget,
                        "network_nodes": len(nodes),
                        "configured_storage_bytes": buffer_bytes * len(nodes),
                        "generated_requests": result.bundles,
                        "delivered_requests": result.delivered,
                        "failed_by_deadline": failure_count,
                        "delivery_ratio": result.delivery_ratio,
                        "mean_delivered_delay_h": result.mean_delay_hours,
                        "p95_delivered_delay_h": result.p95_delay_hours,
                        "failure_inclusive_rmst_h": rmst_hours,
                        "forwarded_bytes": result.forwarded_bytes,
                        "forwarded_bytes_per_request": (
                            result.forwarded_bytes / result.bundles if result.bundles else 0.0
                        ),
                        "queue_copy_drops": result.dropped,
                        "final_duplicate_copies": result.duplicate_copies,
                        "mean_delivered_hops": sum(hops) / len(hops) if hops else None,
                        "event_checksum": result.event_checksum,
                    }
                )
    return pd.DataFrame(rows)


def summarize_operational_metrics(frame: pd.DataFrame) -> pd.DataFrame:
    return (
        frame.groupby(["context", "profile"], as_index=False)
        .agg(
            seeds=("seed", "nunique"),
            generated_requests=("generated_requests", "sum"),
            delivery_ratio=("delivery_ratio", "mean"),
            failure_inclusive_rmst_h=("failure_inclusive_rmst_h", "mean"),
            forwarded_bytes_per_request=("forwarded_bytes_per_request", "mean"),
            queue_copy_drops=("queue_copy_drops", "mean"),
            final_duplicate_copies=("final_duplicate_copies", "mean"),
            configured_storage_bytes=("configured_storage_bytes", "first"),
            copy_budget=("copy_budget", "first"),
        )
        .sort_values(["context", "profile"])
    )


def write_operational_metrics(trace_root: Path, output: Path) -> dict[str, Path]:
    output.mkdir(parents=True, exist_ok=True)
    runs = run_operational_metrics(trace_root)
    summary = summarize_operational_metrics(runs)
    runs_path = output / "temporal_operational_runs.csv"
    summary_path = output / "temporal_operational_summary.csv"
    manifest_path = output / "temporal_operational_manifest.json"
    runs.to_csv(runs_path, index=False, lineterminator="\n")
    summary.to_csv(summary_path, index=False, lineterminator="\n")
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "semantics": "held-out requests; finite-buffer binary Spray-and-Wait routing",
                "failure_latency": "undelivered request is censored at its 24-hour TTL",
                "storage": "configured aggregate per-node buffer budget, not peak occupancy",
                "profiles": {
                    name: {"buffer_bytes_per_node": values[0], "copy_budget": values[1]}
                    for name, values in PROFILES.items()
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    files = [runs_path, summary_path, manifest_path]
    checksum_path = output / "CHECKSUMS.sha256"
    checksum_path.write_text(
        "".join(
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
            for path in files
        ),
        encoding="ascii",
    )
    return {
        "runs": runs_path,
        "summary": summary_path,
        "manifest": manifest_path,
        "checksums": checksum_path,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            {
                key: str(path)
                for key, path in write_operational_metrics(
                    args.trace_root, args.output
                ).items()
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
