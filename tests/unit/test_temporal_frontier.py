import pandas as pd
import pytest

from aidmesh.experiments.temporal_frontier import (
    TemporalPathIndex,
    _evaluate_method,
    generate_heldout_requests,
    mechanism_summary,
)
from aidmesh.simulator.dtn import Contact


def test_replay_pair_has_one_home_binding() -> None:
    contacts = tuple(
        Contact(index * 20, index * 20 + 10, f"c{index}", "hub", 1000)
        for index in range(20)
    )
    roles = {
        "sites": ("s0", "s1", "s2", "s3"),
        "relays": ("r0", "r1", "r2"),
        "clients": tuple(f"c{index}" for index in range(20)),
    }
    requests = generate_heldout_requests(
        contacts, roles, context="unit", seed=3, cutoff_us=0, ttl_us=1000
    )
    pairs = {}
    for request in requests:
        if request.nullifier.startswith("dup-"):
            pairs.setdefault(request.nullifier, []).append(request)
    assert pairs
    assert all(len({request.home_site for request in pair}) == 1 for pair in pairs.values())
    assert all(
        len({request.bundle.destination for request in pair}) == 2
        for pair in pairs.values()
    )


def test_conserved_methods_never_exceed_cap() -> None:
    contacts = (
        Contact(0, 10, "s0", "s1", 1000),
        Contact(20, 30, "s1", "s2", 1000),
        Contact(40, 50, "s2", "s3", 1000),
    )
    roles = {
        "sites": ("s0", "s1", "s2", "s3"),
        "relays": ("r0", "r1", "r2"),
        "clients": tuple(f"c{index}" for index in range(12)),
    }
    activity = tuple(
        Contact(100 + index, 101 + index, client, "relay", 1000)
        for index, client in enumerate(roles["clients"])
    )
    requests = generate_heldout_requests(
        activity, roles, context="unit", seed=4, cutoff_us=0, ttl_us=1000
    )
    deliveries = {request.bundle.bundle_id: request.bundle.created_us for request in requests}
    path_index = TemporalPathIndex(contacts, epoch_us=1)
    for method in (
        "fixed_home",
        "static_rights",
        "optimized_static",
        "periodic_sync",
        "transferable_rights",
    ):
        result = _evaluate_method(
            requests,
            deliveries,
            method=method,
            cap=4,
            sites=roles["sites"],
            path_index=path_index,
            gateway="r0",
        )
        assert result["cap_violation"] == 0
        assert result["completed"] <= 4


def test_quantitative_identity_matches_temporal_rights_methods() -> None:
    sites = ("s0", "s1", "s2", "s3")
    contacts = (
        Contact(0, 1, "s0", "s1", 1000),
        Contact(2, 3, "s1", "s2", 1000),
        Contact(4, 5, "s2", "s3", 1000),
    )
    roles = {
        "sites": sites,
        "relays": ("r0", "r1", "r2"),
        "clients": tuple(f"c{index}" for index in range(16)),
    }
    activity = tuple(
        Contact(100 + index, 101 + index, client, "relay", 1000)
        for index, client in enumerate(roles["clients"])
    )
    requests = generate_heldout_requests(
        activity, roles, context="identity", seed=11, cutoff_us=0, ttl_us=1000
    )
    deliveries = {request.bundle.bundle_id: request.bundle.created_us for request in requests}
    path_index = TemporalPathIndex(contacts, epoch_us=1)

    for method in ("static_rights", "optimized_static", "transferable_rights"):
        result = _evaluate_method(
            requests,
            deliveries,
            method=method,
            cap=8,
            sites=sites,
            path_index=path_index,
            gateway="r0",
        )
        assert result["identity_matches_observed"] is True
        assert result["identity_completion"] == result["completed"]
        assert result["delivered_budget"] + result["stranded_rights"] == 8


def test_quantitative_identity_includes_safe_quorum_completion() -> None:
    sites = ("s0", "s1", "s2", "s3")
    roles = {
        "sites": sites,
        "relays": ("r0", "r1", "r2"),
        "clients": tuple(f"c{index}" for index in range(12)),
    }
    activity = tuple(
        Contact(100 + index, 101 + index, client, "relay", 1000)
        for index, client in enumerate(roles["clients"])
    )
    requests = generate_heldout_requests(
        activity, roles, context="quorum-identity", seed=5, cutoff_us=0, ttl_us=1000
    )
    deliveries = {request.bundle.bundle_id: request.bundle.created_us for request in requests}
    result = _evaluate_method(
        requests,
        deliveries,
        method="transferable_rights",
        cap=4,
        sites=sites,
        path_index=TemporalPathIndex((), epoch_us=1),
        gateway="r0",
        quorum_probability=1.0,
        experiment_id="unit-quorum",
    )
    assert result["quorum_completed"] > 0
    assert result["cap_violation"] == 0
    assert result["identity_completion"] == result["completed"]


def test_mechanism_summary_rejects_an_identity_failure() -> None:
    frame = pd.DataFrame(
        [
            {
                "context": "x",
                "method": "transferable_rights",
                "identity_matches_observed": False,
                "initial_placement_l1": 4,
                "delivered_placement_l1": 2,
                "stranded_rights": 0,
                "transfer_criterion_margin": 2,
                "identity_gain_vs_equal_static": 1,
                "generated_requests": 10,
            }
        ]
    )
    with pytest.raises(ValueError, match="identity failed"):
        mechanism_summary(frame)
