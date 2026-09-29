"""Best-of-both-worlds causal authorization over one shared DTN queue.

This campaign is deliberately stricter than the legacy held-out campaign:
requests, quorum exchanges, right transfers, periodic synchronization and
receipts are all causally generated and contend for the same per-contact byte
budget and finite node buffers.  The offline comparator replaces only online
right-transfer bytes in the realized queue with an integral time-expanded
flow; it never repairs request transport failures.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd

from aidmesh.analysis.quantitative_frontier import completion_from_identity, l1_misplacement
from aidmesh.analysis.time_expanded_frontier import (
    TemporalDemand,
    solve_time_expanded_oracle,
    transport_conditioning_gap,
)
from aidmesh.common.canonical import stable_hash
from aidmesh.experiments.temporal_frontier import (
    HeldoutRequest,
    TemporalPathIndex,
    _calibration_roles,
    _initial_balances,
    _optimized_static_balances,
    generate_heldout_requests,
)
from aidmesh.policy.adatransfer import AdaTransferAllocator
from aidmesh.policy.conservative_reclaimable import ConservativeRegimeController
from aidmesh.simulator.dtn import HOUR_US, Contact, RoutingPolicy, load_contacts_csv
from aidmesh.simulator.shared_queue import (
    MessageClass,
    SharedBundleSpec,
    SharedDeliveryRecord,
    SharedNetworkSnapshot,
    SharedTemporalContactSimulator,
    aggregate_contact_residual_units,
)

ONLINE_POLICIES = (
    "quorum_only",
    "equal_static",
    "optimized_static",
    "robust_static",
    "rolling_relocation",
    "myopic_transfer",
    "adatransfer",
    "periodic_sync",
)
CATR_POLICIES = (
    "conservative_transfer",
    "reclaimable_transfer",
    "catr",
    "reclaimable_transfer_v2",
    "catr_v2",
)
LOCAL_PULL_POLICIES = {"local_pull", "local_pull_certified"}
ALL_CAUSAL_POLICIES = (*ONLINE_POLICIES, *CATR_POLICIES, *sorted(LOCAL_PULL_POLICIES))
RIGHTS_POLICIES = {
    *LOCAL_PULL_POLICIES,
    "equal_static",
    "optimized_static",
    "robust_static",
    "rolling_relocation",
    "myopic_transfer",
    "adatransfer",
    "conservative_transfer",
    "reclaimable_transfer",
    "catr",
    "reclaimable_transfer_v2",
    "catr_v2",
}
TRANSFER_POLICIES = {
    *LOCAL_PULL_POLICIES,
    "rolling_relocation",
    "myopic_transfer",
    "adatransfer",
    "conservative_transfer",
    "reclaimable_transfer",
    "catr",
    "reclaimable_transfer_v2",
    "catr_v2",
}
RECLAIMABLE_POLICIES = {
    "local_pull_certified",  # Accept certificate required; no unilateral reclaim.
    "reclaimable_transfer",
    "catr",
    "reclaimable_transfer_v2",
    "catr_v2",
}
RECOVERABLE_VOTE_POLICIES = {"reclaimable_transfer_v2", "catr_v2"}
CONSERVATIVE_POLICIES = {"conservative_transfer", "catr", "catr_v2"}


def _stable_unit(payload: object, *, domain: str) -> float:
    return int(stable_hash(payload, domain=domain)[:16], 16) / float(16**16)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def perturb_contacts_for_sensitivity(
    contacts: tuple[Contact, ...],
    *,
    context: str,
    temporal_jitter_us: int = 0,
    contact_capacity_scale: float = 1.0,
) -> tuple[Contact, ...]:
    """Apply deterministic, outcome-blind contact perturbations.

    Jitter translates a complete contact interval and therefore preserves its
    duration. Capacity scaling changes only the registered byte rate. The
    transformation is deterministic in the immutable contact identity so paired
    policies always see the same perturbed temporal graph.
    """

    if temporal_jitter_us < 0:
        raise ValueError("temporal_jitter_us must be nonnegative")
    if contact_capacity_scale <= 0:
        raise ValueError("contact_capacity_scale must be positive")
    transformed = []
    for ordinal, contact in enumerate(contacts):
        identity = {
            "context": context,
            "ordinal": ordinal,
            "start_us": contact.start_us,
            "end_us": contact.end_us,
            "node_a": contact.node_a,
            "node_b": contact.node_b,
        }
        jitter_fraction = _stable_unit(
            identity,
            domain="aidmesh/contact-jitter/v1",
        )
        offset_us = round((2 * jitter_fraction - 1) * temporal_jitter_us)
        duration_us = max(0, contact.end_us - contact.start_us)
        start_us = max(0, contact.start_us + offset_us)
        transformed.append(
            Contact(
                start_us=start_us,
                end_us=start_us + duration_us,
                node_a=contact.node_a,
                node_b=contact.node_b,
                bytes_per_second=max(
                    1,
                    round(contact.bytes_per_second * contact_capacity_scale),
                ),
            )
        )
    return tuple(
        sorted(
            transformed,
            key=lambda row: (row.start_us, row.end_us, row.node_a, row.node_b),
        )
    )


def _hamilton(sites: tuple[str, ...], budget: int, weights: tuple[float, ...]) -> dict[str, int]:
    if len(sites) != len(weights) or budget < 0 or any(value < 0 for value in weights):
        raise ValueError("invalid Hamilton apportionment")
    total = sum(weights)
    normalized = tuple((value / total) if total else (1 / len(sites)) for value in weights)
    quotas = tuple(budget * value for value in normalized)
    allocation = [math.floor(value) for value in quotas]
    order = sorted(
        range(len(sites)),
        key=lambda index: (-(quotas[index] - allocation[index]), sites[index]),
    )
    for index in order[: budget - sum(allocation)]:
        allocation[index] += 1
    return dict(zip(sites, allocation, strict=True))


def _robust_static_balances(sites: tuple[str, ...], cap: int) -> dict[str, int]:
    equal = _initial_balances(sites, cap)
    optimized = _optimized_static_balances(sites, cap)
    weights = tuple((equal[site] + optimized[site]) / 2 for site in sites)
    return _hamilton(sites, cap, weights)


def _initial_for_policy(policy: str, sites: tuple[str, ...], cap: int) -> dict[str, int]:
    if policy in {"optimized_static", *CATR_POLICIES}:
        return _optimized_static_balances(sites, cap)
    if policy in {"robust_static", *LOCAL_PULL_POLICIES}:
        return _robust_static_balances(sites, cap)
    return _initial_balances(sites, cap)


@lru_cache(maxsize=32)
def calibration_path_reliability(
    contacts: tuple[Contact, ...],
    *,
    sites: tuple[str, ...],
    cutoff_us: int,
    window_us: int = 24 * HOUR_US,
) -> dict[tuple[str, str], float]:
    """Estimate deadline-matched time-respecting delivery from the prefix.

    Each complete calibration window contributes one Bernoulli observation for
    exact slot-causal store-carry-forward reachability within the campaign's
    24-hour request TTL.  Unlike undirected component membership, this respects
    contact order and therefore cannot label a temporally reversed path as
    deliverable.  The suffix is never queried.
    """

    if window_us <= 0:
        raise ValueError("reliability window must be positive")
    calibration = tuple(contact for contact in contacts if contact.start_us < cutoff_us)
    if not calibration:
        return {(left, right): float(left == right) for left in sites for right in sites}
    origin = min(contact.start_us for contact in calibration)
    complete_starts = tuple(range(origin, cutoff_us - window_us + 1, window_us))
    # Very short traces (notably ENE5AI) still contribute one prefix-only
    # observation over their complete available calibration horizon.
    window_starts = complete_starts or (origin,)
    path_index = TemporalPathIndex(
        tuple(
            sorted(
                calibration,
                key=lambda row: (
                    row.start_us,
                    row.end_us,
                    row.node_a,
                    row.node_b,
                ),
            )
        ),
        epoch_us=1,
    )
    result: dict[tuple[str, str], float] = {}
    for left in sites:
        for right in sites:
            if left == right:
                result[left, right] = 1.0
                continue
            delivered = 0
            for start_us in window_starts:
                expires_us = min(cutoff_us, start_us + window_us)
                if path_index.earliest(left, right, start_us, expires_us) is not None:
                    delivered += 1
            result[left, right] = delivered / len(window_starts)
    return result


@dataclass(frozen=True)
class CausalCampaignConfig:
    cap: int
    policy: str
    gateway: str
    witnesses: tuple[str, ...]
    quorum_threshold: int
    quorum_availability: float = 0.75
    quorum_timeout_us: int = HOUR_US
    sync_period_us: int = 6 * HOUR_US
    buffer_bytes: int = 96 * 1024
    copy_budget: int = 8
    routing_policy: RoutingPolicy = RoutingPolicy.SPRAY_WAIT
    loss_probability: float = 0.0
    transfer_loss_probability: float = 0.0
    transfer_processing_delay_us: int = 0
    reclaim_delay_us: int = 2 * HOUR_US
    transfer_decision_ttl_us: int = 6 * HOUR_US
    transfer_witness_availability: float | None = None
    conservative_protection_fraction: float = 0.10
    reserve_fraction: float = 0.5
    adatransfer_gate_mode: str = "confidence"
    request_bytes: int = 2048
    quorum_request_bytes: int = 512
    quorum_share_bytes: int = 192
    transfer_bytes: int = 512
    periodic_sync_bytes: int = 640
    receipt_bytes: int = 1741
    background_bundles_per_hour: float = 0.0
    background_bundle_bytes: int = 8192
    background_ttl_us: int = 6 * HOUR_US
    oracle_slot_us: int = HOUR_US
    oracle_contact_timing: str = "relaxed_start"
    experiment_id: str = "bow-primary"

    def validate(self, sites: tuple[str, ...]) -> None:
        if self.policy not in ALL_CAUSAL_POLICIES:
            raise ValueError(f"unknown policy: {self.policy}")
        if self.cap <= 0 or self.buffer_bytes <= 0 or self.copy_budget <= 0:
            raise ValueError("cap, buffer and copy budget must be positive")
        RoutingPolicy(self.routing_policy)
        if not self.witnesses or not 1 <= self.quorum_threshold <= len(self.witnesses):
            raise ValueError("quorum threshold must be in [1, number of witnesses]")
        if not 0.0 <= self.quorum_availability <= 1.0:
            raise ValueError("quorum availability must be in [0, 1]")
        if not 0.0 <= self.loss_probability <= 1.0:
            raise ValueError("loss probability must be in [0, 1]")
        if not 0.0 <= self.transfer_loss_probability <= 1.0:
            raise ValueError("transfer loss probability must be in [0, 1]")
        if self.transfer_processing_delay_us < 0:
            raise ValueError("transfer processing delay must be nonnegative")
        if self.reclaim_delay_us <= 0 or self.transfer_decision_ttl_us <= 0:
            raise ValueError("reclaim delay and decision ttl must be positive")
        if (
            self.transfer_witness_availability is not None
            and not 0.0 <= self.transfer_witness_availability <= 1.0
        ):
            raise ValueError("transfer witness availability must be in [0, 1]")
        if not 0.0 < self.conservative_protection_fraction <= 1.0:
            raise ValueError("conservative protection fraction must be in (0, 1]")
        if self.background_bundles_per_hour < 0:
            raise ValueError("background bundle rate must be nonnegative")
        if self.background_bundle_bytes <= 0 or self.background_ttl_us <= 0:
            raise ValueError("background bundle size and ttl must be positive")
        if not 0.0 <= self.reserve_fraction <= 1.0:
            raise ValueError("reserve fraction must be in [0, 1]")
        if self.adatransfer_gate_mode not in {"confidence", "expected_value"}:
            raise ValueError("unknown AdaTransfer gate mode")
        if self.oracle_contact_timing not in {"relaxed_start", "event_respecting"}:
            raise ValueError("unknown oracle contact timing")
        if len(set(sites)) != len(sites) or not sites:
            raise ValueError("sites must be unique and nonempty")


@dataclass
class _RequestState:
    request: HeldoutRequest
    arrived_us: int
    digest: str
    shares: set[str] = field(default_factory=set)
    completed_us: int | None = None
    branch: str | None = None
    transfer_id: str | None = None


@dataclass
class _TransferState:
    transfer_id: str
    request_id: str
    donor: str
    destination: str
    created_us: int
    reclaim_after_us: int
    terminal: str | None = None
    shares: dict[str, set[str]] = field(default_factory=lambda: {"accept": set(), "reclaim": set()})


class _CausalAuthorizationController:
    def __init__(
        self,
        *,
        requests: tuple[HeldoutRequest, ...],
        sites: tuple[str, ...],
        config: CausalCampaignConfig,
        path_reliability: dict[tuple[str, str], float],
    ) -> None:
        config.validate(sites)
        self.requests = {request.bundle.bundle_id: request for request in requests}
        self.sites = sites
        self.config = config
        self.path_reliability = path_reliability
        self.initial_balances = _initial_for_policy(config.policy, sites, config.cap)
        self.balances = dict(self.initial_balances)
        self.delivered_capacity = dict(self.initial_balances)
        self.local_seen = {site: set() for site in sites}
        self.witness_seen: dict[tuple[str, str], str] = {}
        self.states: dict[str, _RequestState] = {}
        self.observed_demand: Counter[str] = Counter()
        self.accepted: list[tuple[str, str, str, int, str, bool]] = []
        self.transfer_started: dict[str, tuple[str, str, str]] = {}
        self.pull_requested: set[str] = set()
        self.pull_processed: set[str] = set()
        self.local_control_events: list[dict[str, Any]] = []
        self.transfer_delivered: set[str] = set()
        self.transfers: dict[str, _TransferState] = {}
        self.transfer_terminal_accepted: set[str] = set()
        self.transfer_reclaimed: set[str] = set()
        self.transfer_witness_decisions: dict[tuple[str, str], str] = {}
        self.pending_by_site: dict[str, set[str]] = {site: set() for site in sites}
        self.transfer_decisions = 0
        self.transfer_candidate_decisions = 0
        self.transfer_positive_margin_decisions = 0
        self.transfer_margin_min = math.inf
        self.transfer_margin_max = -math.inf
        self.transfer_best_mismatch_lcb = math.nan
        self.transfer_best_stranding_ucb = math.nan
        self.transfer_best_congestion_ucb = math.nan
        self.transfer_best_interference_ucb = math.nan
        self.receipts_created = 0
        self.receipts_delivered = 0
        self.settled_nullifiers: set[str] = set()
        self.settlement_conflicts = 0
        self.sync_remaining = config.cap
        self.sync_seen: set[str] = set()
        self.sync_attempts = 0
        self.sync_successes = 0
        self.adaptive = (
            AdaTransferAllocator(
                sites=len(sites),
                budget=config.cap,
                initial_target=tuple(float(self.initial_balances[site]) for site in sites),
                reserve_fraction=config.reserve_fraction,
                gate_mode=config.adatransfer_gate_mode,
            )
            if config.policy in {"adatransfer", "reclaimable_transfer", "reclaimable_transfer_v2"}
            else None
        )
        self.conservative = (
            ConservativeRegimeController(
                baseline=tuple(self.initial_balances[site] for site in sites),
                protection_fraction=config.conservative_protection_fraction,
            )
            if config.policy in CONSERVATIVE_POLICIES
            else None
        )

    def _payload(self, **values: object) -> tuple[tuple[str, str], ...]:
        return tuple(sorted((key, str(value)) for key, value in values.items()))

    def _request_deadline(self, request_id: str) -> int:
        return self.requests[request_id].bundle.expires_us

    def _accept(self, request_id: str, now_us: int, branch: str) -> tuple[SharedBundleSpec, ...]:
        state = self.states[request_id]
        request = state.request
        site = request.bundle.destination
        if state.completed_us is not None or now_us > request.bundle.expires_us:
            return ()
        if request.nullifier in self.local_seen[site]:
            return ()
        self.local_seen[site].add(request.nullifier)
        self.pending_by_site[site].discard(request_id)
        state.completed_us = now_us
        state.branch = branch
        self.accepted.append(
            (
                request_id,
                request.nullifier,
                site,
                now_us,
                branch,
                site != request.home_site,
            )
        )
        self.receipts_created += 1
        return (
            SharedBundleSpec(
                bundle_id=f"receipt:{request_id}:{branch}",
                message_class=MessageClass.RECEIPT,
                source=site,
                destination=self.config.gateway,
                created_us=now_us,
                ttl_us=max(1, request.bundle.expires_us - now_us),
                size_bytes=self.config.receipt_bytes,
                risk_score=1.0,
                correlation_id=request_id,
                payload=self._payload(
                    request_id=request_id,
                    nullifier=request.nullifier,
                    site=site,
                    branch=branch,
                    digest=state.digest,
                ),
            ),
        )

    def _transfer_donor(
        self, site: str, snapshot: SharedNetworkSnapshot
    ) -> tuple[str | None, dict[str, float]]:
        candidates = [donor for donor in self.sites if donor != site and self.balances[donor] > 0]
        diagnostics = {
            "margin": 0.0,
            "stranding_ucb": 0.0,
            "congestion_ucb": 0.0,
            "interference_ucb": 0.0,
            "drift_score": 0.0,
            "drift_threshold": 0.0,
        }
        if not candidates:
            return None, diagnostics
        if self.config.policy == "myopic_transfer":
            return min(candidates, key=lambda donor: (-self.balances[donor], donor)), diagnostics
        if self.config.policy == "rolling_relocation":
            smoothing = tuple(self.observed_demand[member] + 1 for member in self.sites)
            target = _hamilton(self.sites, self.config.cap, smoothing)
            donors = [donor for donor in candidates if self.balances[donor] > target[donor]]
            if not donors:
                return None, diagnostics
            return min(
                donors,
                key=lambda donor: (-(self.balances[donor] - target[donor]), donor),
            ), diagnostics
        network_scale = max(1, self.config.buffer_bytes * (len(self.sites) + 1))
        queue_pressure = min(1.0, snapshot.buffered_bytes / network_scale)
        safe_bytes = sum(
            snapshot.buffered_bytes_by_class.get(message_class.value, 0)
            for message_class in (MessageClass.QUORUM_REQUEST, MessageClass.QUORUM_SHARE)
        )
        safe_pressure = min(1.0, safe_bytes / network_scale)
        if self.config.policy in CONSERVATIVE_POLICIES and self.conservative is not None:
            target_index = self.sites.index(site)
            reliability = tuple(
                self.path_reliability.get((donor, site), 0.0) for donor in self.sites
            )
            open_locks = sum(transfer.terminal is None for transfer in self.transfers.values())
            request = min(
                (
                    self.states[request_id].request
                    for request_id in self.pending_by_site[site]
                    if self.states[request_id].completed_us is None
                ),
                key=lambda row: (row.bundle.expires_us, row.bundle.bundle_id),
                default=None,
            )
            remaining_fraction = 1.0
            if request is not None:
                remaining = max(1, request.bundle.expires_us - snapshot.now_us)
                remaining_fraction = min(1.0, self.config.reclaim_delay_us / remaining)
            decision = self.conservative.decide(
                target_index=target_index,
                balances=tuple(self.balances[member] for member in self.sites),
                observed_demand=tuple(self.observed_demand[member] for member in self.sites),
                path_reliability=reliability,
                queue_pressure=queue_pressure,
                safe_path_pressure=safe_pressure,
                open_locks=open_locks,
                reclaimable=self.config.policy in RECLAIMABLE_POLICIES,
                reclaim_delay_fraction=remaining_fraction,
            )
            self.transfer_decisions += 1
            if decision.donor_index is not None:
                self.transfer_candidate_decisions += 1
            if decision.margin > 0:
                self.transfer_positive_margin_decisions += 1
            self.transfer_margin_min = min(self.transfer_margin_min, decision.margin)
            if decision.margin > self.transfer_margin_max:
                self.transfer_margin_max = decision.margin
                self.transfer_best_mismatch_lcb = decision.target_excess
                self.transfer_best_stranding_ucb = decision.lock_cost
                self.transfer_best_congestion_ucb = decision.pressure_cost
                self.transfer_best_interference_ucb = 0.0
            diagnostics = {
                "margin": decision.margin,
                "stranding_ucb": decision.lock_cost,
                "congestion_ucb": decision.pressure_cost,
                "interference_ucb": 0.0,
                "drift_score": decision.drift_score,
                "drift_threshold": decision.drift_threshold,
            }
            donor = self.sites[decision.donor_index] if decision.approved else None
            return donor, diagnostics
        if self.adaptive is None:
            return None, diagnostics
        self.adaptive.update_target(tuple(self.observed_demand[member] for member in self.sites))
        target_index = self.sites.index(site)
        reliability = tuple(self.path_reliability.get((donor, site), 0.0) for donor in self.sites)
        decision = self.adaptive.decide(
            target_index=target_index,
            balances=tuple(self.balances[member] for member in self.sites),
            observed_samples=sum(self.observed_demand.values()),
            path_reliability=reliability,
            queue_pressure=queue_pressure,
            safe_path_pressure=safe_pressure,
        )
        self.transfer_decisions += 1
        if decision.donor_index is not None:
            self.transfer_candidate_decisions += 1
        if decision.margin > 0:
            self.transfer_positive_margin_decisions += 1
        self.transfer_margin_min = min(self.transfer_margin_min, decision.margin)
        if decision.margin > self.transfer_margin_max:
            self.transfer_margin_max = decision.margin
            self.transfer_best_mismatch_lcb = decision.mismatch_reduction_lcb
            self.transfer_best_stranding_ucb = decision.stranding_ucb
            self.transfer_best_congestion_ucb = decision.congestion_ucb
            self.transfer_best_interference_ucb = decision.safe_path_interference_ucb
        diagnostics = {
            "margin": decision.margin,
            "stranding_ucb": decision.stranding_ucb,
            "congestion_ucb": decision.congestion_ucb,
            "interference_ucb": decision.safe_path_interference_ucb,
            "drift_score": 0.0,
            "drift_threshold": 0.0,
        }
        donor = self.sites[decision.donor_index] if decision.approved else None
        return donor, diagnostics

    def _fallback_or_transfer(
        self,
        request_id: str,
        now_us: int,
        snapshot: SharedNetworkSnapshot,
    ) -> tuple[SharedBundleSpec, ...]:
        state = self.states[request_id]
        request = state.request
        site = request.bundle.destination
        if state.completed_us is not None or request.nullifier in self.local_seen[site]:
            return ()
        if self.config.policy in {"quorum_only", "periodic_sync"}:
            if self.config.policy == "periodic_sync":
                self.sync_attempts += 1
                emitted_us = (
                    math.ceil(now_us / self.config.sync_period_us) * self.config.sync_period_us
                )
                if emitted_us > request.bundle.expires_us:
                    return ()
                return (
                    SharedBundleSpec(
                        bundle_id=f"sync-request:{request_id}",
                        message_class=MessageClass.PERIODIC_SYNC,
                        source=site,
                        destination=self.config.gateway,
                        created_us=emitted_us,
                        ttl_us=max(1, request.bundle.expires_us - emitted_us),
                        size_bytes=self.config.periodic_sync_bytes,
                        risk_score=1.0,
                        correlation_id=request_id,
                        payload=self._payload(phase="request", request_id=request_id),
                    ),
                )
            return ()
        if self.balances[site] > 0:
            self.balances[site] -= 1
            return self._accept(request_id, now_us, "right")
        self.pending_by_site[site].add(request_id)
        if self.config.policy in LOCAL_PULL_POLICIES:
            if request_id in self.pull_requested:
                return ()
            # Static membership, public initial issuance and prefix reliability are
            # the only remote information available to this requester.
            donors = [
                member
                for member in self.sites
                if member != site and self.initial_balances[member] > 0
            ]
            if not donors:
                return ()
            donor = min(
                donors,
                key=lambda member: (
                    -self.path_reliability.get((site, member), 0.0)
                    * self.path_reliability.get((member, site), 0.0),
                    -self.initial_balances[member],
                    member,
                ),
            )
            self.pull_requested.add(request_id)
            self.local_control_events.append(
                {
                    "event": "pull_sent",
                    "actor": site,
                    "peer": donor,
                    "time_us": now_us,
                    "request": request_id,
                }
            )
            return (
                SharedBundleSpec(
                    bundle_id=f"pull:{request_id}:{donor}",
                    message_class=MessageClass.RIGHT_PULL,
                    source=site,
                    destination=donor,
                    created_us=now_us,
                    ttl_us=max(1, request.bundle.expires_us - now_us),
                    size_bytes=self.config.quorum_request_bytes,
                    risk_score=1.0,
                    correlation_id=request_id,
                    payload=self._payload(
                        request_id=request_id,
                        site=site,
                        donor=donor,
                        deadline=request.bundle.expires_us,
                    ),
                ),
            )
        if self.config.policy not in TRANSFER_POLICIES:
            return ()
        donor, diagnostics = self._transfer_donor(site, snapshot)
        if donor is None:
            return ()
        transfer_created_us = now_us + self.config.transfer_processing_delay_us
        if transfer_created_us > request.bundle.expires_us:
            return ()
        self.balances[donor] -= 1  # fail-closed debit before emission
        self.delivered_capacity[donor] -= 1
        transfer_id = f"transfer:{request_id}:{donor}:{site}"
        state.transfer_id = transfer_id
        self.transfer_started[transfer_id] = (donor, site, request_id)
        emitted = [
            SharedBundleSpec(
                bundle_id=transfer_id,
                message_class=MessageClass.RIGHT_TRANSFER,
                source=donor,
                destination=site,
                created_us=transfer_created_us,
                ttl_us=max(1, request.bundle.expires_us - transfer_created_us),
                size_bytes=self.config.transfer_bytes,
                risk_score=1.0,
                correlation_id=request_id,
                payload=self._payload(
                    request_id=request_id,
                    donor=donor,
                    site=site,
                    margin=diagnostics["margin"],
                    stranding_ucb=diagnostics["stranding_ucb"],
                    congestion_ucb=diagnostics["congestion_ucb"],
                    interference_ucb=diagnostics["interference_ucb"],
                    drift_score=diagnostics["drift_score"],
                    drift_threshold=diagnostics["drift_threshold"],
                ),
            )
        ]
        if self.config.policy in RECLAIMABLE_POLICIES:
            reclaim_after_us = min(
                request.bundle.expires_us,
                transfer_created_us + self.config.reclaim_delay_us,
            )
            self.transfers[transfer_id] = _TransferState(
                transfer_id=transfer_id,
                request_id=request_id,
                donor=donor,
                destination=site,
                created_us=transfer_created_us,
                reclaim_after_us=reclaim_after_us,
            )
            emitted.append(
                SharedBundleSpec(
                    bundle_id=f"reclaim-timer:{transfer_id}",
                    message_class=MessageClass.LOCAL_TIMER,
                    source=donor,
                    destination=donor,
                    created_us=reclaim_after_us,
                    ttl_us=1,
                    size_bytes=1,
                    correlation_id=request_id,
                    payload=self._payload(kind="reclaim", transfer_id=transfer_id),
                )
            )
        return tuple(emitted)

    def _transfer_decision_requests(
        self,
        transfer: _TransferState,
        *,
        decision: str,
        requester: str,
        now_us: int,
    ) -> tuple[SharedBundleSpec, ...]:
        if decision not in {"accept", "reclaim"} or transfer.terminal is not None:
            return ()
        return tuple(
            SharedBundleSpec(
                bundle_id=(f"right-decision-request:{transfer.transfer_id}:{decision}:{witness}"),
                message_class=MessageClass.RIGHT_DECISION_REQUEST,
                source=requester,
                destination=witness,
                created_us=now_us,
                ttl_us=self.config.transfer_decision_ttl_us,
                size_bytes=self.config.quorum_request_bytes,
                risk_score=1.0,
                correlation_id=transfer.request_id,
                payload=self._payload(
                    transfer_id=transfer.transfer_id,
                    decision=decision,
                    witness=witness,
                    requester=requester,
                    donor=transfer.donor,
                    destination=transfer.destination,
                ),
            )
            for witness in self.config.witnesses
        )

    def _consume_pending_right(
        self,
        site: str,
        now_us: int,
        *,
        branch: str,
        preferred_request_id: str | None = None,
    ) -> tuple[SharedBundleSpec, ...]:
        if self.balances[site] <= 0:
            return ()
        candidates = [
            request_id
            for request_id in self.pending_by_site[site]
            if self.states[request_id].completed_us is None
            and now_us <= self.states[request_id].request.bundle.expires_us
        ]
        if not candidates:
            return ()
        request_id = min(
            candidates,
            key=lambda candidate: (
                candidate != preferred_request_id,
                self.states[candidate].request.bundle.expires_us,
                candidate,
            ),
        )
        self.balances[site] -= 1
        return self._accept(request_id, now_us, branch)

    def _finalize_transfer(
        self,
        transfer: _TransferState,
        *,
        decision: str,
        now_us: int,
    ) -> tuple[SharedBundleSpec, ...]:
        if transfer.terminal is not None:
            return ()
        transfer.terminal = decision
        if decision == "accept":
            self.transfer_terminal_accepted.add(transfer.transfer_id)
            self.delivered_capacity[transfer.destination] += 1
            self.balances[transfer.destination] += 1
            return self._consume_pending_right(
                transfer.destination,
                now_us,
                branch="certified_transferred_right",
                preferred_request_id=transfer.request_id,
            )
        if decision != "reclaim":
            raise ValueError(f"unknown transfer terminal decision: {decision}")
        self.transfer_reclaimed.add(transfer.transfer_id)
        self.delivered_capacity[transfer.donor] += 1
        self.balances[transfer.donor] += 1
        return self._consume_pending_right(
            transfer.donor,
            now_us,
            branch="certified_reclaimed_right",
        )

    def on_delivery(
        self,
        record: SharedDeliveryRecord,
        bundle: SharedBundleSpec,
        snapshot: SharedNetworkSnapshot,
    ) -> tuple[SharedBundleSpec, ...]:
        payload = bundle.payload_dict()
        if bundle.message_class == MessageClass.REQUEST:
            request = self.requests[bundle.bundle_id]
            digest = stable_hash(
                {
                    "request": bundle.bundle_id,
                    "nullifier": request.nullifier,
                    "site": bundle.destination,
                    "value": 1,
                },
                domain="aidmesh/semantic-authorization/v1",
            )
            self.states[bundle.bundle_id] = _RequestState(request, record.delivered_us, digest)
            self.observed_demand[bundle.destination] += 1
            emitted: list[SharedBundleSpec] = []
            for witness in self.config.witnesses:
                emitted.append(
                    SharedBundleSpec(
                        bundle_id=f"quorum-request:{bundle.bundle_id}:{witness}",
                        message_class=MessageClass.QUORUM_REQUEST,
                        source=bundle.destination,
                        destination=witness,
                        created_us=record.delivered_us,
                        ttl_us=max(1, bundle.expires_us - record.delivered_us),
                        size_bytes=self.config.quorum_request_bytes,
                        risk_score=1.0,
                        correlation_id=bundle.bundle_id,
                        payload=self._payload(
                            request_id=bundle.bundle_id,
                            witness=witness,
                            nullifier=request.nullifier,
                            digest=digest,
                        ),
                    )
                )
            timeout_us = min(bundle.expires_us, record.delivered_us + self.config.quorum_timeout_us)
            emitted.append(
                SharedBundleSpec(
                    bundle_id=f"timer:{bundle.bundle_id}",
                    message_class=MessageClass.LOCAL_TIMER,
                    source=bundle.destination,
                    destination=bundle.destination,
                    created_us=timeout_us,
                    ttl_us=1,
                    size_bytes=1,
                    correlation_id=bundle.bundle_id,
                    payload=self._payload(request_id=bundle.bundle_id),
                )
            )
            return tuple(emitted)

        if bundle.message_class == MessageClass.QUORUM_REQUEST:
            request_id = payload["request_id"]
            witness = payload["witness"]
            state = self.states.get(request_id)
            if state is None:
                return ()
            available = (
                _stable_unit(
                    {
                        "experiment": self.config.experiment_id,
                        "request": request_id,
                        "witness": witness,
                    },
                    domain="aidmesh/shared-quorum/availability/v1",
                )
                < self.config.quorum_availability
            )
            key = (witness, state.request.nullifier)
            persisted = self.witness_seen.get(key)
            if not available or (persisted is not None and persisted != state.digest):
                return ()
            self.witness_seen[key] = state.digest
            return (
                SharedBundleSpec(
                    bundle_id=f"quorum-share:{request_id}:{witness}",
                    message_class=MessageClass.QUORUM_SHARE,
                    source=witness,
                    destination=state.request.bundle.destination,
                    created_us=record.delivered_us,
                    ttl_us=max(1, state.request.bundle.expires_us - record.delivered_us),
                    size_bytes=self.config.quorum_share_bytes,
                    risk_score=1.0,
                    correlation_id=request_id,
                    payload=self._payload(
                        request_id=request_id,
                        witness=witness,
                        digest=state.digest,
                    ),
                ),
            )

        if bundle.message_class == MessageClass.QUORUM_SHARE:
            request_id = payload["request_id"]
            state = self.states.get(request_id)
            if state is None or payload.get("digest") != state.digest:
                return ()
            state.shares.add(payload["witness"])
            if len(state.shares) >= self.config.quorum_threshold:
                return self._accept(request_id, record.delivered_us, "quorum")
            return ()

        if bundle.message_class == MessageClass.LOCAL_TIMER:
            if payload.get("kind") == "reclaim":
                transfer = self.transfers.get(payload["transfer_id"])
                if transfer is None or transfer.terminal is not None:
                    return ()
                return self._transfer_decision_requests(
                    transfer,
                    decision="reclaim",
                    requester=transfer.donor,
                    now_us=record.delivered_us,
                )
            request_id = payload["request_id"]
            if request_id not in self.states:
                return ()
            return self._fallback_or_transfer(request_id, record.delivered_us, snapshot)

        if bundle.message_class == MessageClass.RIGHT_PULL:
            # Only delivery to the donor enables its local debit and emission.
            # Do not inspect the requester's current state, demand, or queues.
            if (
                self.config.policy not in LOCAL_PULL_POLICIES
                or bundle.bundle_id in self.pull_processed
            ):
                return ()
            self.pull_processed.add(bundle.bundle_id)
            donor, site = payload["donor"], payload["site"]
            request_id = payload["request_id"]
            now_us, deadline = record.delivered_us, int(payload["deadline"])
            self.local_control_events.append(
                {
                    "event": "pull_received",
                    "actor": donor,
                    "peer": site,
                    "time_us": now_us,
                    "request": request_id,
                }
            )
            reserve = math.floor(self.initial_balances[donor] * self.config.reserve_fraction)
            if now_us >= deadline or self.balances[donor] <= reserve:
                return ()
            self.balances[donor] -= 1
            self.delivered_capacity[donor] -= 1
            transfer_id = f"transfer:{request_id}:{donor}:{site}"
            self.transfer_started[transfer_id] = (donor, site, request_id)
            self.local_control_events.append(
                {
                    "event": "donor_debit",
                    "actor": donor,
                    "peer": site,
                    "time_us": now_us,
                    "request": request_id,
                }
            )
            if self.config.policy == "local_pull_certified":
                self.transfers[transfer_id] = _TransferState(
                    transfer_id, request_id, donor, site, now_us, deadline
                )
            return (
                SharedBundleSpec(
                    bundle_id=transfer_id,
                    message_class=MessageClass.RIGHT_TRANSFER,
                    source=donor,
                    destination=site,
                    created_us=now_us,
                    ttl_us=max(1, deadline - now_us),
                    size_bytes=self.config.transfer_bytes,
                    risk_score=1.0,
                    correlation_id=request_id,
                    payload=self._payload(request_id=request_id, site=site, donor=donor),
                ),
            )

        if bundle.message_class == MessageClass.RIGHT_TRANSFER:
            request_id = payload["request_id"]
            site = payload["site"]
            self.transfer_delivered.add(bundle.bundle_id)
            if self.config.policy in RECLAIMABLE_POLICIES:
                transfer = self.transfers.get(bundle.bundle_id)
                if transfer is None or transfer.terminal is not None:
                    return ()
                return self._transfer_decision_requests(
                    transfer,
                    decision="accept",
                    requester=site,
                    now_us=record.delivered_us,
                )
            self.delivered_capacity[site] += 1
            self.balances[site] += 1
            state = self.states.get(request_id)
            if state is None or state.completed_us is not None:
                return ()
            if record.delivered_us > state.request.bundle.expires_us:
                return ()
            self.balances[site] -= 1
            return self._accept(request_id, record.delivered_us, "transferred_right")

        if bundle.message_class == MessageClass.RIGHT_DECISION_REQUEST:
            transfer_id = payload["transfer_id"]
            decision = payload["decision"]
            witness = payload["witness"]
            transfer = self.transfers.get(transfer_id)
            if transfer is None or transfer.terminal is not None:
                return ()
            if self.config.policy in RECOVERABLE_VOTE_POLICIES:
                if decision == "accept" and record.delivered_us > transfer.reclaim_after_us:
                    return ()
                if decision == "reclaim" and record.delivered_us < transfer.reclaim_after_us:
                    return ()
            availability = (
                self.config.transfer_witness_availability
                if self.config.transfer_witness_availability is not None
                else self.config.quorum_availability
            )
            available = (
                _stable_unit(
                    {
                        "experiment": self.config.experiment_id,
                        "transfer": transfer_id,
                        "decision": decision,
                        "witness": witness,
                    },
                    domain="aidmesh/reclaimable-transfer/witness-availability/v1",
                )
                < availability
            )
            key = (witness, transfer_id)
            persisted = self.transfer_witness_decisions.get(key)
            recoverable_switch = (
                self.config.policy in RECOVERABLE_VOTE_POLICIES
                and persisted == "accept"
                and decision == "reclaim"
                and record.delivered_us >= transfer.reclaim_after_us
            )
            if not available or (
                persisted is not None and persisted != decision and not recoverable_switch
            ):
                return ()
            if recoverable_switch:
                # An uncommitted ACCEPT share expires at the registered recovery
                # boundary.  A committed q-share ACCEPT would already have made
                # the transfer terminal, so reaching this branch cannot overwrite
                # an accepted transfer in the simulator's linearizable register.
                transfer.shares["accept"].discard(witness)
            self.transfer_witness_decisions[key] = decision
            return (
                SharedBundleSpec(
                    bundle_id=f"right-decision-share:{transfer_id}:{decision}:{witness}",
                    message_class=MessageClass.RIGHT_DECISION_SHARE,
                    source=witness,
                    destination=payload["requester"],
                    created_us=record.delivered_us,
                    ttl_us=self.config.transfer_decision_ttl_us,
                    size_bytes=self.config.quorum_share_bytes,
                    risk_score=1.0,
                    correlation_id=transfer.request_id,
                    payload=self._payload(
                        transfer_id=transfer_id,
                        decision=decision,
                        witness=witness,
                    ),
                ),
            )

        if bundle.message_class == MessageClass.RIGHT_DECISION_SHARE:
            transfer_id = payload["transfer_id"]
            decision = payload["decision"]
            witness = payload["witness"]
            transfer = self.transfers.get(transfer_id)
            if transfer is None or transfer.terminal is not None:
                return ()
            if self.transfer_witness_decisions.get((witness, transfer_id)) != decision:
                return ()
            transfer.shares[decision].add(witness)
            if len(transfer.shares[decision]) < self.config.quorum_threshold:
                return ()
            return self._finalize_transfer(
                transfer,
                decision=decision,
                now_us=record.delivered_us,
            )

        if bundle.message_class == MessageClass.PERIODIC_SYNC:
            request_id = payload["request_id"]
            state = self.states.get(request_id)
            if state is None:
                return ()
            if payload.get("phase") == "request":
                nullifier = state.request.nullifier
                granted = nullifier not in self.sync_seen and self.sync_remaining > 0
                if granted:
                    self.sync_seen.add(nullifier)
                    self.sync_remaining -= 1
                return (
                    SharedBundleSpec(
                        bundle_id=f"sync-response:{request_id}",
                        message_class=MessageClass.PERIODIC_SYNC,
                        source=self.config.gateway,
                        destination=state.request.bundle.destination,
                        created_us=record.delivered_us,
                        ttl_us=max(1, state.request.bundle.expires_us - record.delivered_us),
                        size_bytes=self.config.periodic_sync_bytes,
                        risk_score=1.0,
                        correlation_id=request_id,
                        payload=self._payload(
                            phase="response", request_id=request_id, granted=int(granted)
                        ),
                    ),
                )
            if payload.get("granted") == "1":
                self.sync_successes += 1
                return self._accept(request_id, record.delivered_us, "periodic_sync")
            return ()

        if bundle.message_class == MessageClass.RECEIPT:
            self.receipts_delivered += 1
            nullifier = payload["nullifier"]
            if nullifier in self.settled_nullifiers:
                self.settlement_conflicts += 1
            else:
                self.settled_nullifiers.add(nullifier)
            return ()
        return ()


def _shared_requests(
    requests: tuple[HeldoutRequest, ...], *, request_bytes: int
) -> tuple[SharedBundleSpec, ...]:
    return tuple(
        SharedBundleSpec(
            bundle_id=request.bundle.bundle_id,
            message_class=MessageClass.REQUEST,
            source=request.bundle.source,
            destination=request.bundle.destination,
            created_us=request.bundle.created_us,
            ttl_us=request.bundle.ttl_us,
            size_bytes=request_bytes,
            risk_score=request.bundle.risk_score,
            correlation_id=request.bundle.bundle_id,
        )
        for request in requests
    )


def _background_bundles(
    contacts: tuple[Contact, ...],
    *,
    config: CausalCampaignConfig,
) -> tuple[SharedBundleSpec, ...]:
    """Generate deterministic cross-class recovery traffic for contention tests."""

    if not contacts or config.background_bundles_per_hour <= 0:
        return ()
    nodes = tuple(sorted({node for row in contacts for node in (row.node_a, row.node_b)}))
    if len(nodes) < 2:
        return ()
    start_us = min(row.start_us for row in contacts)
    end_us = max(row.end_us for row in contacts)
    duration_hours = max(1 / 3600, (end_us - start_us) / HOUR_US)
    count = max(1, round(config.background_bundles_per_hour * duration_hours))
    bundles = []
    for ordinal in range(count):
        identity = {"experiment": config.experiment_id, "ordinal": ordinal}
        created_fraction = _stable_unit(
            identity,
            domain="aidmesh/shared-background/created/v1",
        )
        source_fraction = _stable_unit(
            identity,
            domain="aidmesh/shared-background/source/v1",
        )
        destination_fraction = _stable_unit(
            identity,
            domain="aidmesh/shared-background/destination/v1",
        )
        created_us = start_us + round(created_fraction * max(0, end_us - start_us - 1))
        source_index = min(len(nodes) - 1, int(source_fraction * len(nodes)))
        destination_index = min(len(nodes) - 2, int(destination_fraction * (len(nodes) - 1)))
        if destination_index >= source_index:
            destination_index += 1
        bundles.append(
            SharedBundleSpec(
                bundle_id=f"background:{config.experiment_id}:{ordinal}",
                message_class=MessageClass.RECOVERY,
                source=nodes[source_index],
                destination=nodes[destination_index],
                created_us=created_us,
                ttl_us=min(config.background_ttl_us, max(1, end_us - created_us)),
                size_bytes=config.background_bundle_bytes,
                risk_score=0.0,
                correlation_id=f"background:{ordinal}",
            )
        )
    return tuple(sorted(bundles, key=lambda row: (row.created_us, row.bundle_id)))


def evaluate_causal_policy(
    *,
    contacts: tuple[Contact, ...],
    requests: tuple[HeldoutRequest, ...],
    sites: tuple[str, ...],
    config: CausalCampaignConfig,
    path_reliability: dict[tuple[str, str], float] | None = None,
    compute_oracle: bool = True,
) -> dict[str, Any]:
    """Evaluate one online policy and its transport-conditioned flow oracle."""

    config.validate(sites)
    reliability = path_reliability or {(left, right): 1.0 for left in sites for right in sites}
    controller = _CausalAuthorizationController(
        requests=requests,
        sites=sites,
        config=config,
        path_reliability=reliability,
    )
    network = SharedTemporalContactSimulator(
        contacts=contacts,
        bundles=(
            *_shared_requests(requests, request_bytes=config.request_bytes),
            *_background_bundles(contacts, config=config),
        ),
        buffer_bytes=config.buffer_bytes,
        copy_budget=config.copy_budget,
        routing_policy=config.routing_policy,
        loss_probability=config.loss_probability,
        class_loss_probability={MessageClass.RIGHT_TRANSFER: config.transfer_loss_probability},
        on_delivery=controller.on_delivery,
    ).run()

    accepted_by_nullifier = Counter(row[1] for row in controller.accepted)
    duplicate_exposure = sum(max(0, count - 1) for count in accepted_by_nullifier.values())
    safe_completed = sum(row[4] == "quorum" for row in controller.accepted)
    sync_completed = sum(row[4] == "periodic_sync" for row in controller.accepted)
    fallback_completed = len(controller.accepted) - safe_completed - sync_completed
    transfer_stranded = (
        sum(transfer.terminal is None for transfer in controller.transfers.values())
        if config.policy in RECLAIMABLE_POLICIES
        else len(controller.transfer_started) - len(controller.transfer_delivered)
    )
    arrived_request_ids = set(controller.states)
    safe_request_ids = {row[0] for row in controller.accepted if row[4] == "quorum"}
    unique_demand: dict[tuple[str, str], TemporalDemand] = {}
    for request_id in sorted(arrived_request_ids):
        state = controller.states[request_id]
        if request_id in safe_request_ids:
            continue
        key = (state.request.bundle.destination, state.request.nullifier)
        candidate = TemporalDemand(
            demand_id=request_id,
            site=state.request.bundle.destination,
            release_us=state.arrived_us,
            deadline_us=state.request.bundle.expires_us,
        )
        previous = unique_demand.get(key)
        if previous is None or (candidate.release_us, candidate.demand_id) < (
            previous.release_us,
            previous.demand_id,
        ):
            unique_demand[key] = candidate
    oracle = None
    oracle_total = None
    if compute_oracle:
        residual_units = aggregate_contact_residual_units(
            network,
            transfer_size_bytes=config.transfer_bytes,
            replace_class=MessageClass.RIGHT_TRANSFER,
        )
        oracle = solve_time_expanded_oracle(
            initial_rights=_initial_balances(sites, config.cap),
            demands=tuple(unique_demand.values()),
            contacts=contacts,
            slot_us=config.oracle_slot_us,
            transfer_size_bytes=config.transfer_bytes,
            contact_residual_units=residual_units,
            contact_timing=config.oracle_contact_timing,
        )
        oracle_total = safe_completed + oracle.completed_value

    demand_by_site: dict[str, set[str]] = {site: set() for site in sites}
    for state in controller.states.values():
        if state.branch == "quorum":
            continue
        demand_by_site[state.request.bundle.destination].add(state.request.nullifier)
    demand_vector = [len(demand_by_site[site]) for site in sites]
    delivered_vector = [controller.delivered_capacity[site] for site in sites]
    collapsed_prediction = (
        completion_from_identity(delivered_vector, demand_vector)
        if config.policy in RIGHTS_POLICIES
        else None
    )
    temporal_gap = (
        collapsed_prediction - fallback_completed if collapsed_prediction is not None else None
    )
    issued_conservation = (
        sum(controller.balances.values()) + fallback_completed + transfer_stranded
        if config.policy in RIGHTS_POLICIES
        else None
    )
    completion_latencies = [
        completed_us - controller.requests[request_id].bundle.created_us
        for request_id, _, _, completed_us, _, _ in controller.accepted
    ]
    restricted_latencies = []
    completed_lookup = {row[0]: row[3] for row in controller.accepted}
    for request in requests:
        deadline = request.bundle.expires_us
        event_us = completed_lookup.get(request.bundle.bundle_id, deadline)
        restricted_latencies.append(event_us - request.bundle.created_us)

    return {
        "policy": config.policy,
        "terminal_register_revision": (
            "recoverable-expiring-prepare-v2"
            if config.policy in RECOVERABLE_VOTE_POLICIES
            else "first-prepare-v1"
        ),
        "generated_requests": len(requests),
        "network_arrived_requests": len(arrived_request_ids),
        "transport_conditioning_gap": transport_conditioning_gap(
            generated_demand=len(requests),
            arrived_demand=len(arrived_request_ids),
            conditional_completion=len(controller.accepted),
        ),
        "completed": len(controller.accepted),
        "completion_rate": len(controller.accepted) / max(1, len(requests)),
        "unique_completed": len(accepted_by_nullifier),
        "unique_completion_per_generated": len(accepted_by_nullifier) / max(1, len(requests)),
        "unique_entitlement_coverage": len(accepted_by_nullifier)
        / max(1, len({request.nullifier for request in requests})),
        "settled_per_generated": len(controller.settled_nullifiers) / max(1, len(requests)),
        "information_profile": "local-delivery-gated-pull"
        if config.policy in LOCAL_PULL_POLICIES
        else "historical-controller",
        "local_control_events": controller.local_control_events,
        "conditional_completion_rate": len(controller.accepted) / max(1, len(arrived_request_ids)),
        "quorum_completed": safe_completed,
        "fallback_completed": fallback_completed,
        "periodic_sync_completed": sync_completed,
        "non_home_completed": sum(row[5] for row in controller.accepted),
        "duplicate_exposure": duplicate_exposure,
        "exposure_per_1000_requests": 1000 * duplicate_exposure / max(1, len(requests)),
        "honest_site_risk_cap": config.cap,
        "risk_cap_violation": max(0, fallback_completed - config.cap),
        "rights_conservation_value": issued_conservation,
        "rights_conservation_holds": (
            issued_conservation == config.cap if issued_conservation is not None else None
        ),
        "transfers_started": len(controller.transfer_started),
        "transfers_delivered": len(controller.transfer_delivered),
        "transfers_accepted": (
            len(controller.transfer_terminal_accepted)
            if config.policy in RECLAIMABLE_POLICIES
            else len(controller.transfer_delivered)
        ),
        "transfers_reclaimed": len(controller.transfer_reclaimed),
        "transfers_locked_final": transfer_stranded,
        "transfers_stranded": transfer_stranded,
        "transfer_decisions": controller.transfer_decisions,
        "transfer_candidate_decisions": controller.transfer_candidate_decisions,
        "transfer_positive_margin_decisions": (controller.transfer_positive_margin_decisions),
        "transfer_margin_min": (
            controller.transfer_margin_min if controller.transfer_decisions else None
        ),
        "transfer_margin_max": (
            controller.transfer_margin_max if controller.transfer_decisions else None
        ),
        "transfer_best_mismatch_lcb": controller.transfer_best_mismatch_lcb,
        "transfer_best_stranding_ucb": controller.transfer_best_stranding_ucb,
        "transfer_best_congestion_ucb": controller.transfer_best_congestion_ucb,
        "transfer_best_interference_ucb": controller.transfer_best_interference_ucb,
        "sync_attempts": controller.sync_attempts,
        "sync_successes": controller.sync_successes,
        "receipts_created": controller.receipts_created,
        "receipts_delivered": controller.receipts_delivered,
        "settlements_accepted": len(controller.settled_nullifiers),
        "settlement_conflicts": controller.settlement_conflicts,
        "end_to_end_settlement_probability": (
            len(controller.settled_nullifiers) / max(1, len(controller.accepted))
        ),
        "mean_success_latency_hours": (
            sum(completion_latencies) / len(completion_latencies) / HOUR_US
            if completion_latencies
            else None
        ),
        "restricted_mean_latency_hours": (
            sum(restricted_latencies) / max(1, len(restricted_latencies)) / HOUR_US
        ),
        "forwarded_bytes": network.forwarded_bytes,
        "peak_buffer_bytes": network.peak_buffer_bytes,
        "peak_buffer_bundles": network.peak_buffer_bundles,
        "dropped_messages": network.dropped,
        "created_by_class": dict(network.created_by_class),
        "delivered_by_class": dict(network.delivered_by_class),
        "dropped_by_class": dict(network.dropped_by_class),
        "forwarded_bytes_by_class": dict(network.forwarded_bytes_by_class),
        "background_bundles_created": network.created_by_class.get(MessageClass.RECOVERY.value, 0),
        "background_bundles_delivered": network.delivered_by_class.get(
            MessageClass.RECOVERY.value, 0
        ),
        "network_event_checksum": network.event_checksum,
        "initial_placement_l1": (
            l1_misplacement([controller.initial_balances[site] for site in sites], demand_vector)
            if config.policy in RIGHTS_POLICIES
            else None
        ),
        "delivered_placement_l1": (
            l1_misplacement(delivered_vector, demand_vector)
            if config.policy in RIGHTS_POLICIES
            else None
        ),
        "collapsed_l1_completion": collapsed_prediction,
        "temporal_realization_gap": temporal_gap,
        "oracle_fallback_completed": oracle.completed_value if oracle else None,
        "oracle_total_completed": oracle_total,
        "online_to_oracle_ratio": (
            len(controller.accepted) / max(1, oracle_total) if oracle_total is not None else None
        ),
        "oracle_gap": (
            oracle_total - len(controller.accepted) if oracle_total is not None else None
        ),
        "oracle_transfer_units": oracle.utilized_transfer_units if oracle else None,
        "oracle_cut_value": oracle.cut_value if oracle else None,
        "oracle_nodes": oracle.nodes if oracle else None,
        "oracle_edges": oracle.edges if oracle else None,
    }


def run_bow_temporal_campaign(
    trace_root: Path,
    *,
    contexts: tuple[str, ...] = ("invs13", "lh10", "sfhh"),
    seeds: tuple[int, ...] = tuple(range(8401, 8409)),
    cap_fractions: tuple[float, ...] = (0.10, 0.20, 0.40, 0.80),
    quorum_availabilities: tuple[float, ...] = (0.35, 0.65, 0.90),
    policies: tuple[str, ...] = ONLINE_POLICIES,
    calibration_fraction: float = 0.40,
    buffer_bytes: int = 96 * 1024,
    copy_budget: int = 8,
    routing_policy: RoutingPolicy = RoutingPolicy.SPRAY_WAIT,
    loss_probability: float = 0.0,
    transfer_loss_probability: float = 0.0,
    temporal_jitter_us: int = 0,
    contact_capacity_scale: float = 1.0,
    adatransfer_gate_mode: str = "confidence",
    reclaim_delay_us: int = 2 * HOUR_US,
    transfer_decision_ttl_us: int = 6 * HOUR_US,
    transfer_witness_availability: float | None = None,
    conservative_protection_fraction: float = 0.10,
    oracle_policies: tuple[str, ...] = ("adatransfer",),
    oracle_slot_us: int = HOUR_US,
    oracle_contact_timing: str = "relaxed_start",
    experiment_prefix: str = "bow",
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Run the preregistered held-out matrix without inspecting suffix outcomes."""

    rows: list[dict[str, Any]] = []
    trace_manifest: dict[str, Any] = {}
    for context in contexts:
        contacts_path = trace_root / context / "contacts.csv"
        contacts = perturb_contacts_for_sensitivity(
            load_contacts_csv(contacts_path),
            context=context,
            temporal_jitter_us=temporal_jitter_us,
            contact_capacity_scale=contact_capacity_scale,
        )
        start_us = min(contact.start_us for contact in contacts)
        end_us = max(contact.end_us for contact in contacts)
        cutoff_us = start_us + round((end_us - start_us) * calibration_fraction)
        roles = _calibration_roles(contacts, cutoff_us)
        sites = roles["sites"]
        witness_pool = tuple(dict.fromkeys((*roles["relays"], *sites)))
        witnesses = witness_pool[: min(5, len(witness_pool))]
        quorum_threshold = min(3, len(witnesses))
        reliability = calibration_path_reliability(contacts, sites=sites, cutoff_us=cutoff_us)
        trace_manifest[context] = {
            "contacts_path": str(contacts_path),
            "contacts_sha256": _sha256(contacts_path),
            "contacts": len(contacts),
            "temporal_jitter_us": temporal_jitter_us,
            "contact_capacity_scale": contact_capacity_scale,
            "start_us": start_us,
            "cutoff_us": cutoff_us,
            "end_us": end_us,
            "roles_from_calibration_prefix": {key: list(value) for key, value in roles.items()},
            "path_reliability_from_calibration_prefix": {
                f"{left}->{right}": value for (left, right), value in reliability.items()
            },
        }
        for seed in seeds:
            requests = generate_heldout_requests(
                contacts,
                roles,
                context=context,
                seed=seed,
                cutoff_us=cutoff_us,
            )
            caps = sorted(
                {max(len(sites), round(len(requests) * fraction)) for fraction in cap_fractions}
            )
            for cap in caps:
                for quorum_availability in quorum_availabilities:
                    for policy in policies:
                        config = CausalCampaignConfig(
                            cap=cap,
                            policy=policy,
                            gateway=roles["relays"][0],
                            witnesses=witnesses,
                            quorum_threshold=quorum_threshold,
                            quorum_availability=quorum_availability,
                            buffer_bytes=buffer_bytes,
                            copy_budget=copy_budget,
                            routing_policy=routing_policy,
                            loss_probability=loss_probability,
                            transfer_loss_probability=transfer_loss_probability,
                            adatransfer_gate_mode=adatransfer_gate_mode,
                            reclaim_delay_us=reclaim_delay_us,
                            transfer_decision_ttl_us=transfer_decision_ttl_us,
                            transfer_witness_availability=transfer_witness_availability,
                            conservative_protection_fraction=(conservative_protection_fraction),
                            oracle_slot_us=oracle_slot_us,
                            oracle_contact_timing=oracle_contact_timing,
                            experiment_id=(
                                f"{experiment_prefix}:{context}:{seed}:{cap}:{quorum_availability}"
                            ),
                        )
                        result = evaluate_causal_policy(
                            contacts=tuple(
                                contact for contact in contacts if contact.start_us >= cutoff_us
                            ),
                            requests=requests,
                            sites=sites,
                            config=config,
                            path_reliability=reliability,
                            compute_oracle=policy in oracle_policies,
                        )
                        rows.append(
                            {
                                "context": context,
                                "seed": seed,
                                "cap": cap,
                                "quorum_availability": quorum_availability,
                                "calibration_fraction": calibration_fraction,
                                **result,
                            }
                        )
    frame = pd.DataFrame(rows)
    manifest = {
        "schema_version": 1,
        "campaign": "best-of-both-worlds shared-queue temporal authorization",
        "contexts": list(contexts),
        "seeds": list(seeds),
        "cap_fractions": list(cap_fractions),
        "quorum_availabilities": list(quorum_availabilities),
        "policies": list(policies),
        "calibration_fraction": calibration_fraction,
        "buffer_bytes_per_node": buffer_bytes,
        "copy_budget": copy_budget,
        "routing_policy": RoutingPolicy(routing_policy).value,
        "loss_probability": loss_probability,
        "transfer_loss_probability": transfer_loss_probability,
        "temporal_jitter_us": temporal_jitter_us,
        "contact_capacity_scale": contact_capacity_scale,
        "adatransfer_gate_mode": adatransfer_gate_mode,
        "reclaim_delay_us": reclaim_delay_us,
        "transfer_decision_ttl_us": transfer_decision_ttl_us,
        "transfer_witness_availability": transfer_witness_availability,
        "conservative_protection_fraction": conservative_protection_fraction,
        "oracle_policies": list(oracle_policies),
        "oracle_slot_us": oracle_slot_us,
        "oracle_contact_timing": oracle_contact_timing,
        "routing": (
            f"{RoutingPolicy(routing_policy).value}; one shared bidirectional contact budget; "
            f"configured copy budget {copy_budget}"
        ),
        "causal_classes": [item.value for item in MessageClass],
        "oracle": (
            "integral time-expanded right flow on AdaTransfer rows, conditional on "
            "network-arrived demand; replaces online right-transfer bytes but retains "
            "all other realized traffic"
        ),
        "trace_semantics": "public proximity traces with a deterministic service overlay",
        "traces": trace_manifest,
        "rows": len(frame),
    }
    return frame, manifest


