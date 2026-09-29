"""Paired, deterministic AidMesh protocol experiment.

This is an executable *system model*, not a field-data generator and not the P1
zero-knowledge implementation.  It exercises the quorum and conserved-risk state
machines under a shared stochastic world so methods receive identical requests,
network conditions and adversarial choices.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Iterable
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from aidmesh.common.canonical import stable_hash
from aidmesh.config import digest_config, validate_scenario
from aidmesh.protocol.campaign import SafetyDomain
from aidmesh.protocol.risk import RiskRightsLedger

from .events import EventRecorder

METHODS = (
    "online_only",
    "coconut_online",
    "first_seen_dtn",
    "aidmesh_quorum_only",
    "aidmesh_static_rights",
    "aidmesh_transferable_rights",
    "fixed_station",
    "periodic_sync",
    "optimized_static_placement",
    "wang_seen_tag_sync_1h",
    "wang_seen_tag_sync_6h",
    "wang_seen_tag_sync_24h",
)
HOUR_US = 3_600_000_000
WANG_SEEN_TAG_BYTES = 32
WANG_SEEN_TAG_SYNC_INTERVALS = {
    "wang_seen_tag_sync_1h": HOUR_US,
    "wang_seen_tag_sync_6h": 6 * HOUR_US,
    "wang_seen_tag_sync_24h": 24 * HOUR_US,
}


def _uniform(seed: int, *parts: object) -> float:
    payload = "|".join([str(seed), *(str(part) for part in parts)]).encode("utf-8")
    value = int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")
    return value / 2**64


def _integer(seed: int, stop: int, *parts: object) -> int:
    if stop <= 0:
        raise ValueError("stop must be positive")
    return min(int(_uniform(seed, *parts) * stop), stop - 1)


@dataclass(frozen=True)
class Attempt:
    request_id: str
    nullifier: str
    beneficiary: int
    site: int
    cell: int
    category: int
    group: int
    value: int
    created_us: int
    deadline_us: int
    adversarial_duplicate: bool


@dataclass(frozen=True)
class Release:
    request_id: str
    nullifier: str
    site: int
    value: int
    path: str
    released_us: int
    settled_us: int | None
    receipt_id: str
    payload_bytes: int
    copies: int


@dataclass(frozen=True)
class ExperimentResult:
    run_id: str
    scenario_id: str
    method: str
    seed: int
    requests: int
    attempts: int
    accepted_releases: int
    completed_requests: int
    completion_rate: float
    non_home_completion_rate: float
    quorum_releases: int
    fallback_releases: int
    conflicts: int
    duplicate_exposure: int
    physical_duplicate_exposure: int
    risk_cap: int
    exposure_cap_ratio: float
    settled_receipts: int
    pending_receipts: int
    mean_settlement_hours: float | None
    rmst_hours: float
    transmitted_bytes: int
    bytes_per_settlement: float | None
    conflicting_quorum_certificates: int
    risk_rights_conserved: bool
    seen_tag_sync_rounds: int
    seen_tag_payload_bytes: int
    event_checksum: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _world(config: dict[str, Any]) -> tuple[Attempt, ...]:
    seed = config["seed"]
    population = config["population"]
    horizon = config["analysis_horizon_us"]
    sites = population["sites"]
    attempts: list[Attempt] = []
    request_window = max(1, int(horizon * 0.65))
    service_window = min(config["routing"]["ttl_us"], max(HOUR_US, horizon // 2))
    for beneficiary in range(population["beneficiaries"]):
        home = beneficiary % sites
        cell = home % population["risk_cells"]
        created = _integer(seed, request_window, "created", beneficiary)
        deadline = min(horizon, created + service_window)
        category = _integer(seed, population["categories"], "category", beneficiary)
        group = _integer(seed, population["groups"], "group", beneficiary)
        value = 1 + _integer(seed, 3, "value", beneficiary)
        nullifier = stable_hash(
            {"scenario": config["scenario_id"], "beneficiary": beneficiary},
            domain="aidmesh/simulator/nullifier/v1",
        )
        request_id = f"r-{beneficiary:06d}-0"
        attempts.append(
            Attempt(
                request_id,
                nullifier,
                beneficiary,
                home,
                cell,
                category,
                group,
                value,
                created,
                deadline,
                False,
            )
        )
        if _uniform(seed, "adversarial", beneficiary) < config["adversary"]["beneficiary_fraction"]:
            offset = 1 + _integer(seed, sites - 1, "duplicate-site", beneficiary)
            duplicate_site = (home + offset) % sites
            duplicate_cell = duplicate_site % population["risk_cells"]
            jitter = _integer(
                seed, min(HOUR_US, max(1, service_window // 4)), "dup-jitter", beneficiary
            )
            attempts.append(
                Attempt(
                    f"r-{beneficiary:06d}-1",
                    nullifier,
                    beneficiary,
                    duplicate_site,
                    duplicate_cell,
                    category,
                    group,
                    value,
                    min(deadline, created + jitter),
                    deadline,
                    True,
                )
            )
    return tuple(sorted(attempts, key=lambda item: (item.created_us, item.request_id)))


def _initial_rights(config: dict[str, Any]) -> dict[str, int]:
    sites = config["population"]["sites"]
    total = config["fallback"]["total_rights"]
    base, residual = divmod(total, sites)
    rights = {f"site-{site}": base for site in range(sites)}
    if config["fallback"]["placement"] == "demand":
        # Deterministic skew known before the run; total is still exactly conserved.
        weights = [site + 1 for site in range(sites)]
        weight_total = sum(weights)
        rights = {f"site-{site}": (total * weights[site]) // weight_total for site in range(sites)}
        residual = total - sum(rights.values())
    for site in range(residual):
        rights[f"site-{site % sites}"] += 1
    return rights


def _reachable_witnesses(config: dict[str, Any], attempt: Attempt) -> tuple[int, ...]:
    quorum = config["quorum"]
    available = []
    for witness in range(quorum["K"]):
        if (
            _uniform(config["seed"], "witness", attempt.request_id, witness)
            < quorum["witness_availability"]
        ):
            available.append(witness)
    return tuple(available)


def _temporal_path_index(config: dict[str, Any], *, trace_path: Path | None = None):
    from aidmesh.experiments.temporal_frontier import TemporalPathIndex
    from aidmesh.simulator.dtn import generate_synthetic_contacts, load_contacts_csv

    if trace_path is not None:
        contacts = load_contacts_csv(trace_path)
        return TemporalPathIndex(contacts)

    population = config["population"]
    contacts = generate_synthetic_contacts(
        seed=config["seed"],
        sites=population["sites"],
        relays=max(3, population["sites"] // 8),
        horizon_us=config["analysis_horizon_us"],
        partition_us=config["network"]["partition_us"],
        bytes_per_second=max(8_000, config["network"]["bandwidth_bps"] // 8),
    )
    return TemporalPathIndex(contacts)


def _settlement(
    config: dict[str, Any],
    attempt: Attempt,
    path: str,
    method: str,
    *,
    release_us: int | None = None,
    temporal_index: Any | None = None,
) -> tuple[int | None, int, int]:
    routing = config["routing"]
    partition = config["network"]["partition_us"]
    payload = max(routing["payload_bytes"], routing["padding_bucket_bytes"])
    copies = routing["copy_allowance"]
    if method in {"online_only", "coconut_online", "periodic_sync"}:
        copies = 1
    if temporal_index is not None:
        emitted_us = release_us if release_us is not None else attempt.created_us
        ttl_end = min(attempt.created_us + routing["ttl_us"], config["analysis_horizon_us"])
        settled_us = temporal_index.earliest(
            f"site-{attempt.site}",
            "contract",
            emitted_us,
            ttl_end,
        )
        if settled_us is None or settled_us > ttl_end:
            return None, payload * copies, copies
        return settled_us, payload * copies, copies
    loss = min(
        0.98,
        config["network"]["loss"] + config["adversary"]["relay_drop_fraction"],
    )
    delivery_probability = 1 - loss ** max(1, copies)
    # Paired methods must face the same exogenous delivery draw.  The method may
    # change copy count (and therefore the probability threshold), but it must
    # not receive a different random world merely because its label differs.
    if _uniform(config["seed"], "delivery", attempt.request_id) > delivery_probability:
        return None, payload * copies, copies
    base = max(attempt.created_us, partition)
    mobility_multiplier = {
        "clustered": 1.3,
        "staff_ferry": 0.8,
        "sparse": 2.0,
        "trace": 1.0,
    }[config["network"]["mobility_family"]]
    contact_delay = int(
        HOUR_US
        * mobility_multiplier
        * (0.2 + 1.8 * _uniform(config["seed"], "contact", attempt.request_id))
    )
    if config["routing"]["method"] == "risk_priority" and path == "fallback":
        contact_delay = int(contact_delay * 0.65)
    settled = base + contact_delay
    ttl_end = attempt.created_us + routing["ttl_us"]
    if settled > min(ttl_end, config["analysis_horizon_us"]):
        return None, payload * copies, copies
    return settled, payload * copies, copies


def _try_transfer(
    config: dict[str, Any], ledger: RiskRightsLedger, destination: str, attempt: Attempt
) -> bool:
    if ledger.balance(destination) >= attempt.value:
        return True
    if not config["fallback"]["transfer_enabled"]:
        return False
    # Transfers are only possible after a modeled inter-site contact.  The
    # probability is derived from the same network regime and does not depend on
    # the method's outcome.
    contact_probability = {
        "clustered": 0.25,
        "staff_ferry": 0.75,
        "sparse": 0.12,
        "trace": 0.5,
    }[config["network"]["mobility_family"]]
    if _uniform(config["seed"], "rights-contact", attempt.request_id) >= contact_probability:
        return False
    snapshot = ledger.snapshot()
    donors = sorted(snapshot.balances, key=lambda pair: (-pair[1], pair[0]))
    for donor, balance in donors:
        if donor == destination or balance <= attempt.value:
            continue
        needed = attempt.value - ledger.balance(destination)
        amount = min(needed, balance - attempt.value)
        if amount <= 0:
            continue
        ledger.transfer(
            transfer_id=f"transfer-{attempt.request_id}-{donor}-{destination}",
            source_site=donor,
            destination_site=destination,
            amount=amount,
            expected_source=ledger.state_token(donor),
            expected_destination=ledger.state_token(destination),
        )
        return ledger.balance(destination) >= attempt.value
    return False


def _synchronize_seen_tags(
    site_seen: set[tuple[int, str]], *, sites: int
) -> tuple[int, int]:
    """Replicate every locally seen Wang tag to all sites.

    The byte count is a lower bound on tag payload only: one 32-byte tag for
    each site that did not already hold it.  It excludes framing, signatures,
    transport acknowledgements and audit records.  The paper specifies tag-list
    synchronization but not a transport protocol, so those costs are kept out of
    the matched authorization model.
    """

    holders: dict[str, set[int]] = {}
    for site, tag in site_seen:
        holders.setdefault(tag, set()).add(site)
    missing_copies = sum(sites - len(tag_holders) for tag_holders in holders.values())
    for tag in holders:
        site_seen.update((site, tag) for site in range(sites))
    return len(holders), missing_copies * WANG_SEEN_TAG_BYTES


def run_experiment(
    config: dict[str, Any],
    method: str,
    *,
    experiment_id: str = "E1",
    raw_path: Path | None = None,
    software_commit: str = "workspace-unversioned",
    settlement_mode: str = "abstract",
    trace_path: Path | None = None,
) -> ExperimentResult:
    config = validate_scenario(config)
    if method not in METHODS:
        raise ValueError(f"unknown method {method!r}")
    if settlement_mode not in {"abstract", "temporal"}:
        raise ValueError(f"unknown settlement_mode {settlement_mode!r}")
    config_hash = digest_config(config)
    domain = SafetyDomain(config["scenario_id"], 1, 1, "committee-v1")
    run_id = f"{experiment_id}-{config['scenario_id']}-{method}-S{config['seed']:04d}"
    recorder = EventRecorder(
        run_id,
        experiment_id,
        config["scenario_id"],
        config["phase"],
        method,
        config["seed"],
        domain.digest,
        config_hash,
        software_commit,
    )
    attempts = _world(config)
    effective_config = config
    if method == "optimized_static_placement":
        effective_config = {
            **config,
            "fallback": {**config["fallback"], "placement": "demand"},
        }
    rights = _initial_rights(effective_config)
    temporal_index = (
        _temporal_path_index(config, trace_path=trace_path)
        if settlement_mode == "temporal"
        else None
    )
    sync_window_us = 6 * HOUR_US
    seen_tag_interval_us = WANG_SEEN_TAG_SYNC_INTERVALS.get(method)
    next_seen_tag_sync_us = (
        config["network"]["partition_us"] if seen_tag_interval_us is not None else None
    )
    seen_tag_sync_rounds = 0
    seen_tag_payload_bytes = 0
    ledger = RiskRightsLedger(domain, rights)
    witness_decisions: dict[tuple[str, int], str] = {}
    site_seen: set[tuple[int, str]] = set()
    online_seen: set[str] = set()
    releases: list[Release] = []
    quorum_certificates: dict[str, set[str]] = {}

    for attempt in attempts:
        if seen_tag_interval_us is not None and next_seen_tag_sync_us is not None:
            while next_seen_tag_sync_us <= attempt.created_us:
                synchronized_tags, payload_bytes = _synchronize_seen_tags(
                    site_seen, sites=config["population"]["sites"]
                )
                seen_tag_sync_rounds += 1
                seen_tag_payload_bytes += payload_bytes
                recorder.emit(
                    "seen_tags_synchronized",
                    next_seen_tag_sync_us,
                    actor_id="gateway",
                    actor_role="gateway",
                    payload_bytes=payload_bytes,
                    copies=synchronized_tags,
                    path="online",
                )
                next_seen_tag_sync_us += seen_tag_interval_us
        recorder.emit(
            "redemption_requested",
            attempt.created_us,
            actor_id=f"beneficiary-{attempt.beneficiary}",
            actor_role="beneficiary",
            risk_cell=f"cell-{attempt.cell}",
            group_code=f"group-{attempt.group}",
            category=f"category-{attempt.category}",
            value_units=attempt.value,
            nullifier_hash=attempt.nullifier,
            deadline_us=attempt.deadline_us,
            path="none",
        )
        home_site = attempt.beneficiary % config["population"]["sites"]
        service_site = home_site if method == "fixed_station" else attempt.site

        local_key = (service_site, attempt.nullifier)
        if local_key in site_seen:
            recorder.emit(
                "local_duplicate_rejected",
                attempt.created_us,
                actor_id=f"site-{attempt.site}",
                actor_role="site",
                nullifier_hash=attempt.nullifier,
                terminal_outcome="rejected",
                failure_class="PROTOCOL_INVALID",
                failure_code="LOCAL_DUPLICATE",
                path="none",
            )
            continue

        path: str | None = None
        release_time = attempt.created_us
        request_digest = stable_hash(
            {"nullifier": attempt.nullifier, "site": service_site},
            domain="aidmesh/simulator/witness-request/v1",
        )
        if method in {"online_only", "coconut_online", "periodic_sync"}:
            release_time = max(attempt.created_us, config["network"]["partition_us"])
            if method == "periodic_sync":
                if release_time < config["network"]["partition_us"]:
                    release_time = config["network"]["partition_us"]
                offset = release_time - config["network"]["partition_us"]
                remainder = offset % sync_window_us
                if remainder:
                    release_time += sync_window_us - remainder
            if release_time <= attempt.deadline_us and attempt.nullifier not in online_seen:
                path = "online"
                online_seen.add(attempt.nullifier)
        elif method == "first_seen_dtn" or method in WANG_SEEN_TAG_SYNC_INTERVALS:
            path = "online"
        else:
            signers = 0
            for witness in _reachable_witnesses(config, attempt):
                decision_key = (attempt.nullifier, witness)
                prior = witness_decisions.get(decision_key)
                byzantine = witness < config["adversary"]["byzantine_witnesses"]
                if prior is None:
                    witness_decisions[decision_key] = request_digest
                    signers += 1
                elif prior == request_digest or byzantine:
                    signers += 1
            if signers >= config["quorum"]["q"]:
                path = "quorum"
                quorum_certificates.setdefault(attempt.nullifier, set()).add(request_digest)
            elif method not in {"aidmesh_quorum_only"} and config["fallback"]["enabled"]:
                site_id = f"site-{service_site}"
                transferable = method == "aidmesh_transferable_rights"
                can_pay = ledger.balance(site_id) >= attempt.value
                if not can_pay and transferable:
                    can_pay = _try_transfer(config, ledger, site_id, attempt)
                if can_pay:
                    before = ledger.balance(site_id)
                    ledger.debit(
                        site_id=site_id,
                        debit_id=f"debit-{attempt.request_id}",
                        nullifier=attempt.nullifier,
                        amount=attempt.value,
                        expected_state=ledger.state_token(site_id),
                    )
                    path = "fallback"
                    recorder.emit(
                        "risk_right_debited",
                        attempt.created_us,
                        actor_id=site_id,
                        actor_role="site",
                        nullifier_hash=attempt.nullifier,
                        value_units=attempt.value,
                        rights_before=before,
                        rights_after=ledger.balance(site_id),
                        path="fallback",
                    )

        if path is None or release_time > attempt.deadline_us:
            recorder.emit(
                "redemption_deferred",
                min(release_time, config["analysis_horizon_us"]),
                actor_id=f"site-{attempt.site}",
                actor_role="site",
                nullifier_hash=attempt.nullifier,
                terminal_outcome="rejected",
                failure_class="AVAILABILITY",
                failure_code="NO_AUTHORIZATION_PATH",
                path="none",
            )
            continue
        site_seen.add(local_key)
        settlement_attempt = replace(attempt, site=service_site)
        settled_us, byte_count, copies = _settlement(
            config,
            settlement_attempt,
            path,
            method,
            release_us=release_time,
            temporal_index=temporal_index,
        )
        receipt_id = stable_hash(
            {"run": run_id, "request": attempt.request_id, "path": path},
            domain="aidmesh/simulator/receipt/v1",
        )
        releases.append(
            Release(
                attempt.request_id,
                attempt.nullifier,
                service_site,
                attempt.value,
                path,
                release_time,
                settled_us,
                receipt_id,
                byte_count,
                copies,
            )
        )
        recorder.emit(
            "inventory_released",
            release_time,
            actor_id=f"site-{service_site}",
            actor_role="site",
            nullifier_hash=attempt.nullifier,
            receipt_id=receipt_id,
            bundle_id=f"b-{receipt_id[:16]}",
            value_units=attempt.value,
            payload_bytes=byte_count,
            copies=copies,
            deadline_us=attempt.deadline_us,
            path=path,
        )

    winners: dict[str, Release] = {}
    conflicts = 0
    exposure = 0
    for release in sorted(
        releases,
        key=lambda item: (
            item.settled_us is None,
            item.settled_us if item.settled_us is not None else math.inf,
            item.receipt_id,
        ),
    ):
        if release.settled_us is None:
            continue
        winner = winners.get(release.nullifier)
        if winner is None:
            winners[release.nullifier] = release
            outcome = "settled"
            event_type = "receipt_settled"
        else:
            conflicts += 1
            exposure += release.value
            outcome = "conflict_lost"
            event_type = "receipt_conflict"
        recorder.emit(
            event_type,
            release.settled_us,
            actor_id="contract",
            actor_role="contract",
            nullifier_hash=release.nullifier,
            receipt_id=release.receipt_id,
            value_units=release.value,
            terminal_outcome=outcome,
            path=release.path,
        )

    physical_winners: set[str] = set()
    physical_exposure = 0
    for release in sorted(releases, key=lambda item: (item.released_us, item.receipt_id)):
        if release.nullifier in physical_winners:
            physical_exposure += release.value
        else:
            physical_winners.add(release.nullifier)

    original_requests = config["population"]["beneficiaries"]
    released_by_nullifier: dict[str, list[Release]] = {}
    for release in releases:
        released_by_nullifier.setdefault(release.nullifier, []).append(release)
    completed = len(released_by_nullifier)
    non_home = 0
    for _nullifier, items in released_by_nullifier.items():
        if any(int(item.request_id.split("-")[-1]) == 1 for item in items):
            non_home += 1
    safe = config["quorum"]["safe_label"]
    conflicting_quorum = sum(max(0, len(digests) - 1) for digests in quorum_certificates.values())
    if safe and conflicting_quorum:
        raise AssertionError("safe configuration produced conflicting quorum certificates")
    ledger.assert_conserved()
    snapshot = ledger.snapshot()
    if method.startswith("aidmesh_") and exposure > snapshot.debited_total:
        raise AssertionError("duplicate exposure exceeded consumed fallback risk rights")

    settled = [release for release in releases if release.settled_us is not None]
    delays = [release.settled_us - release.released_us for release in settled]
    horizon = config["analysis_horizon_us"]
    capped_delays = [
        (release.settled_us - release.released_us)
        if release.settled_us is not None
        else max(0, horizon - release.released_us)
        for release in releases
    ]
    transmitted = sum(release.payload_bytes for release in releases)
    result = ExperimentResult(
        run_id=run_id,
        scenario_id=config["scenario_id"],
        method=method,
        seed=config["seed"],
        requests=original_requests,
        attempts=len(attempts),
        accepted_releases=len(releases),
        completed_requests=completed,
        completion_rate=completed / original_requests,
        non_home_completion_rate=non_home / original_requests,
        quorum_releases=sum(release.path == "quorum" for release in releases),
        fallback_releases=sum(release.path == "fallback" for release in releases),
        conflicts=conflicts,
        duplicate_exposure=exposure,
        physical_duplicate_exposure=physical_exposure,
        risk_cap=config["fallback"]["total_rights"],
        exposure_cap_ratio=(
            exposure / config["fallback"]["total_rights"]
            if config["fallback"]["total_rights"]
            else (0.0 if exposure == 0 else math.inf)
        ),
        settled_receipts=len(settled),
        pending_receipts=len(releases) - len(settled),
        mean_settlement_hours=(sum(delays) / len(delays) / HOUR_US if delays else None),
        rmst_hours=(sum(capped_delays) / len(capped_delays) / HOUR_US if capped_delays else 0.0),
        transmitted_bytes=transmitted,
        bytes_per_settlement=(transmitted / len(settled) if settled else None),
        conflicting_quorum_certificates=conflicting_quorum,
        risk_rights_conserved=snapshot.is_conserved,
        seen_tag_sync_rounds=seen_tag_sync_rounds,
        seen_tag_payload_bytes=seen_tag_payload_bytes,
        event_checksum=recorder.checksum,
    )
    if raw_path is not None:
        recorder.validate(Path(__file__).resolve().parents[2] / "schemas" / "event.schema.json")
        recorder.write_jsonl(raw_path)
    return result


def run_matrix(
    configs: Iterable[dict[str, Any]], methods: Iterable[str]
) -> tuple[ExperimentResult, ...]:
    return tuple(run_experiment(config, method) for config in configs for method in methods)
