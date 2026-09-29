"""Seeded generative invariants for packing, rounding, and issuance.

These are implementation property tests, not reported experiment observations.
The fixed PRNG seed makes failures exactly reproducible.
"""

import random
from fractions import Fraction

import pytest

from aidmesh.allocator import (
    IssuanceLedger,
    PackingProblem,
    QuotaExceededError,
    allocation_baselines,
    progressive_fill,
    round_allocation,
    service_ratios,
)


def generated_problem(rng: random.Random, case: int) -> PackingProblem:
    group_count = rng.randint(1, 7)
    category_count = rng.randint(1, 5)
    groups = [f"g-{index:02d}" for index in range(group_count)]
    categories = [f"k-{index:02d}" for index in range(category_count)]
    demand: dict[str, dict[str, int]] = {}
    directions: dict[str, dict[str, Fraction]] = {}
    for group in groups:
        row = {category: rng.randint(0, 30) for category in categories}
        if not any(row.values()):
            row[rng.choice(categories)] = rng.randint(1, 30)
        demand[group] = row
        total = sum(row.values())
        directions[group] = {category: Fraction(row[category], total) for category in categories}
    total_by_category = {
        category: sum(demand[group][category] for group in groups) for category in categories
    }
    stock = {category: rng.randint(0, total_by_category[category] + 10) for category in categories}
    return PackingProblem(
        cell=f"cell-{case:03d}",
        demand=demand,
        stock=stock,
        weights={group: rng.randint(1, 5) for group in groups},
        conversion_factors={category: 1 for category in categories},
        directions=directions,
        units={category: rng.randint(1, 4) for category in categories},
    )


def reordered(problem: PackingProblem) -> PackingProblem:
    """Construct the same problem with all insertion orders reversed."""

    group_order = list(reversed(list(problem.demand)))
    category_order = list(reversed(list(problem.stock)))
    return PackingProblem(
        cell=problem.cell,
        demand={
            group: {category: problem.demand[group].get(category, 0) for category in category_order}
            for group in group_order
        },
        stock={category: problem.stock[category] for category in category_order},
        weights={group: problem.weights[group] for group in group_order},
        conversion_factors={
            category: problem.conversion_factors[category] for category in category_order
        },
        directions={
            group: {
                category: problem.directions[group].get(category, 0) for category in category_order
            }
            for group in group_order
        },
        units={category: problem.units[category] for category in category_order},
    )


def test_progressive_fill_and_integer_rounding_invariants_over_generated_cases() -> None:
    rng = random.Random(0xA1D5EED)
    for case in range(80):
        problem = generated_problem(rng, case)
        allocation = progressive_fill(problem)
        allocation.assert_feasible()

        # Canonical axes make output independent of input mapping insertion order.
        permuted = progressive_fill(reordered(problem))
        assert allocation.groups == permuted.groups
        assert allocation.categories == permuted.categories
        assert allocation.ratios == permuted.ratios
        assert allocation.amounts == permuted.amounts
        assert allocation.bottlenecks == permuted.bottlenecks

        assert tuple(event.level for event in allocation.bottlenecks) == tuple(
            sorted(event.level for event in allocation.bottlenecks)
        )
        measured_ratios = service_ratios(allocation)
        assert measured_ratios == allocation.ratios

        # Every positive-demand group terminates on either its own demand cap or
        # a tight shared stock row with positive coefficient.  Otherwise its
        # ratio could still increase and filling would not be maximal.
        for group in allocation.groups:
            individually_blocked = any(
                allocation.coefficients[group][category] > 0
                and allocation.coefficients[group][category] * allocation.ratios[group]
                == allocation.demand[group][category]
                for category in allocation.categories
            )
            stock_blocked = any(
                allocation.coefficients[group][category] > 0
                and sum(
                    allocation.coefficients[other][category] * allocation.ratios[other]
                    for other in allocation.groups
                )
                == allocation.stock[category]
                for category in allocation.categories
            )
            assert individually_blocked or stock_blocked

        rounded = round_allocation(allocation, seed=f"rounding-case-{case}")
        repeated = round_allocation(allocation, seed=f"rounding-case-{case}")
        assert rounded.quotas == repeated.quotas
        assert rounded.seed_commitment == repeated.seed_commitment
        rounded.assert_feasible()
        for category in rounded.categories:
            certificate = rounded.certificates[category]
            assert (
                certificate.allocated_amount + certificate.stock_remaining
                == rounded.stock[category]
            )
            assert certificate.stock_remaining >= 0
            assert certificate.residual_units_assigned >= 0
            for group in rounded.groups:
                assert rounded.quotas[group][category] >= 0
                assert (
                    rounded.amounts[group][category]
                    == rounded.quotas[group][category] * rounded.units[category]
                )
                assert (
                    abs(rounded.amounts[group][category] - allocation.amounts[group][category])
                    < rounded.units[category]
                )


def test_all_baselines_preserve_common_caps_and_use_available_eligible_stock() -> None:
    rng = random.Random(0xBA5E11E)
    for case in range(50):
        problem = generated_problem(rng, case)
        normalized = problem.normalized()
        for allocation in allocation_baselines(normalized).values():
            allocation.assert_feasible()
            for category in allocation.categories:
                used = sum(allocation.amounts[group][category] for group in allocation.groups)
                eligible = sum(allocation.demand[group][category] for group in allocation.groups)
                # Default utility coefficients are strictly positive, so all
                # three baseline optima exhaust the smaller of stock and demand.
                assert used == min(allocation.stock[category], eligible)


def test_ledger_can_issue_exact_caps_but_never_one_quota_more() -> None:
    rng = random.Random(0x1ED6E2)
    for case in range(35):
        rounded = round_allocation(
            progressive_fill(generated_problem(rng, case)),
            seed=f"ledger-case-{case}",
        )
        ledger = IssuanceLedger(rounded)
        record_number = 0
        for group in rounded.groups:
            positive = {
                category: rounded.quotas[group][category]
                for category in rounded.categories
                if rounded.quotas[group][category] > 0
            }
            if positive:
                record_number += 1
                ledger.issue(
                    issuance_id=f"case-{case}-record-{record_number}",
                    cell=rounded.cell,
                    group=group,
                    quotas=positive,
                )
        ledger.assert_consistent()
        for group in rounded.groups:
            for category in rounded.categories:
                assert ledger.remaining_quota(rounded.cell, group, category) == 0

        group = rounded.groups[0]
        category = rounded.categories[0]
        with pytest.raises(QuotaExceededError):
            ledger.issue(
                issuance_id=f"case-{case}-one-too-many",
                cell=rounded.cell,
                group=group,
                quotas={category: 1},
            )
        ledger.assert_consistent()
