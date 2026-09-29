from __future__ import annotations

import math

import pytest

from aidmesh.policy.adatransfer import AdaTransferAllocator, project_simplex_with_floor


def test_projection_preserves_budget_and_floor() -> None:
    projected = project_simplex_with_floor((8.0, -2.0, 1.0), budget=12.0, floor=2.0)
    assert math.isclose(sum(projected), 12.0)
    assert min(projected) >= 2.0


def test_target_update_moves_toward_observed_demand_without_breaking_reserve() -> None:
    allocator = AdaTransferAllocator(
        sites=4,
        budget=40,
        initial_target=(10.0, 10.0, 10.0, 10.0),
        reserve_fraction=0.5,
        step_size=2.0,
    )
    target = allocator.update_target((1.0, 1.0, 1.0, 20.0))
    assert target[3] > 10.0
    assert min(target) >= 5.0
    assert math.isclose(sum(target), 40.0)


def test_confidence_gate_approves_only_reliable_low_pressure_transfer() -> None:
    allocator = AdaTransferAllocator(
        sites=2,
        budget=20,
        initial_target=(2.0, 18.0),
        reserve_fraction=0.2,
        confidence_delta=0.05,
    )
    approved = allocator.decide(
        target_index=1,
        balances=(15, 0),
        observed_samples=10_000,
        path_reliability=(1.0, 1.0),
        queue_pressure=0.0,
        safe_path_pressure=0.0,
    )
    assert approved.approved
    assert approved.donor_index == 0

    rejected = allocator.decide(
        target_index=1,
        balances=(15, 0),
        observed_samples=2,
        path_reliability=(0.1, 0.1),
        queue_pressure=0.9,
        safe_path_pressure=0.9,
    )
    assert not rejected.approved


def test_expected_value_gate_is_active_but_rejects_unreliable_paths() -> None:
    allocator = AdaTransferAllocator(
        sites=2,
        budget=20,
        initial_target=(2.0, 18.0),
        reserve_fraction=0.2,
        gate_mode="expected_value",
    )
    approved = allocator.decide(
        target_index=1,
        balances=(15, 0),
        observed_samples=2,
        path_reliability=(0.9, 1.0),
        queue_pressure=0.2,
        safe_path_pressure=0.1,
    )
    assert approved.approved
    rejected = allocator.decide(
        target_index=1,
        balances=(15, 0),
        observed_samples=10_000,
        path_reliability=(0.2, 1.0),
        queue_pressure=0.0,
        safe_path_pressure=0.0,
    )
    assert not rejected.approved


def test_bad_projection_is_rejected() -> None:
    with pytest.raises(ValueError):
        project_simplex_with_floor((1.0, 2.0), budget=1.0, floor=1.0)
