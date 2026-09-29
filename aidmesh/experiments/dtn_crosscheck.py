"""Independent temporal reachability oracle for DTN simulator cross-checks."""

from __future__ import annotations

import csv
import math
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from aidmesh.simulator.dtn import (
    BundleSpec,
    Contact,
    RoutingPolicy,
    TemporalContactSimulator,
    load_bundles_csv,
    load_contacts_csv,
)


@dataclass(frozen=True)
class ReachabilityRecord:
    bundle_id: str
    reachable: bool
    earliest_delivery_us: int | None


def earliest_temporal_delivery(
    contacts: tuple[Contact, ...], bundle: BundleSpec
) -> ReachabilityRecord:
    """Compute earliest store-carry-forward arrival under slot-start causality.

    Contacts sharing one start timestamp are evaluated from the same pre-slot
    state.  A bundle received at contact end can only be sent at a later contact
    start.  Capacity and buffers are intentionally unbounded in this oracle.
    """

    arrival: dict[str, int] = {bundle.source: bundle.created_us}
    by_start: dict[int, list[Contact]] = defaultdict(list)
    for contact in contacts:
        by_start[contact.start_us].append(contact)
    for start_us, group in sorted(by_start.items()):
        if start_us < bundle.created_us:
            continue
        before = dict(arrival)
        updates: dict[str, int] = {}
        for contact in group:
            if contact.end_us > bundle.expires_us:
                continue
            for sender, receiver in (
                (contact.node_a, contact.node_b),
                (contact.node_b, contact.node_a),
            ):
                if before.get(sender, math.inf) > contact.start_us:
                    continue
                current = min(arrival.get(receiver, math.inf), updates.get(receiver, math.inf))
                if contact.end_us < current:
                    updates[receiver] = contact.end_us
        for node, delivered_us in updates.items():
            arrival[node] = min(arrival.get(node, math.inf), delivered_us)
    delivered_us = arrival.get(bundle.destination)
    return ReachabilityRecord(
        bundle.bundle_id,
        delivered_us is not None,
        delivered_us,
    )


def earliest_intraslot_closure_delivery(
    contacts: tuple[Contact, ...], bundle: BundleSpec
) -> ReachabilityRecord:
    """Reachability when simultaneous sampled contacts permit intra-slot relaying.

    This is the alternate semantics implemented by a sufficiently fine-grained
    continuous-transfer simulator: a small message may traverse a connected
    component of contacts sharing one sample timestamp.  It is kept separate
    from :func:`earliest_temporal_delivery`, whose snapshot semantics avoid
    inventing an ordering inside a proximity-sampling slot.
    """

    arrival: dict[str, int] = {bundle.source: bundle.created_us}
    by_start: dict[int, list[Contact]] = defaultdict(list)
    for contact in contacts:
        by_start[contact.start_us].append(contact)
    for start_us, group in sorted(by_start.items()):
        if start_us < bundle.created_us:
            continue
        adjacency: dict[str, set[str]] = defaultdict(set)
        end_by_node: dict[str, int] = {}
        for contact in group:
            if contact.end_us > bundle.expires_us:
                continue
            adjacency[contact.node_a].add(contact.node_b)
            adjacency[contact.node_b].add(contact.node_a)
            end_by_node[contact.node_a] = max(
                end_by_node.get(contact.node_a, 0), contact.end_us
            )
            end_by_node[contact.node_b] = max(
                end_by_node.get(contact.node_b, 0), contact.end_us
            )
        frontier = [
            node
            for node, arrived_us in arrival.items()
            if arrived_us <= start_us and node in adjacency
        ]
        reached = set(frontier)
        while frontier:
            sender = frontier.pop()
            for receiver in adjacency[sender]:
                if receiver not in reached:
                    reached.add(receiver)
                    frontier.append(receiver)
        for node in reached:
            arrival[node] = min(arrival.get(node, math.inf), end_by_node[node])
        delivered_us = arrival.get(bundle.destination)
        if delivered_us is not None:
            return ReachabilityRecord(bundle.bundle_id, True, delivered_us)
    return ReachabilityRecord(bundle.bundle_id, False, None)


def _unbounded_capacity_contacts(
    contacts: tuple[Contact, ...], bundles: tuple[BundleSpec, ...]
) -> tuple[Contact, ...]:
    total_bytes = max(1, sum(bundle.size_bytes for bundle in bundles))
    converted: list[Contact] = []
    for contact in contacts:
        duration_seconds = max(1e-6, (contact.end_us - contact.start_us) / 1_000_000)
        required_bps = math.ceil(total_bytes / duration_seconds) + 1
        converted.append(
            Contact(
                contact.start_us,
                contact.end_us,
                contact.node_a,
                contact.node_b,
                required_bps,
            )
        )
    return tuple(converted)


def run_temporal_reachability_crosscheck(
    contacts_path: Path,
    bundles_path: Path,
) -> tuple[pd.DataFrame, dict[str, int | bool]]:
    """Compare independent oracle reachability with unbounded Epidemic routing."""

    contacts = load_contacts_csv(contacts_path)
    bundles = load_bundles_csv(bundles_path)
    oracle = {
        record.bundle_id: record
        for record in (earliest_temporal_delivery(contacts, bundle) for bundle in bundles)
    }
    nodes = {node for contact in contacts for node in (contact.node_a, contact.node_b)}
    unbounded_contacts = _unbounded_capacity_contacts(contacts, bundles)
    result = TemporalContactSimulator(
        contacts=unbounded_contacts,
        bundles=bundles,
        policy=RoutingPolicy.EPIDEMIC,
        buffer_bytes=max(1, sum(bundle.size_bytes for bundle in bundles) * max(1, len(nodes))),
        copy_budget=1,
    ).run()
    simulated = {record.bundle_id: record for record in result.deliveries}
    rows: list[dict] = []
    for bundle in bundles:
        expected = oracle[bundle.bundle_id]
        actual = simulated.get(bundle.bundle_id)
        rows.append(
            {
                "bundle_id": bundle.bundle_id,
                "oracle_reachable": expected.reachable,
                "oracle_earliest_delivery_us": expected.earliest_delivery_us,
                "simulator_delivered": actual is not None,
                "simulator_delivery_us": actual.delivered_us if actual else None,
                "delivery_agreement": expected.reachable == (actual is not None),
                "time_agreement": (
                    expected.earliest_delivery_us == actual.delivered_us
                    if expected.reachable and actual is not None
                    else expected.reachable == (actual is not None)
                ),
            }
        )
    frame = pd.DataFrame(rows)
    summary: dict[str, int | bool] = {
        "bundles": len(frame),
        "delivery_disagreements": int((~frame["delivery_agreement"]).sum()),
        "time_disagreements": int((~frame["time_agreement"]).sum()),
        "all_delivery_agree": bool(frame["delivery_agreement"].all()),
        "all_times_agree": bool(frame["time_agreement"].all()),
    }
    return frame, summary


def write_temporal_reachability_crosscheck(
    frame: pd.DataFrame,
    summary: dict[str, int | bool],
    output: Path,
) -> dict[str, Path]:
    output.mkdir(parents=True, exist_ok=True)
    rows_path = output / "temporal_reachability_rows.csv"
    summary_path = output / "temporal_reachability_summary.csv"
    frame.to_csv(rows_path, index=False)
    with summary_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summary))
        writer.writeheader()
        writer.writerow(summary)
    return {"rows": rows_path, "summary": summary_path}
