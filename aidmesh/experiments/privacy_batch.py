"""Run the concrete public-receipt batch mechanism over immutable event logs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from aidmesh.analysis.privacy import MetadataObservation, PrivacyReport, privacy_report
from aidmesh.common.canonical import canonical_json, stable_hash
from aidmesh.privacy.batching import BatchPolicy, ReceiptIntent, batch_receipts, generate_audit_key


@dataclass(frozen=True)
class PrivacyBatchArtifacts:
    public_views: list[dict[str, Any]]
    audit_records: list[dict[str, Any]]
    manifests: pd.DataFrame
    delays: pd.DataFrame
    metrics: pd.DataFrame
    pareto: pd.DataFrame
    audit_key_fingerprint: str


def _run_metadata(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": record["run_id"],
        "scenario_id": record["scenario_id"],
        "method": record["method"],
        "seed": record["seed"],
    }


def _receipt_intents(event_file: Path) -> tuple[dict[str, Any], list[ReceiptIntent]]:
    requests: dict[str, dict[str, Any]] = {}
    run_meta: dict[str, Any] = {}
    intents: list[ReceiptIntent] = []
    with event_file.open("r", encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            run_meta = _run_metadata(record)
            nullifier = record.get("nullifier_hash")
            if record.get("event_type") == "redemption_requested" and nullifier:
                requests[str(nullifier)] = {
                    "risk_cell": str(record.get("risk_cell") or "unknown"),
                    "category": str(record.get("category") or "unknown"),
                    "deadline_us": int(record.get("deadline_us") or record["sim_time_us"]),
                }
                continue
            if record.get("event_type") != "inventory_released" or not nullifier:
                continue
            receipt_id = record.get("receipt_id")
            if not receipt_id:
                raise ValueError(f"inventory release without receipt_id: {event_file}")
            request = requests.get(str(nullifier), {})
            path = str(record.get("path") or "unknown")
            copies = int(record.get("copies") or 1)
            transmitted_bytes = int(record.get("payload_bytes") or 1)
            if transmitted_bytes % copies:
                raise ValueError(
                    "inventory release payload_bytes must be divisible by copies "
                    "for fixed-shape batching"
                )
            fallback_tag = None
            if path == "fallback":
                fallback_tag = stable_hash(
                    {"nullifier": str(nullifier)},
                    domain="aidmesh/privacy/private-fallback-tag/v1",
                )
            intents.append(
                ReceiptIntent(
                    receipt_id=str(receipt_id),
                    released_us=int(record["sim_time_us"]),
                    deadline_us=int(record.get("deadline_us") or request["deadline_us"]),
                    payload_bytes=transmitted_bytes // copies,
                    copies=copies,
                    epoch=1,
                    risk_cell=str(request.get("risk_cell", "unknown")),
                    category=str(request.get("category", "unknown")),
                    path=path,
                    fallback_tag=fallback_tag,
                )
            )
    return run_meta, intents


def _ledger_observations(result) -> list[MetadataObservation]:
    return [
        MetadataObservation(
            epoch=view.epoch,
            time_bucket=view.slot_start_us,
            risk_cell=view.risk_cell,
            category=view.category,
            path=view.path,
            fallback_tag=view.fallback_tag,
            size_class=str(view.padded_size),
            vantage="ledger",
            is_dummy=view.is_dummy,
        )
        for view in result.public_views
    ]


def _gateway_observations(
    intents: list[ReceiptIntent], policy: BatchPolicy
) -> list[MetadataObservation]:
    return [
        MetadataObservation(
            epoch=intent.epoch,
            time_bucket=intent.released_us // policy.gateway_bucket_us,
            risk_cell="hidden",
            category="hidden",
            path=intent.path,
            fallback_tag=None,
            size_class=str(intent.payload_bytes),
            vantage="gateway",
        )
        for intent in intents
    ]


def _site_observations(
    intents: list[ReceiptIntent], policy: BatchPolicy
) -> list[MetadataObservation]:
    return [
        MetadataObservation(
            epoch=intent.epoch,
            time_bucket=intent.released_us // policy.gateway_bucket_us,
            risk_cell=intent.risk_cell,
            category=intent.category,
            path=intent.path,
            fallback_tag=intent.fallback_tag,
            size_class=str(intent.payload_bytes),
            vantage="site",
        )
        for intent in intents
    ]


def _metric_row(meta: dict[str, Any], vantage: str, report: PrivacyReport) -> dict[str, Any]:
    return {**meta, "vantage": vantage, **report.to_dict()}


def run_privacy_batch(
    input_root: Path,
    *,
    policy: BatchPolicy,
    audit_key: bytes | None = None,
) -> PrivacyBatchArtifacts:
    """Apply actual batching to each immutable run log and measure three vantages."""

    key = audit_key or generate_audit_key()
    public_rows: list[dict[str, Any]] = []
    audit_rows: list[dict[str, Any]] = []
    manifest_rows: list[dict[str, Any]] = []
    delay_rows: list[dict[str, Any]] = []
    metric_rows: list[dict[str, Any]] = []
    for event_file in sorted(input_root.rglob("events.jsonl")):
        meta, intents = _receipt_intents(event_file)
        if not intents:
            continue
        result = batch_receipts(intents, policy=policy, audit_key=key)
        for view in result.public_views:
            public_rows.append({**meta, **view.to_dict()})
        for record in result.audit_records:
            audit_rows.append({**meta, **record.to_dict()})
        for manifest in result.manifests:
            manifest_rows.append({**meta, **manifest.to_dict()})
        for delay in result.delays:
            delay_rows.append({**meta, **delay.to_dict()})
        vantages = {
            "ledger": _ledger_observations(result),
            "gateway": _gateway_observations(intents, policy),
            "site": _site_observations(intents, policy),
        }
        for name, observations in vantages.items():
            metric_rows.append(_metric_row(meta, name, privacy_report(observations)))

    manifests = pd.DataFrame(manifest_rows)
    delays = pd.DataFrame(delay_rows)
    metrics = pd.DataFrame(metric_rows)
    pareto = _pareto(metrics, delays, manifests)
    return PrivacyBatchArtifacts(
        public_views=public_rows,
        audit_records=audit_rows,
        manifests=manifests,
        delays=delays,
        metrics=metrics,
        pareto=pareto,
        audit_key_fingerprint=stable_hash(key, domain="aidmesh/privacy/audit-key-fingerprint/v1"),
    )


def _pareto(
    metrics: pd.DataFrame,
    delays: pd.DataFrame,
    manifests: pd.DataFrame,
) -> pd.DataFrame:
    if metrics.empty:
        return pd.DataFrame()
    delay_summary = (
        delays.groupby(["run_id", "method", "seed"], sort=True)
        .agg(
            median_added_delay_us=("added_delay_us", "median"),
            deadline_miss_fraction=("deadline_missed_after_batch", "mean"),
            transport_overhead_bytes_mean=("transport_overhead_bytes", "mean"),
        )
        .reset_index()
        if not delays.empty
        else pd.DataFrame(columns=["run_id", "method", "seed"])
    )
    batch_summary = (
        manifests.groupby(["run_id", "method", "seed"], sort=True)
        .agg(dummy_receipts=("dummy_receipts", "sum"), public_receipts=("public_receipts", "sum"))
        .reset_index()
        if not manifests.empty
        else pd.DataFrame(columns=["run_id", "method", "seed"])
    )
    merged = metrics.merge(delay_summary, on=["run_id", "method", "seed"], how="left")
    merged = merged.merge(batch_summary, on=["run_id", "method", "seed"], how="left")
    merged["dummy_overhead_fraction"] = merged["dummy_receipts"] / merged["public_receipts"]
    return merged


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(canonical_json(row))
            handle.write("\n")


def write_privacy_batch_artifacts(
    artifacts: PrivacyBatchArtifacts,
    output: Path,
) -> dict[str, Path]:
    """Persist exactly the public/audit separation required for replay and review."""

    output.mkdir(parents=True, exist_ok=True)
    public_path = output / "public_views.jsonl"
    audit_path = output / "audit_receipts.jsonl"
    manifest_path = output / "batch_manifest.csv"
    delay_path = output / "delay_costs.csv"
    metrics_path = output / "privacy_metrics.csv"
    pareto_path = output / "anonymity_pareto.csv"
    run_manifest_path = output / "privacy_batch_run.json"
    _write_jsonl(public_path, artifacts.public_views)
    _write_jsonl(audit_path, artifacts.audit_records)
    artifacts.manifests.to_csv(manifest_path, index=False)
    artifacts.delays.to_csv(delay_path, index=False)
    artifacts.metrics.to_csv(metrics_path, index=False)
    artifacts.pareto.to_csv(pareto_path, index=False)
    run_manifest_path.write_text(
        json.dumps(
            {
                "audit_key_fingerprint": artifacts.audit_key_fingerprint,
                "audit_key_persisted": False,
                "warning": "Recoverable audit decryption requires an authority-managed AES key.",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "public_views": public_path,
        "audit_receipts": audit_path,
        "batch_manifest": manifest_path,
        "delay_costs": delay_path,
        "privacy_metrics": metrics_path,
        "anonymity_pareto": pareto_path,
        "run_manifest": run_manifest_path,
    }
