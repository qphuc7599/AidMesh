from __future__ import annotations

import math

import pytest

from aidmesh.analysis.time_expanded_frontier import (
    TemporalDemand,
    collapsed_time_special_case,
    demand_path_variation,
    ogd_dynamic_regret_bound,
    reserve_separable_competitive_floor,
    reserve_separable_minimax_certificate,
    solve_time_expanded_oracle,
    transport_conditioning_gap,
)
from aidmesh.simulator.dtn import Contact


def test_time_expanded_oracle_moves_only_over_causal_contact() -> None:
    demand = (TemporalDemand("d", "b", 20, 100),)
    no_contact = solve_time_expanded_oracle(
        initial_rights={"a": 1, "b": 0},
        demands=demand,
        contacts=(),
        slot_us=10,
    )
    assert no_contact.completed_value == 0

    with_contact = solve_time_expanded_oracle(
        initial_rights={"a": 1, "b": 0},
        demands=demand,
        contacts=(Contact(0, 10, "a", "b", 1_000_000),),
        slot_us=10,
        transfer_size_bytes=1,
    )
    assert with_contact.completed_value == 1
    assert with_contact.utilized_transfer_units == 1
    assert with_contact.cut_value == 1


def test_deadline_blocks_late_right() -> None:
    result = solve_time_expanded_oracle(
        initial_rights={"a": 1, "b": 0},
        demands=(TemporalDemand("d", "b", 0, 10),),
        contacts=(Contact(20, 30, "a", "b", 1_000_000),),
        slot_us=10,
        transfer_size_bytes=1,
    )
    assert result.completed_value == 0


def test_subslot_window_is_not_dropped_by_oracle_discretization() -> None:
    result = solve_time_expanded_oracle(
        initial_rights={"a": 1},
        demands=(TemporalDemand("d", "a", 15, 19),),
        contacts=(),
        slot_us=10,
    )
    assert result.completed_value == 1


def test_coarse_oracle_relaxes_multiple_subslot_hops_to_preserve_upper_bound() -> None:
    result = solve_time_expanded_oracle(
        initial_rights={"a": 1, "b": 0, "c": 0},
        demands=(TemporalDemand("d", "c", 25, 59),),
        contacts=(
            Contact(10, 20, "a", "b", 1_000_000),
            Contact(30, 40, "b", "c", 1_000_000),
        ),
        slot_us=60,
        transfer_size_bytes=1,
    )
    assert result.completed_value == 1


def test_coarse_oracle_is_explicitly_optimistic_inside_a_slot() -> None:
    result = solve_time_expanded_oracle(
        initial_rights={"a": 1, "b": 0, "c": 0},
        demands=(TemporalDemand("d", "c", 25, 59),),
        contacts=(
            Contact(10, 20, "b", "c", 1_000_000),
            Contact(30, 40, "a", "b", 1_000_000),
        ),
        slot_us=60,
        transfer_size_bytes=1,
    )
    assert result.completed_value == 1


def test_event_respecting_oracle_forbids_reverse_order_contact_chain() -> None:
    result = solve_time_expanded_oracle(
        initial_rights={"a": 1, "b": 0, "c": 0},
        demands=(TemporalDemand("d", "c", 0, 60),),
        contacts=(
            Contact(10, 20, "b", "c", 1_000_000),
            Contact(30, 40, "a", "b", 1_000_000),
        ),
        slot_us=1,
        transfer_size_bytes=1,
        contact_timing="event_respecting",
    )
    assert result.completed_value == 0


def test_event_respecting_oracle_allows_forward_contact_chain() -> None:
    result = solve_time_expanded_oracle(
        initial_rights={"a": 1, "b": 0, "c": 0},
        demands=(TemporalDemand("d", "c", 0, 60),),
        contacts=(
            Contact(10, 20, "a", "b", 1_000_000),
            Contact(30, 40, "b", "c", 1_000_000),
        ),
        slot_us=1,
        transfer_size_bytes=1,
        contact_timing="event_respecting",
    )
    assert result.completed_value == 1


def test_contact_residual_capacity_prices_shared_queue_contention() -> None:
    contact = Contact(0, 1_000_000, "a", "b", 1_000)
    key = "0:0:1000000:a:b"
    demand = (TemporalDemand("d", "b", 1_000_000, 2_000_000),)
    blocked = solve_time_expanded_oracle(
        initial_rights={"a": 1, "b": 0},
        demands=demand,
        contacts=(contact,),
        slot_us=1_000_000,
        contact_residual_units={key: 0},
    )
    open_result = solve_time_expanded_oracle(
        initial_rights={"a": 1, "b": 0},
        demands=demand,
        contacts=(contact,),
        slot_us=1_000_000,
        contact_residual_units={key: 1},
    )
    assert blocked.completed_value == 0
    assert open_result.completed_value == 1


def test_collapsed_time_retains_l1_special_case() -> None:
    static, oracle = collapsed_time_special_case({"a": 8, "b": 2}, {"a": 2, "b": 8})
    assert static == 4
    assert oracle == 10


def test_online_bounds_are_nontrivial_and_validate_inputs() -> None:
    bound = ogd_dynamic_regret_bound(
        horizon=100,
        diameter=1.0,
        gradient_bound=1.0,
        comparator_path_variation=0.0,
    )
    assert math.isclose(bound, 10.0)
    assert (
        reserve_separable_competitive_floor(initial_budget=100, sites=4, reserve_fraction=0.5)
        == 0.12
    )
    lower, upper, optimal = reserve_separable_minimax_certificate(
        initial_budget=100, sites=4, reserve_fraction=0.5
    )
    assert lower == upper == 0.12
    assert optimal
    assert demand_path_variation(((1, 0), (0, 1), (0.5, 0.5))) == 3.0
    assert (
        transport_conditioning_gap(
            generated_demand=100, arrived_demand=60, conditional_completion=50
        )
        == 40
    )
    with pytest.raises(ValueError):
        transport_conditioning_gap(generated_demand=10, arrived_demand=5, conditional_completion=6)
