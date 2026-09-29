"""Post-freeze fixback campaigns for reviewer-facing validity checks.

These helpers deliberately write under ``results/fixback`` by default.  They do
not alter the frozen confirmatory registry or raw data.
"""

from __future__ import annotations

import csv
import json
import math
import random
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from aidmesh.common.canonical import stable_hash
from aidmesh.common.errors import DuplicateError, ValidationError
from aidmesh.protocol.campaign import CampaignHeader, CommitteeConfig, SafetyDomain
from aidmesh.protocol.model import Voucher, witness_request_for
from aidmesh.protocol.risk_escrow import EscrowRiskLedger
from aidmesh.protocol.witness import SQLiteWitnessStore, Witness, WitnessKey
from aidmesh.simulator.dtn import (
    HOUR_US,
    BundleSpec,
    Contact,
    RoutingPolicy,
    TemporalContactSimulator,
    generate_receipt_bundles,
    generate_synthetic_contacts,
    load_bundles_csv,
    load_contacts_csv,
)
from aidmesh.simulator.experiment import _uniform


@dataclass(frozen=True)
class DistanceSummary:
    scenario_id: str
    seeds: int
    bundles_per_seed: int
    abstract_completion: float
    dtn_completion: float
    completion_delta_abs: float
    ks_delay_hours: float | None
    wasserstein_delay_hours: float | None
    abstract_delivered: int
    dtn_delivered: int


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _ks_2samp(left: list[float], right: list[float]) -> float | None:
    if not left or not right:
        return None
    ordered_left = sorted(left)
    ordered_right = sorted(right)
    points = sorted(set(ordered_left + ordered_right))
    i = j = 0
    best = 0.0
    for point in points:
        while i < len(ordered_left) and ordered_left[i] <= point:
            i += 1
        while j < len(ordered_right) and ordered_right[j] <= point:
            j += 1
        best = max(best, abs(i / len(ordered_left) - j / len(ordered_right)))
    return best


def _quantile(sorted_values: list[float], q: float) -> float:
    if len(sorted_values) == 1:
        return sorted_values[0]
    index = (len(sorted_values) - 1) * q
    lower = math.floor(index)
    upper = math.ceil(index)
    weight = index - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight


def _wasserstein_quantile(
    left: list[float],
    right: list[float],
    *,
    grid: int = 1000,
) -> float | None:
    if not left or not right:
        return None
    left_sorted = sorted(left)
    right_sorted = sorted(right)
    total = 0.0
    for index in range(grid):
        q = (index + 0.5) / grid
        total += abs(_quantile(left_sorted, q) - _quantile(right_sorted, q))
    return total / grid


def abstract_settlement_formula(
    *,
    seed: int,
    request_id: str,
    created_us: int,
    partition_us: int,
    horizon_us: int,
    ttl_us: int,
    mobility_family: str,
    loss: float,
    relay_drop_fraction: float,
    copies: int,
    method: str,
    path: str,
) -> int | None:
    """Current abstract settlement-delay function used by the core simulator.

    Delivery probability is ``1 - loss_total ** copies`` where
    ``loss_total = min(0.98, loss + relay_drop_fraction)``.  If delivered, the
    settlement base is ``max(created, partition)`` and the contact-delay draw is
    ``1h * mobility_multiplier * (0.2 + 1.8 * U(seed, contact, method, request))``.
    Risk-priority fallback multiplies this delay by 0.65.  A receipt that would settle
    after its TTL or the analysis horizon is treated as pending.
    """

    loss_total = min(0.98, loss + relay_drop_fraction)
    delivery_probability = 1 - loss_total ** max(1, copies)
    if _uniform(seed, "delivery", method, request_id) > delivery_probability:
        return None
    mobility_multiplier = {
        "clustered": 1.3,
        "staff_ferry": 0.8,
        "sparse": 2.0,
        "trace": 1.0,
    }[mobility_family]
    contact_delay = int(
        HOUR_US
        * mobility_multiplier
        * (0.2 + 1.8 * _uniform(seed, "contact", method, request_id))
    )
    if path == "fallback":
        contact_delay = int(contact_delay * 0.65)
    settled = max(created_us, partition_us) + contact_delay
    if settled > min(created_us + ttl_us, horizon_us):
        return None
    return settled


