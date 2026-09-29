from __future__ import annotations

from aidmesh.simulator.dtn import (
    HOUR_US,
    RoutingPolicy,
    TemporalContactSimulator,
    generate_receipt_bundles,
    generate_synthetic_contacts,
)


def test_temporal_contact_dtn_is_deterministic() -> None:
    contacts = generate_synthetic_contacts(
        seed=11,
        sites=4,
        relays=2,
        horizon_us=12 * HOUR_US,
        partition_us=2 * HOUR_US,
    )
    bundles = generate_receipt_bundles(
        seed=11,
        count=50,
        sites=4,
        horizon_us=12 * HOUR_US,
        ttl_us=10 * HOUR_US,
    )
    first = TemporalContactSimulator(
        contacts=contacts,
        bundles=bundles,
        policy=RoutingPolicy.RISK_PRIORITY,
        buffer_bytes=48 * 1024,
        copy_budget=4,
    ).run()
    second = TemporalContactSimulator(
        contacts=contacts,
        bundles=bundles,
        policy=RoutingPolicy.RISK_PRIORITY,
        buffer_bytes=48 * 1024,
        copy_budget=4,
    ).run()

    assert first.event_checksum == second.event_checksum
    assert 0 <= first.delivery_ratio <= 1
    assert first.forwarded_bytes >= first.delivered * 1024


def test_risk_priority_does_not_deliver_less_than_fifo_on_same_trace() -> None:
    contacts = generate_synthetic_contacts(
        seed=17,
        sites=5,
        relays=2,
        horizon_us=18 * HOUR_US,
        partition_us=3 * HOUR_US,
    )
    bundles = generate_receipt_bundles(
        seed=17,
        count=80,
        sites=5,
        horizon_us=18 * HOUR_US,
        ttl_us=12 * HOUR_US,
    )
    fifo = TemporalContactSimulator(
        contacts=contacts,
        bundles=bundles,
        policy=RoutingPolicy.FIFO,
        buffer_bytes=32 * 1024,
        copy_budget=1,
    ).run()
    priority = TemporalContactSimulator(
        contacts=contacts,
        bundles=bundles,
        policy=RoutingPolicy.RISK_PRIORITY,
        buffer_bytes=32 * 1024,
        copy_budget=4,
    ).run()

    assert priority.delivered >= fifo.delivered
