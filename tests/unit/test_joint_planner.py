from __future__ import annotations

from aidmesh.allocator import (
    compute_allocation_metrics,
    maximin_then_utility,
    utility_only,
)
from aidmesh.experiments.allocation import generate_problem


def test_joint_planner_preserves_utility_and_positive_worst_group_service() -> None:
    problem = generate_problem(77, groups=12, categories=3, stock_sufficiency=0.6)
    joint = maximin_then_utility(problem)
    utility = utility_only(problem)
    joint_metrics = compute_allocation_metrics(joint)
    utility_metrics = compute_allocation_metrics(utility)
    joint.assert_feasible()
    assert joint_metrics.minimum_service_ratio is not None
    assert joint_metrics.minimum_service_ratio > 0
    assert joint_metrics.equivalent_fulfilled >= utility_metrics.equivalent_fulfilled * 0.999999