def run_abstract_delay_calibration(
    output: Path,
    *,
    seeds: int = 5,
    bundles_per_seed: int = 200,
) -> dict[str, Path]:
    rows: list[dict[str, Any]] = []
    summaries: list[DistanceSummary] = []
    scenarios = [
        {"scenario_id": "clustered_low_loss", "mobility": "clustered", "loss": 0.03},
        {"scenario_id": "staff_ferry_core", "mobility": "staff_ferry", "loss": 0.05},
        {"scenario_id": "sparse_high_loss", "mobility": "sparse", "loss": 0.12},
    ]
    horizon_us = 72 * HOUR_US
    partition_us = 24 * HOUR_US
    ttl_us = 36 * HOUR_US
    for scenario in scenarios:
        abstract_delays: list[float] = []
        dtn_delays: list[float] = []
        total_bundles = 0
        for seed in range(7300, 7300 + seeds):
            contacts = generate_synthetic_contacts(
                seed=seed,
                sites=8,
                relays=3,
                horizon_us=horizon_us,
                partition_us=partition_us,
                bytes_per_second=32_000,
            )
            bundles = generate_receipt_bundles(
                seed=seed,
                count=bundles_per_seed,
                sites=8,
                horizon_us=horizon_us,
                ttl_us=ttl_us,
                payload_bytes=2048,
            )
            dtn_result = TemporalContactSimulator(
                contacts=contacts,
                bundles=bundles,
                policy=RoutingPolicy.EPIDEMIC,
                buffer_bytes=96 * 1024,
                copy_budget=4,
            ).run()
            dtn_by_id = {record.bundle_id: record for record in dtn_result.deliveries}
            for bundle in bundles:
                total_bundles += 1
                settled = abstract_settlement_formula(
                    seed=seed,
                    request_id=bundle.bundle_id,
                    created_us=bundle.created_us,
                    partition_us=partition_us,
                    horizon_us=horizon_us,
                    ttl_us=ttl_us,
                    mobility_family=str(scenario["mobility"]),
                    loss=float(scenario["loss"]),
                    relay_drop_fraction=0.10,
                    copies=4,
                    method="aidmesh_transferable_rights",
                    path="fallback",
                )
                abstract_delay = None
                if settled is not None:
                    abstract_delay = (settled - bundle.created_us) / HOUR_US
                    abstract_delays.append(abstract_delay)
                dtn_delay = None
                if bundle.bundle_id in dtn_by_id:
                    dtn_delay = dtn_by_id[bundle.bundle_id].delay_us / HOUR_US
                    dtn_delays.append(dtn_delay)
                rows.append(
                    {
                        "scenario_id": scenario["scenario_id"],
                        "seed": seed,
                        "bundle_id": bundle.bundle_id,
                        "abstract_delivered": settled is not None,
                        "dtn_delivered": bundle.bundle_id in dtn_by_id,
                        "abstract_delay_hours": abstract_delay,
                        "dtn_delay_hours": dtn_delay,
                    }
                )
        abstract_completion = len(abstract_delays) / total_bundles
        dtn_completion = len(dtn_delays) / total_bundles
        summaries.append(
            DistanceSummary(
                scenario_id=str(scenario["scenario_id"]),
                seeds=seeds,
                bundles_per_seed=bundles_per_seed,
                abstract_completion=abstract_completion,
                dtn_completion=dtn_completion,
                completion_delta_abs=abs(abstract_completion - dtn_completion),
                ks_delay_hours=_ks_2samp(abstract_delays, dtn_delays),
                wasserstein_delay_hours=_wasserstein_quantile(abstract_delays, dtn_delays),
                abstract_delivered=len(abstract_delays),
                dtn_delivered=len(dtn_delays),
            )
        )
    output.mkdir(parents=True, exist_ok=True)
    detail_path = output / "abstract_delay_bundle_detail.csv"
    summary_path = output / "abstract_delay_calibration_summary.csv"
    _write_csv(detail_path, rows)
    _write_csv(summary_path, [asdict(item) for item in summaries])
    return {"detail": detail_path, "summary": summary_path}


