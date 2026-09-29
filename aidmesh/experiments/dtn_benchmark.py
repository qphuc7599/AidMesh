"""Experiment matrix for temporal-contact DTN routing policies."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from aidmesh.simulator.dtn import (
    HOUR_US,
    RoutingPolicy,
    TemporalContactSimulator,
    generate_receipt_bundles,
    generate_synthetic_contacts,
    load_bundles_csv,
    load_contacts_csv,
)


def run_dtn_matrix(
    *,
    seed_start: int,
    seeds: int,
    bundle_count: int = 240,
    sites: int = 8,
    relays: int = 3,
    horizon_hours: int = 48,
    partition_hours: int = 6,
    ttl_hours: int = 36,
    buffer_bytes: int = 96 * 1024,
) -> pd.DataFrame:
    policies = (
        RoutingPolicy.FIFO,
        RoutingPolicy.EPIDEMIC,
        RoutingPolicy.SPRAY_WAIT,
        RoutingPolicy.RISK_PRIORITY,
    )
    rows: list[dict] = []
    horizon_us = horizon_hours * HOUR_US
    for seed in range(seed_start, seed_start + seeds):
        contacts = generate_synthetic_contacts(
            seed=seed,
            sites=sites,
            relays=relays,
            horizon_us=horizon_us,
            partition_us=partition_hours * HOUR_US,
        )
        bundles = generate_receipt_bundles(
            seed=seed,
            count=bundle_count,
            sites=sites,
            horizon_us=horizon_us,
            ttl_us=ttl_hours * HOUR_US,
        )
        for policy in policies:
            result = TemporalContactSimulator(
                contacts=contacts,
                bundles=bundles,
                policy=policy,
                buffer_bytes=buffer_bytes,
                copy_budget=8 if policy == RoutingPolicy.EPIDEMIC else 4,
            ).run()
            rows.append(
                {
                    "seed": seed,
                    "policy": result.policy,
                    "bundles": result.bundles,
                    "delivered": result.delivered,
                    "delivery_ratio": result.delivery_ratio,
                    "risk_weighted_delivery_ratio": result.risk_weighted_delivery_ratio,
                    "mean_delivered_risk_score": result.mean_delivered_risk_score,
                    "mean_delay_hours": result.mean_delay_hours,
                    "p95_delay_hours": result.p95_delay_hours,
                    "expired": result.expired,
                    "dropped": result.dropped,
                    "forwarded_bytes": result.forwarded_bytes,
                    "duplicate_copies": result.duplicate_copies,
                    "event_checksum": result.event_checksum,
                }
            )
    return pd.DataFrame(rows)


def summarize_dtn(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []
    for policy, group in frame.groupby("policy", sort=True):
        rows.append(
            {
                "policy": policy,
                "seeds": int(group["seed"].nunique()),
                "delivery_ratio_mean": float(group["delivery_ratio"].mean()),
                "delivery_ratio_sd": float(group["delivery_ratio"].std(ddof=1)),
                "risk_weighted_delivery_ratio_mean": float(
                    group["risk_weighted_delivery_ratio"].mean()
                ),
                "mean_delivered_risk_score_mean": float(group["mean_delivered_risk_score"].mean()),
                "mean_delay_hours_mean": float(group["mean_delay_hours"].mean()),
                "p95_delay_hours_mean": float(group["p95_delay_hours"].mean()),
                "forwarded_bytes_mean": float(group["forwarded_bytes"].mean()),
                "dropped_mean": float(group["dropped"].mean()),
            }
        )
    return pd.DataFrame(rows)


def write_dtn_results(frame: pd.DataFrame, output: Path) -> dict[str, Path]:
    output.mkdir(parents=True, exist_ok=True)
    raw_path = output / "dtn_temporal_contact_runs.csv"
    summary_path = output / "dtn_temporal_contact_summary.csv"
    frame.to_csv(raw_path, index=False)
    summarize_dtn(frame).to_csv(summary_path, index=False)
    return {"raw": raw_path, "summary": summary_path}


def run_dtn_trace_matrix(
    *,
    contacts_path: Path,
    bundles_path: Path,
    buffer_bytes: int,
    copy_budget: int = 4,
) -> pd.DataFrame:
    contacts = load_contacts_csv(contacts_path)
    bundles = load_bundles_csv(bundles_path)
    rows: list[dict] = []
    for policy in (
        RoutingPolicy.FIFO,
        RoutingPolicy.EPIDEMIC,
        RoutingPolicy.SPRAY_WAIT,
        RoutingPolicy.RISK_PRIORITY,
    ):
        result = TemporalContactSimulator(
            contacts=contacts,
            bundles=bundles,
            policy=policy,
            buffer_bytes=buffer_bytes,
            copy_budget=8 if policy == RoutingPolicy.EPIDEMIC else copy_budget,
        ).run()
        rows.append(
            {
                "seed": 0,
                "policy": result.policy,
                "bundles": result.bundles,
                "delivered": result.delivered,
                "delivery_ratio": result.delivery_ratio,
                "risk_weighted_delivery_ratio": result.risk_weighted_delivery_ratio,
                "mean_delivered_risk_score": result.mean_delivered_risk_score,
                "mean_delay_hours": result.mean_delay_hours,
                "p95_delay_hours": result.p95_delay_hours,
                "expired": result.expired,
                "dropped": result.dropped,
                "forwarded_bytes": result.forwarded_bytes,
                "duplicate_copies": result.duplicate_copies,
                "event_checksum": result.event_checksum,
                "contacts_path": str(contacts_path),
                "bundles_path": str(bundles_path),
            }
        )
    return pd.DataFrame(rows)