def paired_policy_contrasts(frame: pd.DataFrame, *, reference: str = "adatransfer") -> pd.DataFrame:
    keys = ["context", "seed", "cap", "quorum_availability"]
    required = {*keys, "policy", "completion_rate", "end_to_end_settlement_probability"}
    if missing := required - set(frame.columns):
        raise ValueError(f"missing campaign columns: {sorted(missing)}")
    reference_rows = frame.loc[
        frame["policy"] == reference,
        keys
        + [
            "completion_rate",
            "end_to_end_settlement_probability",
            "exposure_per_1000_requests",
            "forwarded_bytes",
        ],
    ].rename(
        columns={
            "completion_rate": "reference_completion_rate",
            "end_to_end_settlement_probability": "reference_settlement_probability",
            "exposure_per_1000_requests": "reference_exposure_per_1000",
            "forwarded_bytes": "reference_forwarded_bytes",
        }
    )
    paired = frame.merge(reference_rows, on=keys, how="inner", validate="many_to_one")
    paired["completion_delta"] = paired["reference_completion_rate"] - paired["completion_rate"]
    paired["settlement_delta"] = (
        paired["reference_settlement_probability"] - paired["end_to_end_settlement_probability"]
    )
    paired["exposure_delta_per_1000"] = (
        paired["reference_exposure_per_1000"] - paired["exposure_per_1000_requests"]
    )
    paired["byte_ratio"] = paired["reference_forwarded_bytes"] / paired["forwarded_bytes"].clip(
        lower=1
    )
    return paired


