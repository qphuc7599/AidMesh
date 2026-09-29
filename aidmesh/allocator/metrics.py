"""Exact allocation and rounding metrics for paired allocator experiments.

No metric in this module is treated as a normative ground truth.  The returned
record keeps worst-group service, total fulfillment, unmet demand, dispersion,
and stock use side by side so experiments can report the efficiency--fairness
trade-off rather than selecting whichever scalar favors one method.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from types import MappingProxyType

from .progressive_fill import (
    AllocationInputError,
    AllocationInvariantError,
    AllocationMatrix,
    IntegerAllocation,
    RationalLike,
    as_fraction,
)


def _freeze(mapping: Mapping[str, Fraction]) -> Mapping[str, Fraction]:
    return MappingProxyType(dict(mapping))


def _freeze_nested(
    mapping: Mapping[str, Mapping[str, Fraction]],
) -> Mapping[str, Mapping[str, Fraction]]:
    return MappingProxyType({key: MappingProxyType(dict(row)) for key, row in mapping.items()})


def quantile(values: Sequence[Fraction], probability: RationalLike) -> Fraction:
    """Return an exact linearly interpolated sample quantile (R-7 convention)."""

    if not values:
        raise AllocationInputError("quantile requires at least one value")
    probability_fraction = as_fraction(probability, "probability")
    if probability_fraction < 0 or probability_fraction > 1:
        raise AllocationInputError("probability must lie in [0, 1]")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = probability_fraction * (len(ordered) - 1)
    lower = position.numerator // position.denominator
    upper = min(lower + 1, len(ordered) - 1)
    interpolation = position - lower
    return ordered[lower] + interpolation * (ordered[upper] - ordered[lower])


def gini_coefficient(values: Sequence[Fraction]) -> Fraction | None:
    """Return population Gini, or ``None`` when every observation is zero."""

    if not values:
        return None
    if any(value < 0 for value in values):
        raise AllocationInputError("Gini observations must be nonnegative")
    total = sum(values, Fraction(0))
    if total == 0:
        return None
    pairwise = sum((abs(left - right) for left in values for right in values), Fraction(0))
    return pairwise / (2 * len(values) * total)


def jain_index(values: Sequence[Fraction]) -> Fraction | None:
    """Return Jain's index, or ``None`` for an all-zero/empty sample."""

    if not values:
        return None
    if any(value < 0 for value in values):
        raise AllocationInputError("Jain observations must be nonnegative")
    square_sum = sum((value * value for value in values), Fraction(0))
    if square_sum == 0:
        return None
    total = sum(values, Fraction(0))
    return total * total / (len(values) * square_sum)


def service_ratios(allocation: AllocationMatrix) -> Mapping[str, Fraction]:
    """Compute ``x_g / (w_g d_g)`` for groups with positive aggregate demand."""

    allocation.assert_feasible()
    ratios: dict[str, Fraction] = {}
    for group in allocation.groups:
        eligible = sum(
            (
                allocation.conversion_factors[category] * allocation.demand[group][category]
                for category in allocation.categories
            ),
            Fraction(0),
        )
        if eligible == 0:
            continue
        fulfilled = sum(
            (
                allocation.conversion_factors[category] * allocation.amounts[group][category]
                for category in allocation.categories
            ),
            Fraction(0),
        )
        ratios[group] = fulfilled / (allocation.weights[group] * eligible)
    return _freeze(ratios)


@dataclass(frozen=True, slots=True)
class AllocationMetrics:
    """Multi-objective diagnostics for one cell and allocation method."""

    method: str
    cell: str
    service_ratios: Mapping[str, Fraction]
    leximin_vector: tuple[Fraction, ...]
    worst_groups: tuple[str, ...]
    minimum_service_ratio: Fraction | None
    median_service_ratio: Fraction | None
    gini_service_ratio: Fraction | None
    jain_service_ratio: Fraction | None
    fulfilled_by_category: Mapping[str, Fraction]
    unmet_by_category: Mapping[str, Fraction]
    utilization_by_category: Mapping[str, Fraction | None]
    total_fulfilled_amount: Fraction
    total_unmet_amount: Fraction
    equivalent_fulfilled: Fraction
    equivalent_unmet: Fraction


