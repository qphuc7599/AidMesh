"""Focused examples and failure-path tests for the AidMesh allocator."""

from fractions import Fraction

import pytest

from aidmesh.allocator import (
    AllocationInputError,
    DuplicateIssuanceError,
    IssuanceLedger,
    PackingProblem,
    QuotaExceededError,
    allocation_baselines,
    compute_allocation_metrics,
    compute_rounding_metrics,
    demand_proportional,
    equal_share,
    progressive_fill,
    progressive_fill_cells,
    round_allocation,
    utility_only,
)


def one_category_problem(
    *,
    demand: dict[str, int],
    stock: int,
    weights: dict[str, int] | None = None,
    unit: int = 1,
    cell: str = "cell-a",
) -> PackingProblem:
    groups = tuple(demand)
    return PackingProblem(
        cell=cell,
        demand={group: {"food": amount} for group, amount in demand.items()},
        stock={"food": stock},
        weights=weights or {group: 1 for group in groups},
        conversion_factors={"food": 1},
        directions={group: {"food": 1} for group in groups},
        units={"food": unit},
    )


def test_weighted_progressive_fill_hits_exact_stock_and_demand_rows() -> None:
    problem = one_category_problem(
        demand={"ordinary": 10, "priority": 10},
        stock=15,
        weights={"ordinary": 1, "priority": 2},
    )

    allocation = progressive_fill(problem)

    assert allocation.ratios == {
        "ordinary": Fraction(1, 2),
        "priority": Fraction(1, 2),
    }
    assert allocation.amount("ordinary", "food") == 5
    assert allocation.amount("priority", "food") == 10
    assert len(allocation.bottlenecks) == 1
    event = allocation.bottlenecks[0]
    assert event.stock_rows == ("food",)
    assert event.individual_rows == (("priority", "food"),)
    allocation.assert_feasible()


def test_individual_cap_freezes_one_group_before_later_stock_bottleneck() -> None:
    problem = PackingProblem(
        cell="cell-a",
        demand={
            "composition-limited": {"food": 1, "water": 9},
            "balanced": {"food": 5, "water": 5},
        },
        stock={"food": 6, "water": 10},
        weights={"composition-limited": 1, "balanced": 1},
        conversion_factors={"food": 1, "water": 1},
        directions={
            "composition-limited": {"food": Fraction(1, 2), "water": Fraction(1, 2)},
            "balanced": {"food": Fraction(1, 2), "water": Fraction(1, 2)},
        },
    )

    allocation = progressive_fill(problem)

    assert allocation.ratios["composition-limited"] == Fraction(1, 5)
    assert allocation.ratios["balanced"] == 1
    assert allocation.amounts["composition-limited"] == {
        "food": Fraction(1),
        "water": Fraction(1),
    }
    assert allocation.amounts["balanced"] == {
        "food": Fraction(5),
        "water": Fraction(5),
    }
    assert [event.level for event in allocation.bottlenecks] == [
        Fraction(1, 5),
        Fraction(1),
    ]


def test_cells_are_explicit_and_duplicate_cells_are_rejected() -> None:
    first = one_category_problem(demand={"g": 2}, stock=1, cell="north")
    second = one_category_problem(demand={"g": 4}, stock=3, cell="south")

    campaign = progressive_fill_cells([second, first])

    assert tuple(campaign.cells) == ("north", "south")
    assert campaign["north"].amount("g", "food") == 1
    with pytest.raises(AllocationInputError, match="duplicate cell"):
        progressive_fill_cells([first, first])


@pytest.mark.parametrize(
    "problem, message",
    [
        (
            PackingProblem(
                cell="z",
                demand={"g": {"food": 1}},
                stock={"food": 1},
                weights={"g": 1},
                conversion_factors={"food": 1},
                directions={"g": {"food": Fraction(1, 2)}},
            ),
            "sum\\(a_k\\*p_gk\\)",
        ),
        (
            PackingProblem(
                cell="z",
                demand={"g": {"food": -1}},
                stock={"food": 1},
                weights={"g": 1},
                conversion_factors={"food": 1},
                directions={"g": {"food": 1}},
            ),
            "nonnegative",
        ),
        (
            PackingProblem(
                cell="z",
                demand={"g": {"food": 1}},
                stock={"food": 1},
                weights={"g": 0},
                conversion_factors={"food": 1},
                directions={"g": {"food": 1}},
            ),
            "positive",
        ),
    ],
)
def test_invalid_packing_inputs_fail_closed(problem: PackingProblem, message: str) -> None:
    with pytest.raises(AllocationInputError, match=message):
        progressive_fill(problem)


