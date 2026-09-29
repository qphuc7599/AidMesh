"""Two-stage maximin-then-utility planner for empirical comparison.

Unlike :func:`progressive_fill`, this planner is not covered by the fixed-direction
packing theorem.  It solves a broader LP: first maximize the worst normalized service
ratio over the original demand/stock box, then maximize conversion-adjusted utility
without lowering that optimal floor.  The distinction is intentional and reported in
the experiment output.
"""

from __future__ import annotations

from fractions import Fraction

import numpy as np
from scipy.optimize import linprog

from .progressive_fill import (
    AllocationInvariantError,
    AllocationMatrix,
    PackingProblem,
    ValidatedPackingProblem,
    _matrix_from_normalized,
    normalize_problem,
)


def _fraction(value: float) -> Fraction:
    if abs(value) < 1e-10:
        return Fraction(0)
    return Fraction(str(round(float(value), 10)))


def maximin_then_utility(
    problem: PackingProblem | ValidatedPackingProblem,
) -> AllocationMatrix:
    normalized = normalize_problem(problem)
    groups = normalized.groups
    categories = normalized.categories
    coordinates = [(group, category) for group in groups for category in categories]
    coordinate_index = {coordinate: index for index, coordinate in enumerate(coordinates)}
    variable_count = len(coordinates)

    stock_rows: list[list[float]] = []
    stock_rhs: list[float] = []
    for category in categories:
        row = [0.0] * (variable_count + 1)
        for group in groups:
            row[coordinate_index[(group, category)]] = 1.0
        stock_rows.append(row)
        stock_rhs.append(float(normalized.stock[category]))

    fairness_rows: list[list[float]] = []
    fairness_rhs: list[float] = []
    for group in groups:
        row = [0.0] * (variable_count + 1)
        for category in categories:
            row[coordinate_index[(group, category)]] = -float(
                normalized.conversion_factors[category]
            )
        row[-1] = float(normalized.weights[group] * normalized.aggregate_demand[group])
        fairness_rows.append(row)
        fairness_rhs.append(0.0)

    objective = np.zeros(variable_count + 1)
    objective[-1] = -1.0
    bounds = [
        (0.0, float(normalized.demand[group][category])) for group, category in coordinates
    ] + [(0.0, None)]
    stage_one = linprog(
        objective,
        A_ub=np.asarray(stock_rows + fairness_rows),
        b_ub=np.asarray(stock_rhs + fairness_rhs),
        bounds=bounds,
        method="highs",
    )
    if not stage_one.success:
        raise AllocationInvariantError(f"maximin LP failed: {stage_one.message}")
    fairness_floor = float(stage_one.x[-1])

    utility_objective = np.asarray(
        [-float(normalized.conversion_factors[category]) for _, category in coordinates]
    )
    stage_two_rows = [row[:-1] for row in stock_rows]
    stage_two_rhs = stock_rhs.copy()
    for group in groups:
        row = [0.0] * variable_count
        for category in categories:
            row[coordinate_index[(group, category)]] = -float(
                normalized.conversion_factors[category]
            )
        required = fairness_floor * float(
            normalized.weights[group] * normalized.aggregate_demand[group]
        )
        stage_two_rows.append(row)
        stage_two_rhs.append(-required + 1e-8)
    stage_two = linprog(
        utility_objective,
        A_ub=np.asarray(stage_two_rows),
        b_ub=np.asarray(stage_two_rhs),
        bounds=bounds[:-1],
        method="highs",
    )
    if not stage_two.success:
        raise AllocationInvariantError(f"utility completion LP failed: {stage_two.message}")
    amounts = {
        group: {
            category: min(
                normalized.demand[group][category],
                _fraction(stage_two.x[coordinate_index[(group, category)]]),
            )
            for category in categories
        }
        for group in groups
    }
    # HiGHS works in floating point.  Project only numerical residue back into
    # the exact rational stock box before constructing the audited matrix.
    for category in categories:
        used = sum((amounts[group][category] for group in groups), Fraction(0))
        excess = used - normalized.stock[category]
        if excess <= 0:
            continue
        for group in sorted(groups, key=lambda item: (-amounts[item][category], item)):
            reduction = min(excess, amounts[group][category])
            amounts[group][category] -= reduction
            excess -= reduction
            if excess == 0:
                break
    return _matrix_from_normalized(normalized, amounts, "aidmesh-maximin-utility")
