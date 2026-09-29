"""Monotone completion of original fair service floors in the full demand box.

The original packing allocation is retained as a certified floor. Completion
can leave fixed bundle rays: it is not itself called max-min fair on those rays.
"""

from fractions import Fraction

from aidmesh.allocator.certificate import certify_max_min, certify_rounding
from aidmesh.allocator.progressive_fill import AllocationMatrix, normalize_problem


def complete_fair_floor(problem, fair):
    """Preserve every group/category amount and exhaust independently usable stock.

    For positive category conversion factors and only demand/stock constraints,
    sum_k a_k min(s_k, sum_g d_gk) is a utility upper bound. Distributing each
    residual category to unsatisfied demand attains it without reducing any floor.
    This bound does not cover coupled substitutions, signed constraints or integer
    restrictions. Deterministic rotation or governance can replace the tie order.
    """
    p = normalize_problem(problem)
    certify_max_min(p, fair)
    amounts = {g: dict(fair.amounts[g]) for g in p.groups}
    for k in p.categories:
        residual = p.stock[k] - sum((amounts[g][k] for g in p.groups), Fraction(0))
        for g in sorted(p.groups, key=lambda g: (fair.ratios[g], g)):
            addition = min(residual, p.demand[g][k] - amounts[g][k])
            amounts[g][k] += addition
            residual -= addition
        assert sum(amounts[g][k] for g in p.groups) == min(
            p.stock[k], sum(p.demand[g][k] for g in p.groups)
        )
    result = AllocationMatrix(
        "fair-floor-completion",
        p.cell,
        p.groups,
        p.categories,
        amounts,
        p.demand,
        p.stock,
        p.weights,
        p.conversion_factors,
        p.units,
    )
    result.assert_feasible()
    return result


def complete_integer_fair_floor(problem, fair, rounded):
    """Complete *after* certified rounding so every issued integer floor survives."""
    p = normalize_problem(problem)
    certify_max_min(p, fair)
    certify_rounding(p, fair, rounded)
    amounts = {g: dict(rounded.amounts[g]) for g in p.groups}
    for k in p.categories:
        unit = p.units[k]
        remaining = int((p.stock[k] - sum(amounts[g][k] for g in p.groups)) // unit)
        for g in sorted(p.groups, key=lambda g: (fair.ratios[g], g)):
            count = min(remaining, int((p.demand[g][k] - amounts[g][k]) // unit))
            amounts[g][k] += count * unit
            remaining -= count
    result = AllocationMatrix(
        "integer-fair-floor-completion",
        p.cell,
        p.groups,
        p.categories,
        amounts,
        p.demand,
        p.stock,
        p.weights,
        p.conversion_factors,
        p.units,
    )
    result.assert_feasible()
    return result
