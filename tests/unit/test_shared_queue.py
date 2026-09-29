from __future__ import annotations

from aidmesh.simulator.dtn import Contact, RoutingPolicy
from aidmesh.simulator.shared_queue import (
    MessageClass,
    SharedBundleSpec,
    SharedTemporalContactSimulator,
    aggregate_contact_residual_units,
)


def _bundle(
    name: str,
    message_class: MessageClass,
    *,
    source: str = "a",
    destination: str = "b",
    created_us: int = 0,
    size_bytes: int = 100,
    risk_score: float = 0.0,
) -> SharedBundleSpec:
    return SharedBundleSpec(
        bundle_id=name,
        message_class=message_class,
        source=source,
        destination=destination,
        created_us=created_us,
        ttl_us=10_000_000,
        size_bytes=size_bytes,
        risk_score=risk_score,
        correlation_id=name,
    )


def test_classes_share_one_bidirectional_contact_budget() -> None:
    contacts = (Contact(0, 1_000_000, "a", "b", 200),)  # 200 bytes, not per direction.
    bundles = (
        _bundle("request", MessageClass.REQUEST, size_bytes=150),
        _bundle(
            "receipt",
            MessageClass.RECEIPT,
            source="b",
            destination="a",
            size_bytes=150,
        ),
    )
    result = SharedTemporalContactSimulator(
        contacts=contacts,
        bundles=bundles,
        buffer_bytes=1_000,
        copy_budget=1,
    ).run()
    assert result.forwarded_bytes == 150
    assert result.delivered_by_class == {MessageClass.REQUEST.value: 1}
    assert sum(result.contact_used_bytes.values()) == 150
    assert next(iter(result.contact_used_bytes_by_class.values())) == {"request": 150}


def test_residual_oracle_can_replace_only_online_transfer_bytes() -> None:
    contacts = (Contact(0, 1_000_000, "a", "b", 300),)
    bundles = (
        _bundle("request", MessageClass.REQUEST, size_bytes=100),
        _bundle("transfer", MessageClass.RIGHT_TRANSFER, size_bytes=100),
    )
    result = SharedTemporalContactSimulator(
        contacts=contacts,
        bundles=bundles,
        buffer_bytes=1_000,
        copy_budget=1,
    ).run()
    ordinary = aggregate_contact_residual_units(result, transfer_size_bytes=100)
    replaced = aggregate_contact_residual_units(
        result,
        transfer_size_bytes=100,
        replace_class=MessageClass.RIGHT_TRANSFER,
    )
    assert next(iter(ordinary.values())) == 1
    assert next(iter(replaced.values())) == 2


def test_class_specific_loss_isolated_from_request_traffic() -> None:
    contacts = (Contact(0, 1_000_000, "a", "b", 1_000),)
    bundles = (
        _bundle("request", MessageClass.REQUEST, size_bytes=100),
        _bundle("transfer", MessageClass.RIGHT_TRANSFER, size_bytes=100),
    )
    result = SharedTemporalContactSimulator(
        contacts=contacts,
        bundles=bundles,
        buffer_bytes=1_000,
        copy_budget=1,
        class_loss_probability={MessageClass.RIGHT_TRANSFER: 1.0},
    ).run()
    assert result.delivered_by_class == {MessageClass.REQUEST.value: 1}
    assert result.dropped_by_class == {MessageClass.RIGHT_TRANSFER.value: 1}


def test_delivery_callback_emits_causal_followup_into_same_queue() -> None:
    contacts = (
        Contact(0, 1_000_000, "client", "site", 1_000),
        Contact(2_000_000, 3_000_000, "site", "gateway", 1_000),
    )
    request = _bundle(
        "request",
        MessageClass.REQUEST,
        source="client",
        destination="site",
        size_bytes=100,
    )

    def callback(record, bundle, _snapshot):
        if bundle.message_class != MessageClass.REQUEST:
            return ()
        return (
            _bundle(
                "receipt",
                MessageClass.RECEIPT,
                source="site",
                destination="gateway",
                created_us=record.delivered_us,
                size_bytes=100,
            ),
        )

    result = SharedTemporalContactSimulator(
        contacts=contacts,
        bundles=(request,),
        buffer_bytes=1_000,
        copy_budget=1,
        on_delivery=callback,
    ).run()
    assert result.created_by_class == {"request": 1, "receipt": 1}
    assert result.delivered_by_class == {"request": 1, "receipt": 1}
    receipt = next(item for item in result.deliveries if item.bundle_id == "receipt")
    assert receipt.created_us == 1_000_000
    assert receipt.delivered_us == 3_000_000


def test_quorum_traffic_preempts_receipt_under_buffer_pressure() -> None:
    bundles = (
        _bundle("receipt", MessageClass.RECEIPT, size_bytes=80),
        _bundle("share", MessageClass.QUORUM_SHARE, size_bytes=80),
    )
    result = SharedTemporalContactSimulator(
        contacts=(Contact(10, 1_000_010, "a", "b", 1_000),),
        bundles=bundles,
        buffer_bytes=100,
        copy_budget=1,
    ).run()
    assert result.dropped_by_class[MessageClass.RECEIPT.value] == 1
    assert result.delivered_by_class[MessageClass.QUORUM_SHARE.value] == 1


def test_local_timer_consumes_no_contact_capacity() -> None:
    timer = _bundle(
        "timer",
        MessageClass.LOCAL_TIMER,
        source="site",
        destination="site",
        created_us=5,
        size_bytes=1,
    )
    result = SharedTemporalContactSimulator(
        contacts=(),
        bundles=(timer,),
        buffer_bytes=100,
        copy_budget=1,
    ).run()
    assert result.delivered == 1
    assert result.forwarded_bytes == 0


def test_risk_priority_orders_same_class_by_risk_score() -> None:
    bundles = (
        _bundle("low-risk", MessageClass.REQUEST, risk_score=0.1),
        _bundle("high-risk", MessageClass.REQUEST, risk_score=0.9),
    )
    result = SharedTemporalContactSimulator(
        contacts=(Contact(0, 1_000_000, "a", "b", 100),),
        bundles=bundles,
        buffer_bytes=1_000,
        copy_budget=4,
        routing_policy=RoutingPolicy.RISK_PRIORITY,
    ).run()
    assert [record.bundle_id for record in result.deliveries] == ["high-risk"]


def test_epidemic_replicates_when_single_copy_spray_wait_cannot() -> None:
    contacts = (
        Contact(0, 1_000_000, "a", "relay", 100),
        Contact(2_000_000, 3_000_000, "relay", "destination", 100),
    )
    bundle = _bundle(
        "multi-hop",
        MessageClass.REQUEST,
        destination="destination",
    )
    spray = SharedTemporalContactSimulator(
        contacts=contacts,
        bundles=(bundle,),
        buffer_bytes=1_000,
        copy_budget=1,
        routing_policy=RoutingPolicy.SPRAY_WAIT,
    ).run()
    epidemic = SharedTemporalContactSimulator(
        contacts=contacts,
        bundles=(bundle,),
        buffer_bytes=1_000,
        copy_budget=2,
        routing_policy=RoutingPolicy.EPIDEMIC,
    ).run()
    assert spray.delivered == 0
    assert epidemic.delivered == 1