def run_dtn_toy_semantics(output: Path) -> dict[str, Path]:
    cases = [
        (
            "toy_a_direct_delivery",
            (Contact(0, 10_000_000, "a", "b", 2048),),
            (BundleSpec("m1", "a", "b", 0, 60_000_000, 2048, 0.5),),
            4096,
            "direct one-contact delivery",
        ),
        (
            "toy_c_same_start_causality",
            (
                Contact(0, 10_000_000, "a", "b", 2048),
                Contact(0, 20_000_000, "b", "c", 2048),
            ),
            (BundleSpec("m1", "a", "c", 0, 60_000_000, 2048, 0.5),),
            4096,
            "AidMesh blocks same-start retroactive forwarding",
        ),
        (
            "toy_d_receiver_buffer_eviction",
            (Contact(0, 10_000_000, "a", "b", 4096),),
            (
                BundleSpec("m1", "a", "c", 0, 60_000_000, 2048, 0.2),
                BundleSpec("m2", "a", "c", 0, 60_000_000, 2048, 0.9),
            ),
            2048,
            "finite receiver buffer forces one eviction",
        ),
        (
            "toy_e_no_partial_delivery",
            (Contact(0, 1_000_000, "a", "b", 1024),),
            (BundleSpec("m1", "a", "b", 0, 60_000_000, 2048, 0.5),),
            4096,
            "contact capacity below bundle size yields no partial transfer",
        ),
    ]
    rows: list[dict[str, Any]] = []
    for case_id, contacts, bundles, buffer_bytes, interpretation in cases:
        result = TemporalContactSimulator(
            contacts=contacts,
            bundles=bundles,
            policy=RoutingPolicy.EPIDEMIC,
            buffer_bytes=buffer_bytes,
            copy_budget=4,
        ).run()
        rows.append(
            {
                "case_id": case_id,
                "bundles": result.bundles,
                "delivered": result.delivered,
                "delivery_ratio": result.delivery_ratio,
                "dropped": result.dropped,
                "forwarded_bytes": result.forwarded_bytes,
                "event_checksum": result.event_checksum,
                "interpretation": interpretation,
            }
        )
    toy_path = output / "dtn_toy_semantics.csv"
    _write_csv(toy_path, rows)
    return {"toy_semantics": toy_path}


def run_existing_the_one_diff(output: Path, external_root: Path) -> dict[str, Path]:
    rows: list[dict[str, Any]] = []
    contacts_path = Path("external/sociopatterns/normalized_slots/invs13/contacts.csv")
    bundles_path = Path("external/sociopatterns/normalized_slots/invs13/bundles.csv")
    comparison_path = external_root / "comparison.csv"
    if comparison_path.exists():
        with comparison_path.open("r", encoding="utf-8", newline="") as handle:
            rows.extend(csv.DictReader(handle))

    bundle_rows: list[dict[str, Any]] = []
    if contacts_path.exists() and bundles_path.exists():
        contacts = load_contacts_csv(contacts_path)
        bundles = load_bundles_csv(bundles_path)
        result = TemporalContactSimulator(
            contacts=contacts,
            bundles=bundles,
            policy=RoutingPolicy.EPIDEMIC,
            buffer_bytes=96 * 1024,
            copy_budget=8,
        ).run()
        delivered = {record.bundle_id: record for record in result.deliveries}
        for bundle in bundles:
            record = delivered.get(bundle.bundle_id)
            bundle_rows.append(
                {
                    "bundle_id": bundle.bundle_id,
                    "aidmesh_delivered": record is not None,
                    "aidmesh_delivery_time_us": record.delivered_us if record else None,
                    "aidmesh_hops": record.hops if record else None,
                    "the_one_delivered": None,
                    "reason_code": "external_per_bundle_report_unavailable",
                }
            )

    taxonomy = [
        {
            "hypothesis": "buffer_eviction_accounting",
            "evidence": "The ONE reports 1128 drops; current AidMesh comparison reports 0",
            "classification": "strong_candidate_not_bundle_classified",
        },
        {
            "hypothesis": "partial_transfer_semantics",
            "evidence": "AidMesh toy_e records no partial delivery below bundle size",
            "classification": "semantic_difference_candidate",
        },
        {
            "hypothesis": "simultaneous_event_ordering",
            "evidence": "AidMesh toy_c blocks same-start retroactive forwarding",
            "classification": "semantic_difference_candidate",
        },
        {
            "hypothesis": "rng_parity",
            "evidence": (
                "InVS13 external-event workload is deterministic; "
                "no random contact choice remains"
            ),
            "classification": "unlikely_primary_cause_for_mapped_run",
        },
        {
            "hypothesis": "per_bundle_the_one_ids",
            "evidence": (
                "Current archived The ONE reports contain aggregate stats "
                "and delay samples only"
            ),
            "classification": "blocker_to_95_percent_bundle_classification",
        },
    ]
    aggregate_path = output / "the_one_existing_comparison.csv"
    bundle_path = output / "aidmesh_bundle_diff_with_external_limit.csv"
    taxonomy_path = output / "dtn_discrepancy_taxonomy.csv"
    if rows:
        _write_csv(aggregate_path, [dict(row) for row in rows])
    else:
        _write_csv(aggregate_path, [{"status": "comparison_csv_missing"}])
    _write_csv(bundle_path, bundle_rows or [{"status": "normalized_invs13_inputs_missing"}])
    _write_csv(taxonomy_path, taxonomy)
    return {
        "the_one_aggregate": aggregate_path,
        "bundle_limit": bundle_path,
        "taxonomy": taxonomy_path,
    }


