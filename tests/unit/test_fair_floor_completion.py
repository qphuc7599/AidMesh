from fractions import Fraction

from aidmesh.allocator.completion import complete_fair_floor, complete_integer_fair_floor
from aidmesh.allocator.progressive_fill import PackingProblem, progressive_fill, round_allocation


def test_completion_recovers_stranded_category_without_lowering_any_floor():
    p = PackingProblem(
        "z",
        {"a": {"x": 10, "y": 10}, "b": {"x": 10, "y": 10}},
        {"x": 2, "y": 20},
        {"a": 1, "b": 1},
        {"x": 1, "y": 1},
        {g: {"x": Fraction(1, 2), "y": Fraction(1, 2)} for g in ("a", "b")},
    )
    floor = progressive_fill(p)
    result = complete_fair_floor(p, floor)
    assert sum(result.amounts[g]["y"] for g in p.demand) == 20
    assert sum(result.amounts[g]["x"] for g in p.demand) == 2
    assert all(result.amounts[g][k] >= floor.amounts[g][k] for g in p.demand for k in p.stock)


def test_integer_completion_preserves_issued_units_and_subunit_caps():
    p = PackingProblem(
        "z",
        {"a": {"k": Fraction(7, 2)}, "b": {"k": Fraction(11, 2)}},
        {"k": Fraction(17, 2)},
        {"a": 1, "b": 2},
        {"k": 1},
        {"a": {"k": 1}, "b": {"k": 1}},
        units={"k": 2},
    )
    floor = progressive_fill(p)
    rounded = round_allocation(floor, seed="locked")
    result = complete_integer_fair_floor(p, floor, rounded)
    assert sum(result.amounts[g]["k"] for g in p.demand) == 6
    assert all(result.amounts[g]["k"] >= rounded.amounts[g]["k"] for g in p.demand)
