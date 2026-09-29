from __future__ import annotations

from aidmesh.experiments.bow_temporal import (
    CausalCampaignConfig,
    calibration_path_reliability,
    evaluate_causal_policy,
)
from aidmesh.experiments.temporal_frontier import HeldoutRequest
from aidmesh.simulator.dtn import BundleSpec, Contact

RATE = 1_000_000_000


def test_local_pull_requires_outbound_request_before_donor_can_emit():
    contacts = (Contact(0, 1, "c", "s1", RATE), Contact(4, 5, "s0", "s1", RATE))
    result = evaluate_causal_policy(
        contacts=contacts, requests=(_request("r", client="c", site="s1"),),
        sites=("s0", "s1"), config=_config("local_pull", cap=1), compute_oracle=False)
    events = result["local_control_events"]
    assert [event["event"] for event in events] == ["pull_sent", "pull_received", "donor_debit"]
    assert events[-1]["time_us"] == 5
    assert result["completed"] == 0  # No return contact after the pull delivery.
    assert result["transfers_stranded"] == 1
    assert result["rights_conservation_holds"]


def test_local_pull_positive_control_completes_with_a_return_contact():
    contacts = (Contact(0, 1, "c", "s1", RATE), Contact(4, 5, "s0", "s1", RATE),
                Contact(6, 7, "s0", "s1", RATE), Contact(8, 9, "s1", "g", RATE))
    request = (_request("r", client="c", site="s1"),)
    baseline = evaluate_causal_policy(contacts=contacts, requests=request, sites=("s0", "s1"),
                                     config=_config("robust_static", cap=1), compute_oracle=False)
    result = evaluate_causal_policy(contacts=contacts, requests=request, sites=("s0", "s1"),
                                   config=_config("local_pull", cap=1), compute_oracle=False)
    assert baseline["completed"] == 0
    assert result["unique_completed"] == 1
    assert result["forwarded_bytes_by_class"]["right_pull"] == 20
    assert result["rights_conservation_holds"]


def test_calibration_reliability_respects_contact_order() -> None:
    # The aggregate graph is connected, but A cannot traverse the B--C
    # contact that ended before A reaches B.  The reverse direction is valid.
    contacts = (
        Contact(5, 6, "b", "c", RATE),
        Contact(10, 11, "a", "b", RATE),
    )
    reliability = calibration_path_reliability(
        contacts,
        sites=("a", "c"),
        cutoff_us=20,
        window_us=20,
    )
    assert reliability["a", "c"] == 0.0
    assert reliability["c", "a"] == 1.0


def _request(
    name: str,
    *,
    client: str,
    site: str,
    home: str = "s0",
    nullifier: str | None = None,
    ttl_us: int = 100,
) -> HeldoutRequest:
    return HeldoutRequest(
        bundle=BundleSpec(
            bundle_id=name,
            source=client,
            destination=site,
            created_us=0,
            ttl_us=ttl_us,
            size_bytes=100,
            risk_score=1.0,
        ),
        home_site=home,
        nullifier=nullifier or name,
    )


def _config(
    policy: str,
    *,
    cap: int = 2,
    availability: float = 0.0,
    quorum_timeout_us: int = 2,
):
    return CausalCampaignConfig(
        cap=cap,
        policy=policy,
        gateway="g",
        witnesses=("w0", "w1"),
        quorum_threshold=2,
        quorum_availability=availability,
        quorum_timeout_us=quorum_timeout_us,
        sync_period_us=1,
        buffer_bytes=10_000,
        copy_budget=1,
        request_bytes=100,
        quorum_request_bytes=20,
        quorum_share_bytes=20,
        transfer_bytes=20,
        periodic_sync_bytes=20,
        receipt_bytes=20,
        oracle_slot_us=1,
        experiment_id=f"test:{policy}:{availability}",
    )