def test_integer_rounding_is_deterministic_feasible_and_conservative() -> None:
    divisible = progressive_fill(
        one_category_problem(demand={"a": 10, "b": 10, "c": 10}, stock=10, unit=2)
    )

    first = round_allocation(divisible, seed="committed-block-and-demand-root")
    repeated = round_allocation(divisible, seed="committed-block-and-demand-root")

    assert first.quotas == repeated.quotas
    assert first.seed_commitment == repeated.seed_commitment
    assert sorted(first.amounts[group]["food"] for group in first.groups) == [2, 4, 4]
    assert sum(first.amounts[group]["food"] for group in first.groups) == 10
    certificate = first.certificates["food"]
    assert certificate.floor_amount == 6
    assert certificate.residual_units_assigned == 2
    assert certificate.allocated_amount + certificate.stock_remaining == 10
    for group in first.groups:
        assert abs(first.amounts[group]["food"] - divisible.amounts[group]["food"]) < 2

    # Seed rotation changes at least one winner across a deterministic set of
    # commitments while every run remains reproducible.
    winner_sets = {
        tuple(
            sorted(
                group
                for group in divisible.groups
                if round_allocation(divisible, seed=f"epoch-{epoch}").amounts[group]["food"] == 4
            )
        )
        for epoch in range(16)
    }
    assert len(winner_sets) > 1


def test_rounding_never_crosses_a_non_aligned_demand_cap() -> None:
    problem = PackingProblem(
        cell="z",
        demand={"g": {"medicine": Fraction(7, 2)}},
        stock={"medicine": 10},
        weights={"g": 1},
        conversion_factors={"medicine": 1},
        directions={"g": {"medicine": 1}},
        units={"medicine": 2},
    )
    divisible = progressive_fill(problem)

    rounded = round_allocation(divisible, seed="seed")

    assert divisible.amount("g", "medicine") == Fraction(7, 2)
    assert rounded.amount("g", "medicine") == 2
    assert rounded.quota("g", "medicine") == 1
    assert rounded.certificates["medicine"].stock_remaining == 8


def two_category_integer_allocation():
    problem = PackingProblem(
        cell="cell-ledger",
        demand={
            "g1": {"food": 10, "water": 10},
            "g2": {"food": 10, "water": 10},
        },
        stock={"food": 8, "water": 8},
        weights={"g1": 1, "g2": 1},
        conversion_factors={"food": 1, "water": 1},
        directions={
            "g1": {"food": Fraction(1, 2), "water": Fraction(1, 2)},
            "g2": {"food": Fraction(1, 2), "water": Fraction(1, 2)},
        },
        units={"food": 1, "water": 1},
    )
    return round_allocation(progressive_fill(problem), seed="ledger-seed")


def test_issuance_ledger_enforces_atomic_quota_conservation_and_hash_chain() -> None:
    rounded = two_category_integer_allocation()
    ledger = IssuanceLedger(rounded)
    assert rounded.quota("g1", "food") == 4
    assert rounded.quota("g1", "water") == 4

    record = ledger.issue(
        issuance_id="opaque-issuance-0001",
        cell="cell-ledger",
        group="g1",
        quotas={"food": 3},
    )
    assert record.sequence == 1
    assert ledger.issued_quota("cell-ledger", "g1", "food") == 3
    assert ledger.issued_amount("cell-ledger", "g1", "food") == 3

    with pytest.raises(QuotaExceededError):
        ledger.issue(
            issuance_id="opaque-issuance-0002",
            cell="cell-ledger",
            group="g1",
            quotas={"food": 2, "water": 1},
        )
    # The valid water component was not partially committed.
    assert ledger.issued_quota("cell-ledger", "g1", "water") == 0
    assert len(ledger.records) == 1

    with pytest.raises(DuplicateIssuanceError):
        ledger.issue(
            issuance_id="opaque-issuance-0001",
            cell="cell-ledger",
            group="g1",
            quotas={"water": 1},
        )
    ledger.assert_consistent()


def test_baselines_have_distinct_registered_objectives_and_same_caps() -> None:
    problem = one_category_problem(
        demand={"a": 10, "b": 20},
        stock=15,
        weights={"a": 1, "b": 3},
    )

    equal = equal_share(problem)
    proportional = demand_proportional(problem)
    utility = utility_only(problem)

    assert equal.amounts["a"]["food"] == Fraction(15, 2)
    assert equal.amounts["b"]["food"] == Fraction(15, 2)
    assert proportional.amounts["a"]["food"] == 5
    assert proportional.amounts["b"]["food"] == 10
    assert utility.amounts["a"]["food"] == 0
    assert utility.amounts["b"]["food"] == 15
    for allocation in (equal, proportional, utility):
        allocation.assert_feasible()
        assert sum(allocation.amounts[g]["food"] for g in allocation.groups) == 15
    assert set(allocation_baselines(problem)) == {
        "equal-share",
        "demand-proportional",
        "utility-only",
    }


def test_metrics_report_fairness_efficiency_and_rounding_side_by_side() -> None:
    divisible = progressive_fill(one_category_problem(demand={"a": 10, "b": 20}, stock=15, unit=2))
    rounded = round_allocation(divisible, seed="metrics-seed")

    metrics = compute_allocation_metrics(divisible)
    rounding = compute_rounding_metrics(divisible, rounded)

    assert metrics.minimum_service_ratio == Fraction(1, 2)
    assert metrics.leximin_vector == (Fraction(1, 2), Fraction(1, 2))
    assert metrics.gini_service_ratio == 0
    assert metrics.jain_service_ratio == 1
    assert metrics.total_fulfilled_amount == 15
    assert metrics.total_unmet_amount == 15
    assert metrics.utilization_by_category["food"] == 1
    assert rounding.additive_bound_holds
    assert rounding.maximum_absolute_error < 2