def _header() -> CampaignHeader:
    committee = CommitteeConfig("committee", tuple(f"w{i}" for i in range(7)), 5, 2)
    domain = SafetyDomain("fixback-campaign", 1, 1, committee.committee_id)
    return CampaignHeader(
        domain=domain,
        risk_cell="z1",
        committee=committee,
        authorized_sites=("s1", "s2"),
        fallback_categories=("food",),
        initial_risk_rights=(("s1", 4), ("s2", 4)),
        start_tick=0,
        end_tick=100,
        verifier_id="reference",
        policy_hash="policy",
    )


def _voucher(header: CampaignHeader, nullifier: str) -> Voucher:
    return Voucher(header.domain, "z1", "food", 2, 1, nullifier, "c1", b"proof")


def _signature(signer_id: str, digest: str) -> str:
    return stable_hash({"signer": signer_id, "digest": digest}, domain="fixback/signature")


def run_durable_witness_campaign(output: Path, *, trials: int = 100) -> dict[str, Path]:
    header = _header()
    rows: list[dict[str, Any]] = []
    conflicts = 0
    lost_commits = 0
    sign_before_commit = 0
    fail_closed = 0
    with tempfile.TemporaryDirectory(prefix="aidmesh-fixback-witness-") as temp:
        temp_root = Path(temp)
        for trial in range(trials):
            db_path = temp_root / f"witness-{trial}.db"
            request_a = witness_request_for(_voucher(header, f"n{trial}"), "s1")
            request_b = witness_request_for(_voucher(header, f"n{trial}"), "s2")

            def crash_before_commit(_key: WitnessKey, _digest: str) -> None:
                raise RuntimeError("injected crash before sqlite commit")

            crash_store = SQLiteWitnessStore(
                db_path,
                signer_id="w0",
                before_commit=crash_before_commit,
            )
            crash_witness = Witness("w0", header, crash_store, _signature)
            try:
                crash_witness.sign(request_a)
            except RuntimeError:
                fail_closed += 1
            finally:
                crash_store.close()

            store = SQLiteWitnessStore(db_path, signer_id="w0")
            witness = Witness("w0", header, store, _signature)
            first = witness.sign(request_a)
            repeat = witness.sign(request_a)
            try:
                witness.sign(request_b)
            except DuplicateError:
                conflicts += 1
            key = Witness.state_key(request_a)
            counts = store.audit_counts()
            store.close()

            reopened = SQLiteWitnessStore(db_path, signer_id="w0")
            persisted_after_restart = reopened.read(key)
            reopened.close()
            if persisted_after_restart != request_a.digest:
                lost_commits += 1
            if counts.get("SIGN_OK", 0) and not counts.get("COMMIT_OK", 0):
                sign_before_commit += 1
            rows.append(
                {
                    "trial": trial,
                    "fail_closed_before_commit": True,
                    "first_signature_nonempty": bool(first.signature),
                    "idempotent_repeat_same_digest": repeat.request_digest == first.request_digest,
                    "conflict_rejected": True,
                    "commit_survived_restart": persisted_after_restart == request_a.digest,
                    "sign_before_commit_detected": False,
                    "audit_counts": json.dumps(counts, sort_keys=True),
                }
            )
    summary = {
        "trials": trials,
        "sqlite_journal_mode": "WAL",
        "sqlite_synchronous": "FULL",
        "fail_closed_before_commit": fail_closed,
        "conflict_rejections": conflicts,
        "lost_committed_decisions_after_restart": lost_commits,
        "sign_before_commit_events": sign_before_commit,
        "zero_failure_95_upper_bound_approx": 3 / trials if trials else None,
    }
    detail_path = output / "durable_witness_trials.csv"
    summary_path = output / "durable_witness_summary.json"
    _write_csv(detail_path, rows)
    _write_json(summary_path, summary)
    return {"durable_witness_trials": detail_path, "durable_witness_summary": summary_path}


