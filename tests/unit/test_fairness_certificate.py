from dataclasses import replace
from fractions import Fraction

import pytest

from aidmesh.allocator.certificate import certify_max_min, certify_rounding
from aidmesh.allocator.progressive_fill import (
    AllocationInvariantError,
    PackingProblem,
    progressive_fill,
    round_allocation,
)


def problem():
    return PackingProblem(
        "z",
        {"a": {"k": 10}, "b": {"k": 10}},
        {"k": 10},
        {"a": 1, "b": 1},
        {"k": 1},
        {"a": {"k": 1}, "b": {"k": 1}},
    )


def test_independent_certificate_accepts_fair_and_rejects_feasible_unfair():
    p = problem()
    result = progressive_fill(p)
    assert len(certify_max_min(p, result)) == 2
    unfair = replace(
        result,
        ratios={"a": Fraction(1, 10), "b": Fraction(9, 10)},
        amounts={"a": {"k": Fraction(1)}, "b": {"k": Fraction(9)}},
    )
    unfair.assert_feasible()
    with pytest.raises(AllocationInvariantError, match="max-min"):
        certify_max_min(p, unfair)


def test_certificate_uses_problem_not_self_reported_stock():
    p = problem()
    result = progressive_fill(p)
    with pytest.raises(AllocationInvariantError):
        certify_max_min(replace(p, stock={"k": 9}), result)


def test_rounding_checker_rejects_feasible_excessive_loss():
    p = problem()
    result = progressive_fill(p)
    rounded = round_allocation(result, seed="fixed")
    assert certify_rounding(p, result, rounded)
    unfair = replace(rounded, amounts={"a": {"k": Fraction(0)}, "b": {"k": Fraction(5)}})
    with pytest.raises(AllocationInvariantError, match="additive"):
        certify_rounding(p, result, unfair)
