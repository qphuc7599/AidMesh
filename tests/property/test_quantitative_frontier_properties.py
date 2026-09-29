from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aidmesh.analysis.quantitative_frontier import (
    causal_policy_objective,
    completion_from_identity,
    demand_drift_bound,
    fallback_completion,
    l1_misplacement,
    oracle_completion,
    placement_gap,
    relocation_gain,
    relocation_gain_from_identity,
    relocation_strictly_improves,
)

vectors = st.lists(st.integers(min_value=0, max_value=50), min_size=1, max_size=8)


@settings(max_examples=1000, deadline=None)
@given(capacity=vectors, demand=vectors)
def test_exact_fallback_completion_identity(capacity: list[int], demand: list[int]) -> None:
    if len(capacity) != len(demand):
        return
    direct = fallback_completion(capacity, demand)
    assert direct == completion_from_identity(capacity, demand)
    assert direct <= oracle_completion(capacity, demand)
    assert placement_gap(capacity, demand) == oracle_completion(capacity, demand) - direct
    expected_gap_numerator = l1_misplacement(capacity, demand) - abs(
        sum(capacity) - sum(demand)
    )
    assert expected_gap_numerator % 2 == 0
    assert placement_gap(capacity, demand) == expected_gap_numerator // 2


@settings(max_examples=1000, deadline=None)
@given(static=vectors, delivered=vectors, demand=vectors)
def test_causal_relocation_criterion_is_exact(
    static: list[int], delivered: list[int], demand: list[int]
) -> None:
    if len(static) != len(delivered) or len(static) != len(demand):
        return
    if sum(delivered) > sum(static):
        with pytest.raises(ValueError):
            relocation_gain_from_identity(static, delivered, demand)
        return
    direct = relocation_gain(static, delivered, demand)
    identity = relocation_gain_from_identity(static, delivered, demand)
    assert direct == identity
    assert relocation_strictly_improves(static, delivered, demand) is (direct > 0)


@settings(max_examples=1000, deadline=None)
@given(first=vectors, second=vectors, demand=vectors, extra=st.integers(0, 50))
def test_causal_policy_objective_gives_exact_pairwise_regret(
    first: list[int], second: list[int], demand: list[int], extra: int
) -> None:
    if len(first) != len(second) or len(first) != len(demand):
        return
    budget = max(sum(first), sum(second)) + extra
    direct = fallback_completion(first, demand) - fallback_completion(second, demand)
    objective = causal_policy_objective(
        budget, second, demand
    ) - causal_policy_objective(budget, first, demand)
    assert objective % 2 == 0
    assert direct == objective // 2


@settings(max_examples=1000, deadline=None)
@given(
    first=vectors,
    second=vectors,
    estimated_demand=vectors,
    actual_demand=vectors,
)
def test_static_demand_drift_regret_bound(
    first: list[int],
    second: list[int],
    estimated_demand: list[int],
    actual_demand: list[int],
) -> None:
    if not (
        len(first)
        == len(second)
        == len(estimated_demand)
        == len(actual_demand)
    ):
        return
    if fallback_completion(first, estimated_demand) < fallback_completion(
        second, estimated_demand
    ):
        first, second = second, first
    regret = fallback_completion(second, actual_demand) - fallback_completion(
        first, actual_demand
    )
    assert regret <= demand_drift_bound(estimated_demand, actual_demand)


def test_relocation_examples_cover_help_harm_and_stranding() -> None:
    demand = [2, 8]
    static = [8, 2]
    assert relocation_gain(static, [2, 8], demand) == 6
    assert relocation_strictly_improves(static, [2, 8], demand)

    calibrated = [2, 8]
    assert relocation_gain(calibrated, [5, 5], demand) == -3
    assert not relocation_strictly_improves(calibrated, [5, 5], demand)

    # One right is removed before delivery and stranded.  Misplacement falls
    # enough that relocation still gains two completions after the loss.
    assert relocation_gain([5, 0], [2, 2], [0, 5]) == 2
    assert relocation_gain_from_identity([5, 0], [2, 2], [0, 5]) == 2


@pytest.mark.parametrize(
    ("capacity", "demand"),
    [([], [1]), ([1], []), ([-1], [1]), ([1], [-1]), ([1, 2], [1])],
)
def test_invalid_frontier_vectors_are_rejected(
    capacity: list[int], demand: list[int]
) -> None:
    with pytest.raises(ValueError):
        fallback_completion(capacity, demand)
