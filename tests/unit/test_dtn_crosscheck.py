from __future__ import annotations

from aidmesh.experiments.dtn_crosscheck import (
    earliest_intraslot_closure_delivery,
    earliest_temporal_delivery,
)
from aidmesh.simulator.dtn import BundleSpec, Contact, RoutingPolicy, TemporalContactSimulator


def test_same_start_contacts_cannot_create_zero_time_multihop_path() -> None:
    contacts = (
        Contact(0, 10_000_000, "a", "b", 1000),
        Contact(0, 10_000_000, "b", "c", 1000),
    )
    bundle = BundleSpec("x", "a", "c", 0, 100_000_000, 10, 0.5)

    oracle = earliest_temporal_delivery(contacts, bundle)
    result = TemporalContactSimulator(
        contacts=contacts,
        bundles=(bundle,),
        policy=RoutingPolicy.EPIDEMIC,
        buffer_bytes=1000,
    ).run()

    assert not oracle.reachable
    assert earliest_intraslot_closure_delivery(contacts, bundle).reachable
    assert result.delivered == 0


def test_later_contact_matches_earliest_arrival_oracle() -> None:
    contacts = (
        Contact(0, 10_000_000, "a", "b", 1000),
        Contact(20_000_000, 30_000_000, "b", "c", 1000),
    )
    bundle = BundleSpec("x", "a", "c", 0, 100_000_000, 10, 0.5)

    oracle = earliest_temporal_delivery(contacts, bundle)
    result = TemporalContactSimulator(
        contacts=contacts,
        bundles=(bundle,),
        policy=RoutingPolicy.EPIDEMIC,
        buffer_bytes=1000,
    ).run()

    assert oracle.earliest_delivery_us == 30_000_000
    assert result.deliveries[0].delivered_us == 30_000_000