def test_quorum_is_causal_and_does_not_consume_risk_budget() -> None:
    contacts = (
        Contact(0, 1, "c", "s0", RATE),
        Contact(2, 3, "s0", "w0", RATE),
        Contact(4, 5, "s0", "w1", RATE),
        Contact(6, 7, "w0", "s0", RATE),
        Contact(8, 9, "w1", "s0", RATE),
        Contact(10, 11, "s0", "g", RATE),
    )
    result = evaluate_causal_policy(
        contacts=contacts,
        requests=(_request("r", client="c", site="s0"),),
        sites=("s0", "s1"),
        config=_config("equal_static", availability=1.0, quorum_timeout_us=20),
    )
    assert result["completed"] == 1
    assert result["quorum_completed"] == 1
    assert result["fallback_completed"] == 0
    assert result["receipts_delivered"] == 1
    assert result["rights_conservation_holds"] is True


def test_transfer_debits_before_shared_queue_delivery_and_preserves_cap() -> None:
    contacts = (
        Contact(0, 1, "c", "s1", RATE),
        Contact(4, 5, "s0", "s1", RATE),
        Contact(6, 7, "s1", "g", RATE),
    )
    result = evaluate_causal_policy(
        contacts=contacts,
        requests=(_request("r", client="c", site="s1"),),
        sites=("s0", "s1"),
        config=_config("myopic_transfer", cap=1),
    )
    assert result["transfers_started"] == 1
    assert result["transfers_delivered"] == 1
    assert result["fallback_completed"] == 1
    assert result["risk_cap_violation"] == 0
    assert result["rights_conservation_holds"] is True
    assert result["forwarded_bytes_by_class"]["right_transfer"] == 20


def test_cross_site_replays_are_bounded_by_issued_honest_site_rights() -> None:
    contacts = (
        Contact(0, 1, "c0", "s0", RATE),
        Contact(0, 1, "c1", "s1", RATE),
        Contact(10, 11, "s0", "g", RATE),
        Contact(10, 11, "s1", "g", RATE),
    )
    requests = (
        _request("r0", client="c0", site="s0", nullifier="n"),
        _request("r1", client="c1", site="s1", nullifier="n"),
    )
    result = evaluate_causal_policy(
        contacts=contacts,
        requests=requests,
        sites=("s0", "s1"),
        config=_config("equal_static", cap=2),
    )
    assert result["completed"] == 2
    assert result["duplicate_exposure"] == 1
    assert result["fallback_completed"] <= result["honest_site_risk_cap"]
    assert result["risk_cap_violation"] == 0


def test_periodic_sync_requires_a_causal_round_trip() -> None:
    contacts = (
        Contact(0, 1, "c", "s0", RATE),
        Contact(4, 5, "s0", "g", RATE),
        Contact(6, 7, "g", "s0", RATE),
        Contact(8, 9, "s0", "g", RATE),
    )
    result = evaluate_causal_policy(
        contacts=contacts,
        requests=(_request("r", client="c", site="s0"),),
        sites=("s0", "s1"),
        config=_config("periodic_sync", cap=1),
    )
    assert result["sync_attempts"] == 1
    assert result["sync_successes"] == 1
    assert result["periodic_sync_completed"] == 1
    assert result["receipts_delivered"] == 1


def test_background_recovery_traffic_shares_the_same_capacity() -> None:
    contacts = (
        Contact(0, 10, "c", "s0", 100),
        Contact(10, 20, "s0", "g", 100),
    )
    config = _config("equal_static", cap=2)
    config = CausalCampaignConfig(
        **{
            **config.__dict__,
            "background_bundles_per_hour": 1.0,
            "background_bundle_bytes": 100,
            "background_ttl_us": 20,
        }
    )
    result = evaluate_causal_policy(
        contacts=contacts,
        requests=(_request("r", client="c", site="s0", ttl_us=20),),
        sites=("s0", "s1"),
        config=config,
        compute_oracle=False,
    )
    assert result["background_bundles_created"] == 1
    assert "recovery" in result["created_by_class"]