def json_ready_result(result: dict[str, Any]) -> dict[str, Any]:
    """Return a JSON-friendly copy used by CLI/tests without lossy stringification."""

    return {
        key: asdict(value) if hasattr(value, "__dataclass_fields__") else value
        for key, value in result.items()
    }


def write_bow_temporal_campaign(
    trace_root: Path,
    output: Path,
    *,
    contexts: tuple[str, ...] = ("invs13", "lh10", "sfhh"),
    seeds: tuple[int, ...] = tuple(range(8401, 8409)),
    cap_fractions: tuple[float, ...] = (0.10, 0.20, 0.40, 0.80),
    quorum_availabilities: tuple[float, ...] = (0.35, 0.65, 0.90),
    policies: tuple[str, ...] = ONLINE_POLICIES,
    bootstrap_repetitions: int = 2000,
) -> dict[str, Path]:
    """Run, analyze and checksum one immutable campaign directory."""

    from aidmesh.analysis.hierarchical_bow import hierarchical_policy_effects

    output.mkdir(parents=True, exist_ok=True)
    frame, manifest = run_bow_temporal_campaign(
        trace_root,
        contexts=contexts,
        seeds=seeds,
        cap_fractions=cap_fractions,
        quorum_availabilities=quorum_availabilities,
        policies=policies,
    )
    contrasts = paired_policy_contrasts(frame)
    effects = hierarchical_policy_effects(
        frame,
        repetitions=bootstrap_repetitions,
    )
    summary = frame.groupby(["context", "policy"], as_index=False).agg(
        runs=("completed", "size"),
        generated_requests=("generated_requests", "sum"),
        mean_completion_rate=("completion_rate", "mean"),
        mean_conditional_completion_rate=("conditional_completion_rate", "mean"),
        mean_settlement_probability=("end_to_end_settlement_probability", "mean"),
        total_exposure=("duplicate_exposure", "sum"),
        total_cap_violations=("risk_cap_violation", "sum"),
        total_transfers=("transfers_started", "sum"),
        total_stranded=("transfers_stranded", "sum"),
        total_forwarded_bytes=("forwarded_bytes", "sum"),
    )
    runs_path = output / "bow_temporal_runs.csv"
    contrasts_path = output / "bow_temporal_paired_contrasts.csv"
    effects_path = output / "bow_temporal_hierarchical_effects.csv"
    summary_path = output / "bow_temporal_summary.csv"
    manifest_path = output / "manifest.json"
    frame.to_csv(runs_path, index=False, lineterminator="\n")
    contrasts.to_csv(contrasts_path, index=False, lineterminator="\n")
    effects.to_csv(effects_path, index=False, lineterminator="\n")
    summary.to_csv(summary_path, index=False, lineterminator="\n")
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    files = (runs_path, contrasts_path, effects_path, summary_path, manifest_path)
    checksums_path = output / "CHECKSUMS.sha256"
    checksums_path.write_text(
        "".join(
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n" for path in files
        ),
        encoding="ascii",
    )
    return {
        "runs": runs_path,
        "contrasts": contrasts_path,
        "effects": effects_path,
        "summary": summary_path,
        "manifest": manifest_path,
        "checksums": checksums_path,
    }
