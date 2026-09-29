"""Time-expanded risk-capacity frontier and online-policy bounds.

The collapsed L1 identity in :mod:`quantitative_frontier` assumes that delivered
capacity and demand are already co-indexed at a deadline.  This module retains
time, deadlines, transfer capacity, and request transport loss explicitly.  The
offline unit-right oracle is an integral maximum-flow problem; every feasible
flow preserves the issued risk budget by construction.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

import networkx as nx

from aidmesh.analysis.quantitative_frontier import fallback_completion
from aidmesh.simulator.dtn import Contact


@dataclass(frozen=True)
class TemporalDemand:
    demand_id: str
    site: str
    release_us: int
    deadline_us: int
    value: int = 1


@dataclass(frozen=True)
class TimeExpandedOracleResult:
    completed_value: int
    initial_budget: int
    total_demand_value: int
    unserved_value: int
    utilized_transfer_units: int
    cut_value: int
    time_slots: int
    nodes: int
    edges: int
    completion_by_site: Mapping[str, int]


def _slot(time_us: int, slot_us: int, *, ceil: bool = False) -> int:
    if slot_us <= 0:
        raise ValueError("slot_us must be positive")
    if time_us < 0:
        raise ValueError("time must be nonnegative")
    return math.ceil(time_us / slot_us) if ceil else time_us // slot_us


def _validate_initial_rights(initial_rights: Mapping[str, int]) -> dict[str, int]:
    if not initial_rights:
        raise ValueError("initial_rights must not be empty")
    rights = {str(site): int(value) for site, value in initial_rights.items()}
    if any(not site or value < 0 for site, value in rights.items()):
        raise ValueError("initial rights require non-empty sites and nonnegative values")
    return rights


def solve_time_expanded_oracle(
    *,
    initial_rights: Mapping[str, int],
    demands: Sequence[TemporalDemand],
    contacts: Sequence[Contact],
    slot_us: int,
    transfer_size_bytes: int = 512,
    contact_residual_units: Mapping[str, int] | None = None,
    loss_probability: float = 0.0,
    contact_timing: str = "relaxed_start",
) -> TimeExpandedOracleResult:
    """Return an integral offline upper comparator for unit rights.

    Storage edges retain rights at one site.  Contact edges move rights between
    sites and are capacity-limited by serialized transfer messages.  Demand
    edges consume one right at any slot between request arrival and deadline.
    When ``contact_residual_units`` is supplied it must use the shared-queue
    contact-key format ``index:start:end:a:b`` and therefore prices transfer only
    from bytes left after other protocol classes.  Demand release and deadline
    are rounded down to the same grid.  Every contact is likewise relaxed to an
    instantaneous bidirectional edge at the beginning of its containing start
    slot.  The relaxation can compose contacts in an order that is impossible
    inside one coarse slot, but that is intentional: it preserves the
    *upper-comparator* contract.  Rounding a contact's arrival up to the next
    slot would stretch a sub-slot path and can incorrectly place a feasible
    online completion above the supposed oracle.  Set ``contact_timing`` to
    ``"event_respecting"`` to move a right from the contact start event to its
    end event instead.  That mode forbids instantaneous within-slot chaining
    and is exact for the declared store-carry-forward abstraction (integral
    unit rights, deterministic contact capacities, and independent directed
    link capacity); it is not a model of radio-layer contention.
    """

    rights = _validate_initial_rights(initial_rights)
    if transfer_size_bytes <= 0:
        raise ValueError("transfer_size_bytes must be positive")
    if not 0.0 <= loss_probability <= 1.0:
        raise ValueError("loss_probability must be in [0, 1]")
    if contact_timing not in {"relaxed_start", "event_respecting"}:
        raise ValueError("contact_timing must be 'relaxed_start' or 'event_respecting'")
    if any(
        not demand.demand_id
        or not demand.site
        or demand.release_us < 0
        or demand.deadline_us < demand.release_us
        or demand.value <= 0
        for demand in demands
    ):
        raise ValueError("invalid temporal demand")
    if len({demand.demand_id for demand in demands}) != len(demands):
        raise ValueError("demand identifiers must be unique")

    all_sites = set(rights)
    all_sites.update(demand.site for demand in demands)
    for contact in contacts:
        all_sites.update((contact.node_a, contact.node_b))
    relevant_sites = tuple(sorted(all_sites))
    horizon_us = max(
        [demand.deadline_us for demand in demands] + [contact.end_us for contact in contacts] + [0]
    )
    horizon_slot = _slot(horizon_us, slot_us, ceil=True)
    budget = sum(rights.values())
    graph = nx.DiGraph()
    source = ("source",)
    sink = ("sink",)
    graph.add_nodes_from((source, sink))

    def site_node(site: str, time_slot: int) -> tuple[str, str, int]:
        return ("site", site, time_slot)

    # Sparse event expansion of the coarse-grid relaxation.  A right can change
    # location only at a relaxed contact event and can be consumed only after a
    # relaxed demand release and no later than its deadline; empty grid slots
    # between those events are therefore losslessly contracted.
    events_by_site: dict[str, set[int]] = {site: {0} for site in relevant_sites}
    contact_slots: list[tuple[int, Contact, int, int, str]] = []
    for index, contact in enumerate(contacts):
        start_slot = min(horizon_slot, _slot(contact.start_us, slot_us))
        # The historical comparator is deliberately optimistic.  The new
        # confirmatory campaign instead uses the event-respecting arrival slot.
        end_slot = (
            start_slot
            if contact_timing == "relaxed_start"
            else min(horizon_slot, _slot(contact.end_us, slot_us, ceil=True))
        )
        key = f"{index}:{contact.start_us}:{contact.end_us}:{contact.node_a}:{contact.node_b}"
        contact_slots.append((index, contact, start_slot, end_slot, key))
        events_by_site[contact.node_a].update((start_slot, end_slot))
        events_by_site[contact.node_b].update((start_slot, end_slot))
    demand_windows: dict[str, tuple[int, int]] = {}
    for demand in demands:
        release_slot = _slot(demand.release_us, slot_us)
        deadline_slot = min(horizon_slot, _slot(demand.deadline_us, slot_us))
        demand_windows[demand.demand_id] = (release_slot, deadline_slot)
        events_by_site[demand.site].update((release_slot, deadline_slot))

    for site in relevant_sites:
        ordered_events = sorted(events_by_site[site])
        for time_slot in ordered_events:
            graph.add_node(site_node(site, time_slot))
        for left_slot, right_slot in zip(ordered_events, ordered_events[1:], strict=False):
            graph.add_edge(
                site_node(site, left_slot),
                site_node(site, right_slot),
                capacity=budget,
                kind="storage",
            )
    for site, value in rights.items():
        if value:
            graph.add_edge(source, site_node(site, 0), capacity=value, kind="issue")

    transfer_edges: list[tuple[AnyNode, AnyNode]] = []
    aggregated: Counter[tuple[str, str, int, int]] = Counter()
    for _, contact, start_slot, end_slot, key in contact_slots:
        if contact_residual_units is None:
            units = math.floor(
                contact.capacity_bytes * (1.0 - loss_probability) / transfer_size_bytes
            )
        else:
            units = int(contact_residual_units.get(key, 0))
        if units <= 0:
            continue
        aggregated[(contact.node_a, contact.node_b, start_slot, end_slot)] += units
        aggregated[(contact.node_b, contact.node_a, start_slot, end_slot)] += units
    for (source_site, target_site, start_slot, end_slot), units in aggregated.items():
        left = site_node(source_site, start_slot)
        right = site_node(target_site, end_slot)
        # Contacts joining the same slot pair aggregate into one integral edge.
        previous = graph.get_edge_data(left, right, {}).get("capacity", 0)
        graph.add_edge(
            left,
            right,
            capacity=previous + units,
            kind="transfer",
        )
        transfer_edges.append((left, right))

    completion_edges: dict[str, list[tuple[AnyNode, AnyNode]]] = defaultdict(list)
    for demand in demands:
        demand_node = ("demand", demand.demand_id)
        graph.add_edge(demand_node, sink, capacity=demand.value, kind="completion")
        release_slot, deadline_slot = demand_windows[demand.demand_id]
        for time_slot in sorted(events_by_site[demand.site]):
            if not release_slot <= time_slot <= deadline_slot:
                continue
            edge = (site_node(demand.site, time_slot), demand_node)
            graph.add_edge(*edge, capacity=demand.value, kind="serve")
            completion_edges[demand.site].append(edge)

    flow_value, flow = nx.maximum_flow(graph, source, sink, capacity="capacity")
    cut_value, _ = nx.minimum_cut(graph, source, sink, capacity="capacity")
    utilized_transfer = sum(flow.get(left, {}).get(right, 0) for left, right in transfer_edges)
    completion_by_site = {
        site: sum(flow.get(left, {}).get(right, 0) for left, right in edges)
        for site, edges in completion_edges.items()
    }
    total_demand = sum(demand.value for demand in demands)
    return TimeExpandedOracleResult(
        completed_value=int(flow_value),
        initial_budget=budget,
        total_demand_value=total_demand,
        unserved_value=total_demand - int(flow_value),
        utilized_transfer_units=int(utilized_transfer),
        cut_value=int(cut_value),
        time_slots=len(set().union(*(events_by_site[site] for site in relevant_sites))),
        nodes=graph.number_of_nodes(),
        edges=graph.number_of_edges(),
        completion_by_site=completion_by_site,
    )


# Type alias kept below the function-facing dataclasses to avoid leaking a
# NetworkX implementation detail into public annotations.
AnyNode = tuple[object, ...]


def collapsed_time_special_case(
    initial_rights: Mapping[str, int], demand_by_site: Mapping[str, int]
) -> tuple[int, int]:
    """Return static L1 completion and instantaneous-relocation flow optimum.

    The first component is the current paper's placement identity evaluated
    directly.  The second is the collapsed-time max-flow optimum, equal to
    ``min(B, D)`` when relocation is unconstrained.
    """

    rights = _validate_initial_rights(initial_rights)
    sites = tuple(sorted(set(rights) | set(demand_by_site)))
    demand = [int(demand_by_site.get(site, 0)) for site in sites]
    if any(value < 0 for value in demand):
        raise ValueError("demand must be nonnegative")
    static = fallback_completion([rights.get(site, 0) for site in sites], demand)
    return static, min(sum(rights.values()), sum(demand))


def transport_conditioning_gap(
    *, generated_demand: int, arrived_demand: int, conditional_completion: int
) -> int:
    """Demand hidden by conditioning the frontier on network-arrived requests."""

    if not 0 <= conditional_completion <= arrived_demand <= generated_demand:
        raise ValueError("require completion <= arrived <= generated demand")
    return generated_demand - arrived_demand


def ogd_dynamic_regret_bound(
    *,
    horizon: int,
    diameter: float,
    gradient_bound: float,
    comparator_path_variation: float,
    step_size: float | None = None,
) -> float:
    """Projected-OGD dynamic-regret bound for the AdaTransfer target allocator.

    For convex per-epoch loss and a feasible set of diameter ``diameter``, the
    standard telescoping proof gives

    ``(D^2 + 2 D V_T)/(2 eta) + eta G^2 T / 2``.
    """

    if horizon <= 0 or diameter <= 0 or gradient_bound < 0:
        raise ValueError("invalid OGD bound parameters")
    if comparator_path_variation < 0:
        raise ValueError("path variation must be nonnegative")
    numerator = diameter**2 + 2 * diameter * comparator_path_variation
    eta = step_size
    if eta is None:
        eta = math.sqrt(numerator / max(1e-12, gradient_bound**2 * horizon))
    if eta <= 0:
        raise ValueError("step_size must be positive")
    return numerator / (2 * eta) + eta * gradient_bound**2 * horizon / 2


def reserve_separable_competitive_floor(
    *, initial_budget: int, sites: int, reserve_fraction: float
) -> float:
    """Worst-case one-burst competitive floor from immutable per-site reserve.

    Each site retains ``floor(reserve_fraction * B / m)`` units.  An adversary
    concentrating all ``B`` units of demand at the least-provisioned site cannot
    drive completion below that reserve; the bound is tight if all movable
    rights are stranded before the burst.
    """

    if initial_budget <= 0 or sites <= 0 or not 0.0 <= reserve_fraction <= 1.0:
        raise ValueError("invalid reserve parameters")
    reserve = math.floor(reserve_fraction * initial_budget / sites)
    return reserve / initial_budget


def reserve_separable_minimax_certificate(
    *, initial_budget: int, sites: int, reserve_fraction: float
) -> tuple[float, float, bool]:
    """Certify the optimal worst-case floor within reserve-separable policies.

    Fix a total immutable reserve ``floor(rho B)`` and permit an adversary to
    place a one-shot demand burst of value ``B`` at the least-reserved site after
    every movable unit has been stranded.  Pigeonhole gives an upper bound of
    ``floor(floor(rho B)/m)/B`` for every reserve placement.  Balanced issuance
    attains that bound, so the returned lower and upper bounds coincide.
    This is a class-conditional minimax result, not an unrestricted competitive
    ratio against an adversary that can violate the contact or risk model.
    """

    if initial_budget <= 0 or sites <= 0 or not 0.0 <= reserve_fraction <= 1.0:
        raise ValueError("invalid reserve parameters")
    total_reserve = math.floor(reserve_fraction * initial_budget)
    balanced_minimum = total_reserve // sites
    lower = balanced_minimum / initial_budget
    upper = math.floor(total_reserve / sites) / initial_budget
    return lower, upper, math.isclose(lower, upper)


def demand_path_variation(sequence: Iterable[Sequence[float]]) -> float:
    """Return the L1 path variation of a demand/contact target sequence."""

    vectors = [tuple(float(value) for value in vector) for vector in sequence]
    if not vectors:
        return 0.0
    dimension = len(vectors[0])
    if dimension == 0 or any(len(vector) != dimension for vector in vectors):
        raise ValueError("all variation vectors must share a nonzero dimension")
    return sum(
        sum(abs(left - right) for left, right in zip(previous, current, strict=True))
        for previous, current in zip(vectors, vectors[1:], strict=False)
    )