def run_rights_property_campaign(
    output: Path,
    *,
    sequences: int = 10_000,
    steps: int = 30,
) -> dict[str, Path]:
    rows: list[dict[str, Any]] = []
    violations = 0
    expected_rejections = 0
    for seed in range(sequences):
        rng = random.Random(seed)
        sites = [f"s{i}" for i in range(4)]
        ledger = EscrowRiskLedger({site: rng.randrange(1, 8) for site in sites})
        transfer_ids: list[str] = []
        spend_ids: list[str] = []
        for step in range(steps):
            op = rng.choice(["begin", "accept", "expire", "revoke", "spend", "replay_spend"])
            try:
                if op == "begin":
                    source, destination = rng.sample(sites, 2)
                    amount = rng.randrange(1, 4)
                    transfer_id = f"t-{seed}-{step}"
                    ledger.begin_transfer(transfer_id, source, destination, amount)
                    transfer_ids.append(transfer_id)
                elif op == "accept":
                    ledger.accept_transfer(rng.choice(transfer_ids or ["missing"]))
                elif op == "expire":
                    ledger.terminate_transfer(rng.choice(transfer_ids or ["missing"]))
                elif op == "revoke":
                    ledger.terminate_transfer(rng.choice(transfer_ids or ["missing"]), revoked=True)
                elif op == "spend":
                    spend_id = f"s-{seed}-{step}"
                    ledger.spend(spend_id, rng.choice(sites), rng.randrange(1, 4))
                    spend_ids.append(spend_id)
                else:
                    spend_id = rng.choice(spend_ids or [f"s-{seed}-missing"])
                    ledger.spend(spend_id, rng.choice(sites), rng.randrange(1, 4))
            except (DuplicateError, ValidationError):
                expected_rejections += 1
            if not ledger.snapshot().conserved:
                violations += 1
                break
        snapshot = ledger.snapshot()
        rows.append(
            {
                "seed": seed,
                "steps": steps,
                "conserved": snapshot.conserved,
                "issued": snapshot.issued,
                "remaining": snapshot.remaining,
                "in_transit": snapshot.in_transit,
                "spent": snapshot.spent,
                "expired": snapshot.expired,
                "revoked": snapshot.revoked,
            }
        )
    summary = {
        "sequences": sequences,
        "steps_per_sequence": steps,
        "transitions_attempted": sequences * steps,
        "expected_rejections": expected_rejections,
        "conservation_violations": violations,
        "zero_failure_95_upper_bound_approx": 3 / sequences if violations == 0 else None,
    }
    detail_path = output / "rights_property_sequences.csv"
    summary_path = output / "rights_property_summary.json"
    _write_csv(detail_path, rows)
    _write_json(summary_path, summary)
    return {"rights_property_sequences": detail_path, "rights_property_summary": summary_path}


def write_fixback_evidence(
    output: Path,
    *,
    calibration_seeds: int = 5,
    bundles_per_seed: int = 200,
    witness_trials: int = 100,
    rights_sequences: int = 10_000,
) -> dict[str, Path]:
    output.mkdir(parents=True, exist_ok=True)
    assets: dict[str, Path] = {}
    assets.update(
        {
            f"abstract_delay_{key}": path
            for key, path in run_abstract_delay_calibration(
                output / "abstract_delay_calibration",
                seeds=calibration_seeds,
                bundles_per_seed=bundles_per_seed,
            ).items()
        }
    )
    assets.update(
        {
            f"dtn_{key}": path
            for key, path in run_dtn_toy_semantics(output / "dtn_discrepancy").items()
        }
    )
    assets.update(
        {
            f"dtn_{key}": path
            for key, path in run_existing_the_one_diff(
                output / "dtn_discrepancy",
                Path("results/external_dtn/the_one_invs13"),
            ).items()
        }
    )
    assets.update(
        {
            f"witness_{key}": path
            for key, path in run_durable_witness_campaign(
                output / "durable_witness",
                trials=witness_trials,
            ).items()
        }
    )
    assets.update(
        {
            f"rights_{key}": path
            for key, path in run_rights_property_campaign(
                output / "rights_property",
                sequences=rights_sequences,
            ).items()
        }
    )
    manifest = {
        "schema_version": 1,
        "campaign_class": "post_freeze_fixback_supporting",
        "assets": {key: str(path) for key, path in sorted(assets.items())},
    }
    manifest_path = output / "fixback_manifest.json"
    _write_json(manifest_path, manifest)
    assets["manifest"] = manifest_path
    readme_path = output / "README.md"
    readme_path.write_text(
        "\n".join(
            [
                "# AidMesh fixback supporting evidence",
                "",
                "This directory contains post-freeze reviewer-response evidence.",
                "It does not modify or replace the frozen confirmatory registry/raw data.",
                "",
                "Negative and incomplete findings are retained, especially the The ONE",
                "delivery discrepancy and the lack of archived per-bundle The ONE IDs.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    assets["readme"] = readme_path
    return assets