def compute_allocation_metrics(allocation: AllocationMatrix) -> AllocationMetrics:
    """Compute exact fairness, fulfillment, and utilization diagnostics."""

    if not isinstance(allocation, AllocationMatrix):
        raise AllocationInputError("allocation must be an AllocationMatrix")
    allocation.assert_feasible()
    ratios = dict(service_ratios(allocation))
    values = tuple(ratios.values())
    minimum = min(values) if values else None
    worst = (
        tuple(sorted(group for group, ratio in ratios.items() if ratio == minimum))
        if minimum is not None
        else ()
    )
    fulfilled: dict[str, Fraction] = {}
    unmet: dict[str, Fraction] = {}
    utilization: dict[str, Fraction | None] = {}
    for category in allocation.categories:
        used = sum(
            (allocation.amounts[group][category] for group in allocation.groups),
            Fraction(0),
        )
        eligible = sum(
            (allocation.demand[group][category] for group in allocation.groups),
            Fraction(0),
        )
        fulfilled[category] = used
        unmet[category] = eligible - used
        utilization[category] = (
            used / allocation.stock[category] if allocation.stock[category] > 0 else None
        )

    total_fulfilled = sum(fulfilled.values(), Fraction(0))
    total_unmet = sum(unmet.values(), Fraction(0))
    equivalent_fulfilled = sum(
        (
            allocation.conversion_factors[category] * fulfilled[category]
            for category in allocation.categories
        ),
        Fraction(0),
    )
    equivalent_unmet = sum(
        (
            allocation.conversion_factors[category] * unmet[category]
            for category in allocation.categories
        ),
        Fraction(0),
    )
    return AllocationMetrics(
        method=allocation.method,
        cell=allocation.cell,
        service_ratios=_freeze(ratios),
        leximin_vector=tuple(sorted(values)),
        worst_groups=worst,
        minimum_service_ratio=minimum,
        median_service_ratio=quantile(values, Fraction(1, 2)) if values else None,
        gini_service_ratio=gini_coefficient(values),
        jain_service_ratio=jain_index(values),
        fulfilled_by_category=_freeze(fulfilled),
        unmet_by_category=_freeze(unmet),
        utilization_by_category=MappingProxyType(utilization),
        total_fulfilled_amount=total_fulfilled,
        total_unmet_amount=total_unmet,
        equivalent_fulfilled=equivalent_fulfilled,
        equivalent_unmet=equivalent_unmet,
    )


@dataclass(frozen=True, slots=True)
class RoundingMetrics:
    """Coordinate-level and aggregate differences from a divisible target."""

    cell: str
    coordinate_error: Mapping[str, Mapping[str, Fraction]]
    equivalent_error_by_group: Mapping[str, Fraction]
    maximum_absolute_error: Fraction
    total_absolute_error: Fraction
    total_signed_error: Fraction
    rounded_up_coordinates: int
    rounded_down_coordinates: int
    exact_coordinates: int
    additive_bound_holds: bool


def compute_rounding_metrics(
    divisible: AllocationMatrix,
    rounded: IntegerAllocation,
) -> RoundingMetrics:
    """Compare integer quotas with their divisible target on identical axes."""

    if not isinstance(divisible, AllocationMatrix) or not isinstance(rounded, IntegerAllocation):
        raise AllocationInputError(
            "compute_rounding_metrics expects AllocationMatrix and IntegerAllocation"
        )
    if (
        divisible.cell != rounded.cell
        or divisible.groups != rounded.groups
        or divisible.categories != rounded.categories
    ):
        raise AllocationInputError("rounding comparison axes do not match")
    divisible.assert_feasible()
    rounded.assert_feasible()
    errors: dict[str, dict[str, Fraction]] = {}
    equivalent: dict[str, Fraction] = {}
    absolute_errors: list[Fraction] = []
    up = down = exact = 0
    bound_holds = True
    for group in divisible.groups:
        errors[group] = {}
        equivalent[group] = Fraction(0)
        for category in divisible.categories:
            error = rounded.amounts[group][category] - divisible.amounts[group][category]
            errors[group][category] = error
            absolute_errors.append(abs(error))
            equivalent[group] += divisible.conversion_factors[category] * error
            if error > 0:
                up += 1
            elif error < 0:
                down += 1
            else:
                exact += 1
            if abs(error) >= divisible.units[category]:
                bound_holds = False
    if not bound_holds:
        raise AllocationInvariantError("integer allocation violates additive unit bound")
    return RoundingMetrics(
        cell=divisible.cell,
        coordinate_error=_freeze_nested(errors),
        equivalent_error_by_group=_freeze(equivalent),
        maximum_absolute_error=max(absolute_errors, default=Fraction(0)),
        total_absolute_error=sum(absolute_errors, Fraction(0)),
        total_signed_error=sum(
            (error for row in errors.values() for error in row.values()), Fraction(0)
        ),
        rounded_up_coordinates=up,
        rounded_down_coordinates=down,
        exact_coordinates=exact,
        additive_bound_holds=True,
    )


__all__ = [
    "AllocationMetrics",
    "RoundingMetrics",
    "compute_allocation_metrics",
    "compute_rounding_metrics",
    "gini_coefficient",
    "jain_index",
    "quantile",
    "service_ratios",
]
