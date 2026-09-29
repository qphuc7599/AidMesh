"""Held-out safety--availability frontier on temporal contact events.

The first 40% of each trace is used only to assign infrastructure roles by
contact-time centrality.  Requests are generated at client activity timestamps
in the final 60%, and every delivery/transfer decision is evaluated against the
held-out temporal contacts.  The source traces remain proximity data; request,
site and voucher semantics are a deterministic experimental overlay.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import math
import random
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import pandas as pd

from aidmesh.analysis.frontier import hypervolume_2d
from aidmesh.analysis.quantitative_frontier import (
    completion_from_identity,
    l1_misplacement,
    relocation_gain_from_identity,
)
from aidmesh.common.canonical import stable_hash
from aidmesh.simulator.dtn import (
    HOUR_US,
    BundleSpec,
    Contact,
    RoutingPolicy,
    TemporalContactSimulator,
    load_contacts_csv,
)

METHODS = (
    "fixed_home",
    "static_rights",
    "optimized_static",
    "periodic_sync",
    "transferable_rights",
    "first_seen_unsafe",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stable_unit(payload: object, *, domain: str) -> float:
    return int(stable_hash(payload, domain=domain)[:16], 16) / float(16**16)


@dataclass(frozen=True)
class HeldoutRequest:
    bundle: BundleSpec
    home_site: str
    nullifier: str


@dataclass(frozen=True)
class _Accepted:
    bundle_id: str
    nullifier: str
    site: str
    accepted_us: int
    non_home: bool


class TemporalPathIndex:
    """Exact slot-causal earliest-arrival queries with allocation-epoch caching."""

    def __init__(self, contacts: tuple[Contact, ...], *, epoch_us: int = HOUR_US) -> None:
        self.contacts = contacts
        self.starts = [contact.start_us for contact in contacts]
        self.epoch_us = epoch_us
        self.cache: dict[tuple[str, str, int, int], int | None] = {}

    def earliest(
        self, source: str, destination: str, created_us: int, expires_us: int
    ) -> int | None:
        # Transfers are emitted at the next registered allocation epoch.  This
        # both models batching and makes the query cache independent of request ID.
        emitted_us = math.ceil(created_us / self.epoch_us) * self.epoch_us
        if emitted_us > expires_us:
            return None
        key = (source, destination, emitted_us, expires_us // self.epoch_us)
        if key in self.cache:
            return self.cache[key]
        arrival: dict[str, int] = {source: emitted_us}
        index = bisect.bisect_left(self.starts, emitted_us)
        while index < len(self.contacts):
            start = self.contacts[index].start_us
            if start > expires_us:
                break
            end = index + 1
            while end < len(self.contacts) and self.contacts[end].start_us == start:
                end += 1
            updates: dict[str, int] = {}
            for contact in self.contacts[index:end]:
                if contact.end_us > expires_us:
                    continue
                for sender, receiver in (
                    (contact.node_a, contact.node_b),
                    (contact.node_b, contact.node_a),
                ):
                    if arrival.get(sender, math.inf) > start:
                        continue
                    if contact.end_us < min(
                        arrival.get(receiver, math.inf), updates.get(receiver, math.inf)
                    ):
                        updates[receiver] = contact.end_us
            for node, arrived_us in updates.items():
                arrival[node] = min(arrival.get(node, math.inf), arrived_us)
            if destination in arrival:
                self.cache[key] = arrival[destination]
                return arrival[destination]
            index = end
        self.cache[key] = None
        return None


def _calibration_roles(
    contacts: tuple[Contact, ...], cutoff_us: int, *, sites: int = 4, relays: int = 3
) -> dict[str, tuple[str, ...]]:
    scores: Counter[str] = Counter()
    all_nodes: set[str] = set()
    for contact in contacts:
        all_nodes.update((contact.node_a, contact.node_b))
        if contact.start_us >= cutoff_us:
            continue
        duration = contact.end_us - contact.start_us
        scores[contact.node_a] += duration
        scores[contact.node_b] += duration
    ordered = sorted(all_nodes, key=lambda node: (-scores[node], node))
    if len(ordered) <= sites + relays:
        raise ValueError("trace does not contain enough nodes for frozen roles")
    return {
        "sites": tuple(ordered[:sites]),
        "relays": tuple(ordered[sites : sites + relays]),
        "clients": tuple(ordered[sites + relays :]),
    }


def _weighted_site(
    sites: tuple[str, ...],
    u: float,
    *,
    weights: tuple[float, ...] = (0.55, 0.25, 0.15, 0.05),
) -> str:
    if len(weights) != len(sites) or not math.isclose(sum(weights), 1.0):
        raise ValueError("service weights must match sites and sum to one")
    cumulative = 0.0
    for site, weight in zip(sites, weights, strict=True):
        cumulative += weight
        if u < cumulative:
            return site
    return sites[-1]


def generate_heldout_requests(
    contacts: tuple[Contact, ...],
    roles: dict[str, tuple[str, ...]],
    *,
    context: str,
    seed: int,
    cutoff_us: int,
    ttl_us: int = 24 * HOUR_US,
    service_weights: tuple[float, ...] = (0.55, 0.25, 0.15, 0.05),
) -> tuple[HeldoutRequest, ...]:
    activity: dict[str, list[int]] = defaultdict(list)
    for contact in contacts:
        if contact.start_us < cutoff_us:
            continue
        activity[contact.node_a].append(contact.start_us)
        activity[contact.node_b].append(contact.start_us)
    candidates = [client for client in roles["clients"] if activity.get(client)]
    sites = roles["sites"]
    requests: list[HeldoutRequest] = []
    for index, client in enumerate(candidates):
        timestamps = sorted(activity[client])
        position = int(
            _stable_unit(
                {"context": context, "seed": seed, "client": client},
                domain="aidmesh/heldout/activity-index/v1",
            )
            * len(timestamps)
        )
        created_us = timestamps[min(position, len(timestamps) - 1)]
        service_site = _weighted_site(
            sites,
            _stable_unit(
                {"context": context, "seed": seed, "client": client},
                domain="aidmesh/heldout/service-site/v1",
            ),
            weights=service_weights,
        )
        home_index = int(
            _stable_unit(
                {"context": context, "client": client},
                domain="aidmesh/heldout/home-site/v1",
            )
            * len(sites)
        )
        home_site = sites[min(home_index, len(sites) - 1)]
        # In every block of ten clients, two vouchers are an adversarial replay
        # pair.  The service sites are forced apart to exercise cross-site state.
        block, offset = divmod(index, 10)
        if offset in (0, 1) and index + (1 - offset) < len(candidates):
            nullifier = f"dup-{context}-{seed}-{block}"
            home_site = sites[block % len(sites)]
            service_site = sites[(block + offset) % len(sites)]
        else:
            nullifier = f"unique-{context}-{seed}-{client}"
        bundle_id = f"request-{context}-{seed}-{client}"
        requests.append(
            HeldoutRequest(
                BundleSpec(
                    bundle_id=bundle_id,
                    source=client,
                    destination=service_site,
                    created_us=created_us,
                    ttl_us=ttl_us,
                    size_bytes=2048,
                    risk_score=1.0,
                ),
                home_site=home_site,
                nullifier=nullifier,
            )
        )
    return tuple(
        sorted(requests, key=lambda request: (request.bundle.created_us, request.bundle.bundle_id))
    )


def _initial_balances(sites: tuple[str, ...], cap: int) -> dict[str, int]:
    quotient, remainder = divmod(cap, len(sites))
    return {site: quotient + int(index < remainder) for index, site in enumerate(sites)}


def _optimized_static_balances(sites: tuple[str, ...], cap: int) -> dict[str, int]:
    """Hamilton apportionment using the frozen 55/25/15/5 demand policy.

    The weights are fixed before the held-out suffix is inspected. This is a
    deliberately strong static baseline: it knows the registered demand model,
    but it cannot move capacity after the run begins.
    """

    weights = (0.55, 0.25, 0.15, 0.05)
    quotas = [cap * weight for weight in weights[: len(sites)]]
    floors = [math.floor(value) for value in quotas]
    remainder = cap - sum(floors)
    order = sorted(
        range(len(sites)),
        key=lambda index: (-(quotas[index] - floors[index]), sites[index]),
    )
    for index in order[:remainder]:
        floors[index] += 1
    return dict(zip(sites, floors, strict=True))


def _evaluate_method(
    requests: tuple[HeldoutRequest, ...],
    deliveries: dict[str, int],
    *,
    method: str,
    cap: int,
    sites: tuple[str, ...],
    path_index: TemporalPathIndex,
    gateway: str,
    sync_period_us: int = 6 * HOUR_US,
    quorum_probability: float = 0.0,
    transfer_loss: float = 0.0,
    transfer_delay_us: int = 0,
    experiment_id: str = "primary",
) -> dict[str, Any]:
    if not 0.0 <= quorum_probability <= 1.0:
        raise ValueError("quorum_probability must be in [0, 1]")
    if not 0.0 <= transfer_loss <= 1.0:
        raise ValueError("transfer_loss must be in [0, 1]")
    balances = (
        _optimized_static_balances(sites, cap)
        if method == "optimized_static"
        else _initial_balances(sites, cap)
    )
    initial_balances = dict(balances)
    delivered_capacity = dict(balances)
    local_seen: dict[str, set[str]] = {site: set() for site in sites}
    globally_seen: set[str] = set()
    quorum_seen: set[str] = set()
    quorum_completed_pairs: set[tuple[str, str]] = set()
    globally_remaining = cap
    request_by_id = {request.bundle.bundle_id: request for request in requests}
    ordered = sorted(deliveries.items(), key=lambda row: (row[1], row[0]))
    accepted: list[_Accepted] = []
    fallback_completed = 0
    stranded = 0
    transfers_started = transfers_delivered = 0
    sync_attempts = sync_successes = 0
    for bundle_id, delivered_us in ordered:
        request = request_by_id[bundle_id]
        site = request.bundle.destination
        if delivered_us > request.bundle.expires_us or request.nullifier in local_seen[site]:
            continue
        quorum_draw = _stable_unit(
            {
                "experiment": experiment_id,
                "bundle": bundle_id,
                "nullifier": request.nullifier,
                "site": site,
            },
            domain="aidmesh/heldout/quorum-availability/v1",
        )
        if request.nullifier not in quorum_seen and quorum_draw < quorum_probability:
            quorum_seen.add(request.nullifier)
            quorum_completed_pairs.add((site, request.nullifier))
            local_seen[site].add(request.nullifier)
            accepted.append(
                _Accepted(
                    bundle_id,
                    request.nullifier,
                    site,
                    delivered_us,
                    site != request.home_site,
                )
            )
            continue
        if method == "fixed_home" and site != request.home_site:
            continue
        if method == "first_seen_unsafe":
            local_seen[site].add(request.nullifier)
            accepted.append(
                _Accepted(
                    bundle_id,
                    request.nullifier,
                    site,
                    delivered_us,
                    site != request.home_site,
                )
            )
            continue
        if method == "periodic_sync":
            sync_attempts += 1
            emitted_us = math.ceil(delivered_us / sync_period_us) * sync_period_us
            outbound = path_index.earliest(
                site, gateway, emitted_us, request.bundle.expires_us
            )
            if outbound is None:
                continue
            returned = path_index.earliest(
                gateway, site, outbound, request.bundle.expires_us
            )
            if (
                returned is None
                or request.nullifier in globally_seen
                or globally_remaining <= 0
            ):
                continue
            sync_successes += 1
            globally_seen.add(request.nullifier)
            globally_remaining -= 1
            local_seen[site].add(request.nullifier)
            accepted.append(
                _Accepted(
                    bundle_id,
                    request.nullifier,
                    site,
                    returned,
                    site != request.home_site,
                )
            )
            continue
        if balances[site] > 0:
            balances[site] -= 1
            fallback_completed += 1
            local_seen[site].add(request.nullifier)
            accepted.append(
                _Accepted(
                    bundle_id,
                    request.nullifier,
                    site,
                    delivered_us,
                    site != request.home_site,
                )
            )
            continue
        if method != "transferable_rights":
            continue
        donors = [
            donor
            for donor, donor_balance in balances.items()
            if donor != site and donor_balance > 0
        ]
        if not donors:
            continue
        # Donor choice uses only current protocol state; it never peeks at future
        # held-out contacts. A missing path therefore strands/burns the debited
        # escrow unit, following the fail-closed lifecycle in the formal model.
        donor = min(donors, key=lambda candidate: (-balances[candidate], candidate))
        transfers_started += 1
        balances[donor] -= 1  # begin-transfer debit precedes message emission
        delivered_capacity[donor] -= 1
        arrival = path_index.earliest(donor, site, delivered_us, request.bundle.expires_us)
        loss_draw = _stable_unit(
            {
                "experiment": experiment_id,
                "bundle": bundle_id,
                "donor": donor,
                "site": site,
            },
            domain="aidmesh/heldout/transfer-loss/v1",
        )
        if arrival is not None:
            arrival += transfer_delay_us
        if (
            arrival is None
            or arrival > request.bundle.expires_us
            or loss_draw < transfer_loss
        ):
            stranded += 1
            continue
        transfers_delivered += 1
        delivered_capacity[site] += 1
        # Accept and consume the transferred unit atomically at the destination.
        fallback_completed += 1
        local_seen[site].add(request.nullifier)
        accepted.append(
            _Accepted(
                bundle_id,
                request.nullifier,
                site,
                arrival,
                site != request.home_site,
            )
        )

    by_nullifier = Counter(item.nullifier for item in accepted)
    exposed = sum(max(0, count - 1) for count in by_nullifier.values())
    cap_violation = max(
        0,
        (len(accepted) if method == "first_seen_unsafe" else fallback_completed) - cap,
    )
    demand_by_site: dict[str, set[str]] = {site: set() for site in sites}
    for bundle_id, delivered_us in deliveries.items():
        request = request_by_id[bundle_id]
        if (
            delivered_us <= request.bundle.expires_us
            and (request.bundle.destination, request.nullifier)
            not in quorum_completed_pairs
        ):
            demand_by_site[request.bundle.destination].add(request.nullifier)
    demand_vector = [len(demand_by_site[site]) for site in sites]
    initial_vector = [initial_balances[site] for site in sites]
    delivered_vector = [delivered_capacity[site] for site in sites]
    rights_method = method in {"static_rights", "optimized_static", "transferable_rights"}
    identity_completion = (
        len(quorum_seen) + completion_from_identity(delivered_vector, demand_vector)
        if rights_method
        else None
    )
    equal_static_vector = list(_initial_balances(sites, cap).values())
    identity_gain_vs_equal_static = (
        relocation_gain_from_identity(equal_static_vector, delivered_vector, demand_vector)
        if rights_method
        else None
    )
    criterion_margin = (
        l1_misplacement(equal_static_vector, demand_vector)
        - l1_misplacement(delivered_vector, demand_vector)
        - (cap - sum(delivered_vector))
        if rights_method
        else None
    )
    return {
        "method": method,
        "generated_requests": len(requests),
        "network_delivered_requests": len(deliveries),
        "completed": len(accepted),
        "completion_rate": len(accepted) / max(1, len(requests)),
        "conditional_completion_rate": len(accepted) / max(1, len(deliveries)),
        "non_home_completed": sum(item.non_home for item in accepted),
        "duplicate_exposure": exposed,
        "normalized_exposure": exposed / max(1, cap),
        "cap_violation": cap_violation,
        "rights_remaining": sum(balances.values()),
        "transfers_started": transfers_started,
        "transfers_delivered": transfers_delivered,
        "stranded_rights": stranded,
        "quorum_completed": len(quorum_seen),
        "fallback_completed": fallback_completed,
        "residual_demand": sum(demand_vector),
        "initial_budget": sum(initial_vector) if rights_method else None,
        "delivered_budget": sum(delivered_vector) if rights_method else None,
        "initial_placement_l1": (
            l1_misplacement(initial_vector, demand_vector) if rights_method else None
        ),
        "delivered_placement_l1": (
            l1_misplacement(delivered_vector, demand_vector) if rights_method else None
        ),
        "identity_completion": identity_completion,
        "identity_matches_observed": (
            identity_completion == len(accepted) if rights_method else None
        ),
        "identity_gain_vs_equal_static": identity_gain_vs_equal_static,
        "transfer_criterion_margin": criterion_margin,
        "transfer_criterion_predicts_gain": (
            criterion_margin > 0 if rights_method else None
        ),
        "sync_attempts": sync_attempts,
        "sync_successes": sync_successes,
    }


def run_heldout_temporal_frontier(
    trace_root: Path,
    *,
    contexts: tuple[str, ...] = ("invs13", "lh10", "sfhh"),
    seeds: tuple[int, ...] = tuple(range(7301, 7309)),
    cap_fractions: tuple[float, ...] = (0.10, 0.20, 0.40, 0.80),
    calibration_fraction: float = 0.40,
    buffer_bytes: int = 96 * 1024,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    trace_manifest: dict[str, Any] = {}
    for context in contexts:
        context_root = trace_root / context
        contacts_path = context_root / "contacts.csv"
        contacts = load_contacts_csv(contacts_path)
        start_us = min(contact.start_us for contact in contacts)
        end_us = max(contact.end_us for contact in contacts)
        cutoff_us = start_us + round((end_us - start_us) * calibration_fraction)
        roles = _calibration_roles(contacts, cutoff_us)
        path_index = TemporalPathIndex(contacts)
        trace_manifest[context] = {
            "contacts_path": str(contacts_path),
            "contacts_sha256": _sha256(contacts_path),
            "contacts": len(contacts),
            "start_us": start_us,
            "cutoff_us": cutoff_us,
            "end_us": end_us,
            "roles_from_calibration_only": {key: list(value) for key, value in roles.items()},
        }
        for seed in seeds:
            requests = generate_heldout_requests(
                contacts,
                roles,
                context=context,
                seed=seed,
                cutoff_us=cutoff_us,
            )
            bundles = tuple(request.bundle for request in requests)
            network = TemporalContactSimulator(
                contacts=contacts,
                bundles=bundles,
                policy=RoutingPolicy.EPIDEMIC,
                buffer_bytes=buffer_bytes,
                copy_budget=8,
            ).run()
            deliveries = {
                delivery.bundle_id: delivery.delivered_us for delivery in network.deliveries
            }
            caps = sorted(
                {max(len(roles["sites"]), round(len(requests) * f)) for f in cap_fractions}
            )
            for cap in caps:
                for method in METHODS:
                    result = _evaluate_method(
                        requests,
                        deliveries,
                        method=method,
                        cap=cap,
                        sites=roles["sites"],
                        path_index=path_index,
                        gateway=roles["relays"][0],
                    )
                    rows.append(
                        {
                            "context": context,
                            "seed": seed,
                            "cap": cap,
                            "calibration_fraction": calibration_fraction,
                            "heldout_fraction": 1 - calibration_fraction,
                            "contact_cutoff_us": cutoff_us,
                            "network_event_checksum": network.event_checksum,
                            **result,
                        }
                    )
    frame = pd.DataFrame(rows)
    manifest = {
        "schema_version": 1,
        "design": "calibration-prefix/held-out-suffix temporal-contact replication",
        "contexts": list(contexts),
        "seeds": list(seeds),
        "cap_fractions_of_generated_demand": list(cap_fractions),
        "calibration_fraction": calibration_fraction,
        "buffer_bytes": buffer_bytes,
        "request_bytes": 2048,
        "transfer_bytes_semantics": (
            "one-unit control message; exact temporal path; one-hour allocation epoch"
        ),
        "routing": "finite-buffer Epidemic for requests",
        "primary_safe_baselines": [
            "fixed_home",
            "optimized_static",
            "periodic_sync",
        ],
        "optimized_static_semantics": (
            "Hamilton apportionment from the preregistered 55/25/15/5 demand "
            "weights; no held-out outcomes or future contacts"
        ),
        "periodic_sync_semantics": (
            "six-hour epochs; causal site-to-calibration-selected-gateway round "
            "trip on held-out contacts; global cap and nullifier set"
        ),
        "duplicate_overlay": "one forced cross-site replay pair per ten clients",
        "demand_overlay": "fixed 55/25/15/5 percent service-site weights",
        "trace_semantics": "public proximity contacts, not humanitarian participants",
        "traces": trace_manifest,
    }
    return frame, manifest


def _frontier_rows(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for (context, seed, method), group in frame.groupby(["context", "seed", "method"], sort=True):
        points = [
            (min(1.0, float(row.normalized_exposure)), float(row.completion_rate))
            for row in group.itertuples()
        ]
        rows.append(
            {
                "context": context,
                "seed": seed,
                "method": method,
                "hypervolume": hypervolume_2d(points),
            }
        )
    return pd.DataFrame(rows)


def _paired_bootstrap(values: list[float], *, seed: int = 20260718) -> tuple[float, float]:
    if not values:
        return math.nan, math.nan
    rng = random.Random(seed)
    means = []
    for _ in range(10_000):
        sample = [values[rng.randrange(len(values))] for _ in values]
        means.append(sum(sample) / len(sample))
    means.sort()
    return means[249], means[9749]


def analyze_heldout_temporal_frontier(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    volumes = _frontier_rows(frame)
    rows: list[dict[str, Any]] = []
    for context in sorted((*frame["context"].unique(), "pooled_descriptive")):
        subset = (
            volumes if context == "pooled_descriptive" else volumes[volumes["context"] == context]
        )
        pivot = subset.pivot_table(
            index=["context", "seed"], columns="method", values="hypervolume"
        )
        for baseline in ("fixed_home", "optimized_static", "periodic_sync", "static_rights"):
            differences = (pivot["transferable_rights"] - pivot[baseline]).tolist()
            lower, upper = _paired_bootstrap(
                differences,
                seed=20260724
                + sum(ord(character) for character in f"{context}|{baseline}"),
            )
            rows.append(
                {
                    "context": context,
                    "baseline": baseline,
                    "paired_units": len(differences),
                    "mean_transferable_minus_baseline_hypervolume": sum(differences)
                    / max(1, len(differences)),
                    "bootstrap_95ci_lower": lower,
                    "bootstrap_95ci_upper": upper,
                    "positive_pairs": sum(value > 0 for value in differences),
                    "zero_pairs": sum(value == 0 for value in differences),
                    "negative_pairs": sum(value < 0 for value in differences),
                }
            )
    return volumes, pd.DataFrame(rows)


def completion_contrasts(frame: pd.DataFrame) -> pd.DataFrame:
    """Paired cap-level service effects against preregistered safe baselines."""

    rows: list[dict[str, Any]] = []
    baselines = ("fixed_home", "optimized_static", "periodic_sync")
    for context in sorted(frame["context"].unique()):
        for cap in sorted(frame[frame["context"] == context]["cap"].unique()):
            subset = frame[(frame["context"] == context) & (frame["cap"] == cap)]
            transferable = subset[subset["method"] == "transferable_rights"].set_index("seed")
            for baseline in baselines:
                comparator = subset[subset["method"] == baseline].set_index("seed")
                common = transferable.index.intersection(comparator.index)
                differences = (
                    transferable.loc[common, "completion_rate"]
                    - comparator.loc[common, "completion_rate"]
                ).tolist()
                exposure_differences = (
                    transferable.loc[common, "normalized_exposure"]
                    - comparator.loc[common, "normalized_exposure"]
                ).tolist()
                lower, upper = _paired_bootstrap(
                    differences,
                    seed=20260724
                    + int(cap)
                    + sum(ord(character) for character in f"{context}|{baseline}|completion"),
                )
                mean_effect = sum(differences) / max(1, len(differences))
                mean_exposure_effect = sum(exposure_differences) / max(
                    1, len(exposure_differences)
                )
                cap_safe = bool(
                    (transferable.loc[common, "cap_violation"] == 0).all()
                    and (comparator.loc[common, "cap_violation"] == 0).all()
                )
                rows.append(
                    {
                        "context": context,
                        "cap": cap,
                        "baseline": baseline,
                        "paired_units": len(common),
                        "mean_completion_effect": mean_effect,
                        "bootstrap_95ci_lower": lower,
                        "bootstrap_95ci_upper": upper,
                        "mean_normalized_exposure_effect": mean_exposure_effect,
                        "cap_safe": cap_safe,
                        "preregistered_completion_margin": 0.02,
                        "passes_completion_margin": bool(
                            cap_safe and lower > 0.02 and mean_exposure_effect <= 0
                        ),
                    }
                )
    return pd.DataFrame(rows)


def mechanism_summary(frame: pd.DataFrame) -> pd.DataFrame:
    """Summarize the exact transfer criterion by held-out trace context."""

    transfer = frame[frame["method"] == "transferable_rights"].copy()
    if transfer.empty:
        raise ValueError("transferable_rights rows are required for mechanism analysis")
    if not transfer["identity_matches_observed"].astype(bool).all():
        raise ValueError("quantitative frontier identity failed on a temporal run")
    transfer["mismatch_reduction"] = (
        transfer["initial_placement_l1"] - transfer["delivered_placement_l1"]
    )
    transfer["completion_gain_per_1000_requests"] = (
        1000.0 * transfer["identity_gain_vs_equal_static"] / transfer["generated_requests"]
    )
    records = []
    for context, group in transfer.groupby("context", sort=True):
        gain = group["identity_gain_vs_equal_static"]
        records.append(
            {
                "context": context,
                "run_cap_cells": int(len(group)),
                "identity_failures": int((~group["identity_matches_observed"].astype(bool)).sum()),
                "mean_initial_l1": float(group["initial_placement_l1"].mean()),
                "mean_delivered_l1": float(group["delivered_placement_l1"].mean()),
                "mean_mismatch_reduction": float(group["mismatch_reduction"].mean()),
                "mean_stranded_capacity": float(group["stranded_rights"].mean()),
                "mean_transfer_criterion_margin": float(
                    group["transfer_criterion_margin"].mean()
                ),
                "mean_completion_gain_vs_equal_static": float(gain.mean()),
                "mean_completion_gain_per_1000_requests": float(
                    group["completion_gain_per_1000_requests"].mean()
                ),
                "positive_cells": int((gain > 0).sum()),
                "zero_cells": int((gain == 0).sum()),
                "negative_cells": int((gain < 0).sum()),
            }
        )
    return pd.DataFrame(records)


def write_heldout_temporal_frontier(
    frame: pd.DataFrame, manifest: dict[str, Any], output: Path
) -> dict[str, Path]:
    output.mkdir(parents=True, exist_ok=True)
    volumes, contrasts = analyze_heldout_temporal_frontier(frame)
    completion = completion_contrasts(frame)
    mechanism = mechanism_summary(frame)
    paths = {
        "rows": output / "heldout_temporal_frontier_rows.csv",
        "volumes": output / "heldout_temporal_hypervolumes.csv",
        "contrasts": output / "heldout_temporal_contrasts.csv",
        "completion_contrasts": output
        / "heldout_temporal_completion_contrasts.csv",
        "practical_decisions": output / "heldout_temporal_practical_decisions.csv",
        "mechanism_summary": output / "heldout_temporal_mechanism_summary.csv",
        "manifest": output / "heldout_temporal_manifest.json",
        "figure": output / "heldout_temporal_frontier.pdf",
        "mechanism_figure": output / "heldout_temporal_mechanism.pdf",
    }
    frame.to_csv(paths["rows"], index=False)
    volumes.to_csv(paths["volumes"], index=False)
    contrasts.to_csv(paths["contrasts"], index=False)
    completion.to_csv(paths["completion_contrasts"], index=False)
    practical = contrasts.copy()
    practical["preregistered_hypervolume_margin"] = 0.01
    practical["passes_hypervolume_margin"] = (
        practical["bootstrap_95ci_lower"] > 0.01
    )
    practical.to_csv(paths["practical_decisions"], index=False)
    mechanism.to_csv(paths["mechanism_summary"], index=False)
    paths["manifest"].write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    summary = frame.groupby(["context", "method", "cap"], as_index=False).agg(
        completion_rate=("completion_rate", "mean"),
        normalized_exposure=("normalized_exposure", "mean"),
    )
    fig, axes = plt.subplots(1, 3, figsize=(10.4, 3.25), sharey=True)
    labels = {
        "fixed_home": "Fixed-home",
        "static_rights": "Static rights",
        "optimized_static": "Optimized static",
        "periodic_sync": "Periodic sync",
        "transferable_rights": "Transferable rights",
        "first_seen_unsafe": "First-seen (unsafe)",
    }
    markers = {
        "fixed_home": "s",
        "static_rights": "o",
        "optimized_static": "P",
        "periodic_sync": "D",
        "transferable_rights": "^",
        "first_seen_unsafe": "x",
    }
    for axis, context in zip(axes, sorted(summary["context"].unique()), strict=True):
        for method in METHODS:
            subset = summary[(summary["context"] == context) & (summary["method"] == method)]
            axis.plot(
                subset["normalized_exposure"],
                subset["completion_rate"],
                marker=markers[method],
                linewidth=1.4,
                label=labels[method],
            )
        axis.set_title(context.upper())
        axis.set_xlabel("Duplicate exposure / cap")
        axis.grid(alpha=0.25)
    axes[0].set_ylabel("Completion before deadline")
    handles, legend_labels = axes[-1].get_legend_handles_labels()
    fig.legend(handles, legend_labels, loc="upper center", ncol=4, frameon=False)
    fig.tight_layout(rect=(0, 0, 1, 0.88))
    fig.savefig(paths["figure"], bbox_inches="tight")
    plt.close(fig)

    mechanism_plot = mechanism.set_index("context")
    contexts = [context.upper() for context in mechanism_plot.index]
    positions = list(range(len(contexts)))
    width = 0.25
    fig, axis = plt.subplots(figsize=(7.2, 3.5))
    axis.bar(
        [position - width for position in positions],
        mechanism_plot["mean_mismatch_reduction"],
        width,
        label=r"Mismatch reduction $\|b-d\|_1-\|b'-d\|_1$",
        color="#2b6cb0",
    )
    axis.bar(
        positions,
        mechanism_plot["mean_stranded_capacity"],
        width,
        label=r"Stranded capacity $\ell$",
        color="#dd6b20",
    )
    axis.bar(
        [position + width for position in positions],
        mechanism_plot["mean_transfer_criterion_margin"],
        width,
        label="Net criterion margin",
        color="#2f855a",
    )
    axis.axhline(0, color="black", linewidth=0.8)
    axis.set_xticks(positions, contexts)
    axis.set_ylabel("Mean value units per run-cap cell")
    axis.set_title("Why causal transfer helps or hurts")
    axis.grid(axis="y", alpha=0.25)
    axis.legend(frameon=False, fontsize=8, loc="upper left")
    fig.tight_layout()
    fig.savefig(paths["mechanism_figure"], bbox_inches="tight")
    plt.close(fig)

    checksums = {name: _sha256(path) for name, path in paths.items() if name != "manifest"}
    manifest["output_sha256"] = checksums
    paths["manifest"].write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return paths
