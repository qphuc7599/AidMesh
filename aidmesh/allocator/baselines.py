"""Reproducible allocation baselines sharing AidMesh's exact input schema.

The three baselines deliberately use the same demand and stock caps as the
proved allocator but optimize different objectives.  Keeping them in the same
schema prevents comparison bugs caused by silently changing cells, categories,
or conversion factors between methods.
"""

from __future__ import annotations

from collections.abc import Mapping
from fractions import Fraction

from .progressive_fill import (
    AllocationInputError,
    AllocationMatrix,
    PackingProblem,
    RationalLike,
    ValidatedPackingProblem,
    _matrix_from_normalized,
    as_fraction,
    normalize_problem,
)


def _zero_matrix(
    problem: ValidatedPackingProblem,
) -> dict[str, dict[str, Fraction]]:
    return {
        group: {category: Fraction(0) for category in problem.categories}
        for group in problem.groups
    }


def equal_share(
    problem: PackingProblem | ValidatedPackingProblem,
) -> AllocationMatrix:
    """Allocate every category by capped equal-share water filling.

    Within a category, all groups with unmet demand grow at the same absolute
    rate.  Demand-satisfied groups leave the active set and their unused share is
    redistributed.  Thus the method never strands stock merely because one
    group's demand is smaller than the initial equal share.
    """

    normalized = normalize_problem(problem)
    amounts = _zero_matrix(normalized)
    for category in normalized.categories:
        remaining = min(
            normalized.stock[category],
            sum(
                (normalized.demand[g][category] for g in normalized.groups),
                Fraction(0),
            ),
        )
        active = {group for group in normalized.groups if normalized.demand[group][category] > 0}
        while active and remaining > 0:
            equal_increment = remaining / len(active)
            demand_increment = min(
                normalized.demand[group][category] - amounts[group][category] for group in active
            )
            increment = min(equal_increment, demand_increment)
            if increment < 0:
                raise RuntimeError("equal-share baseline reached negative slack")
            for group in active:
                amounts[group][category] += increment
            remaining -= increment * len(active)
            active = {
                group
                for group in active
                if amounts[group][category] < normalized.demand[group][category]
            }
            if increment == 0 and active:
                raise RuntimeError("equal-share baseline made no progress")
    return _matrix_from_normalized(normalized, amounts, "equal-share")


def demand_proportional(
    problem: PackingProblem | ValidatedPackingProblem,
) -> AllocationMatrix:
    """Allocate each category in proportion to its declared group demand."""

    normalized = normalize_problem(problem)
    amounts = _zero_matrix(normalized)
    for category in normalized.categories:
        total_demand = sum((normalized.demand[g][category] for g in normalized.groups), Fraction(0))
        if total_demand == 0:
            continue
        fraction_served = min(Fraction(1), normalized.stock[category] / total_demand)
        for group in normalized.groups:
            amounts[group][category] = normalized.demand[group][category] * fraction_served
    return _matrix_from_normalized(normalized, amounts, "demand-proportional")


def _normalize_utilities(
    problem: ValidatedPackingProblem,
    utilities: Mapping[str, Mapping[str, RationalLike]] | None,
) -> dict[str, dict[str, Fraction]]:
    if utilities is None:
        # This default is the published priority-weighted equivalent-unit
        # objective: sum_gk w_g * a_k * x_gk.
        return {
            group: {
                category: problem.weights[group] * problem.conversion_factors[category]
                for category in problem.categories
            }
            for group in problem.groups
        }
    if not isinstance(utilities, Mapping):
        raise AllocationInputError("utilities must be a group/category mapping")
    if set(utilities) != set(problem.groups):
        missing = sorted(set(problem.groups) - set(utilities))
        extra = sorted(set(utilities) - set(problem.groups))
        raise AllocationInputError(
            f"utilities group axis mismatch; missing={missing}, extra={extra}"
        )
    result: dict[str, dict[str, Fraction]] = {}
    for group in problem.groups:
        row = utilities[group]
        if not isinstance(row, Mapping) or set(row) != set(problem.categories):
            raise AllocationInputError(
                f"utilities[{group!r}] must define exactly {problem.categories!r}"
            )
        result[group] = {}
        for category in problem.categories:
            value = as_fraction(row[category], f"utilities[{group!r}][{category!r}]")
            result[group][category] = value
    return result


def utility_only(
    problem: PackingProblem | ValidatedPackingProblem,
    *,
    utilities: Mapping[str, Mapping[str, RationalLike]] | None = None,
) -> AllocationMatrix:
    """Maximize a separable linear utility without a fairness constraint.

    Because categories have independent demand and stock rows, the exact LP
    optimum is obtained by greedily exhausting positive-utility demand in
    descending utility order for each category.  Equal utilities are resolved
    by canonical group identifier, making the otherwise non-unique optimum
    reproducible.  Nonpositive-utility demand is left unallocated because stock
    constraints are upper bounds, not fulfillment requirements.

    If ``utilities`` is omitted, the coefficient is ``w_g * a_k``.
    """

    normalized = normalize_problem(problem)
    coefficients = _normalize_utilities(normalized, utilities)
    amounts = _zero_matrix(normalized)
    for category in normalized.categories:
        remaining = normalized.stock[category]
        ordering = sorted(
            normalized.groups,
            key=lambda group: (-coefficients[group][category], group),
        )
        for group in ordering:
            if remaining <= 0 or coefficients[group][category] <= 0:
                break
            amount = min(remaining, normalized.demand[group][category])
            amounts[group][category] = amount
            remaining -= amount
    return _matrix_from_normalized(normalized, amounts, "utility-only")


def allocation_baselines(
    problem: PackingProblem | ValidatedPackingProblem,
    *,
    utilities: Mapping[str, Mapping[str, RationalLike]] | None = None,
) -> Mapping[str, AllocationMatrix]:
    """Return all core baselines under one validated problem instance."""

    normalized = normalize_problem(problem)
    return {
        "equal-share": equal_share(normalized),
        "demand-proportional": demand_proportional(normalized),
        "utility-only": utility_only(normalized, utilities=utilities),
    }


__all__ = [
    "allocation_baselines",
    "demand_proportional",
    "equal_share",
    "utility_only",
]