def test_reclaimable_transfer_requires_a_certified_accept() -> None:
    contacts = (
        Contact(0, 1, "c", "s1", RATE),
        Contact(4, 5, "s0", "s1", RATE),
        Contact(6, 7, "s1", "w0", RATE),
        Contact(8, 9, "s1", "w1", RATE),
        Contact(10, 11, "w0", "s1", RATE),
        Contact(12, 13, "w1", "s1", RATE),
        Contact(14, 15, "s1", "g", RATE),
    )
    config = _config("reclaimable_transfer", cap=1, availability=0.0)
    config = CausalCampaignConfig(
        **{
            **config.__dict__,
            "reclaim_delay_us": 20,
            "adatransfer_gate_mode": "expected_value",
            "transfer_witness_availability": 1.0,
        }
    )
    result = evaluate_causal_policy(
        contacts=contacts,
        requests=(_request("r", client="c", site="s1"),),
        sites=("s0", "s1"),
        config=config,
        path_reliability={(left, right): 1.0 for left in ("s0", "s1") for right in ("s0", "s1")},
        compute_oracle=False,
    )
    assert result["transfers_started"] == 1
    assert result["transfers_accepted"] == 1
    assert result["transfers_reclaimed"] == 0
    assert result["transfers_locked_final"] == 0
    assert result["fallback_completed"] == 1
    assert result["rights_conservation_holds"] is True


def test_certified_reclaim_returns_right_for_later_source_demand() -> None:
    contacts = (
        Contact(0, 1, "c1", "s1", RATE),
        Contact(4, 5, "c0", "s0", RATE),
        Contact(6, 7, "s0", "w0", RATE),
        Contact(8, 9, "s0", "w1", RATE),
        Contact(10, 11, "w0", "s0", RATE),
        Contact(12, 13, "w1", "s0", RATE),
        Contact(14, 15, "s0", "g", RATE),
    )
    config = _config("reclaimable_transfer", cap=1, availability=0.0)
    config = CausalCampaignConfig(
        **{
            **config.__dict__,
            "reclaim_delay_us": 2,
            "adatransfer_gate_mode": "expected_value",
            "transfer_witness_availability": 1.0,
        }
    )
    result = evaluate_causal_policy(
        contacts=contacts,
        requests=(
            _request("remote", client="c1", site="s1"),
            _request("local", client="c0", site="s0"),
        ),
        sites=("s0", "s1"),
        config=config,
        path_reliability={(left, right): 1.0 for left in ("s0", "s1") for right in ("s0", "s1")},
        compute_oracle=False,
    )
    assert result["transfers_started"] == 1
    assert result["transfers_delivered"] == 0
    assert result["transfers_reclaimed"] == 1
    assert result["transfers_locked_final"] == 0
    assert result["fallback_completed"] == 1
    assert result["rights_conservation_holds"] is True


def test_v2_recovery_releases_an_uncommitted_accept_prepare() -> None:
    contacts = (
        Contact(0, 1, "c1", "s1", RATE),
        Contact(4, 5, "s0", "s1", RATE),
        Contact(6, 7, "s1", "w0", RATE),
        Contact(8, 9, "w0", "s1", RATE),
        Contact(12, 13, "s0", "w0", RATE),
        Contact(14, 15, "s0", "w1", RATE),
        Contact(16, 17, "w0", "s0", RATE),
        Contact(18, 19, "w1", "s0", RATE),
    )
    config = _config("reclaimable_transfer_v2", cap=1, availability=0.0)
    config = CausalCampaignConfig(
        **{
            **config.__dict__,
            "reclaim_delay_us": 8,
            "adatransfer_gate_mode": "expected_value",
            "transfer_witness_availability": 1.0,
        }
    )
    result = evaluate_causal_policy(
        contacts=contacts,
        requests=(_request("remote", client="c1", site="s1"),),
        sites=("s0", "s1"),
        config=config,
        path_reliability={(left, right): 1.0 for left in ("s0", "s1") for right in ("s0", "s1")},
        compute_oracle=False,
    )
    assert result["transfers_started"] == 1
    assert result["transfers_delivered"] == 1
    assert result["transfers_accepted"] == 0
    assert result["transfers_reclaimed"] == 1
    assert result["transfers_locked_final"] == 0
    assert result["rights_conservation_holds"] is True
