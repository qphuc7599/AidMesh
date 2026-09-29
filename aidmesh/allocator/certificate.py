"""Check the original fairness theorem without replaying progressive filling."""

from fractions import Fraction

from aidmesh.allocator.progressive_fill import AllocationInvariantError, normalize_problem


def certify_max_min(problem, allocation):
    """Exact final-state packing certificate, independent of allocator events.

    Each positive-demand group needs a tight individual cap, or a tight stock
    row whose positive-coefficient groups all have no larger service ratio.
    Raising that group must therefore lower a no-better-served group.
    """
    p = normalize_problem(problem)
    if set(allocation.ratios) != set(p.groups):
        raise AllocationInvariantError("ratio axes differ from committed problem")
    a = {
        g: {k: p.weights[g] * p.aggregate_demand[g] * p.directions[g][k] for k in p.categories}
        for g in p.groups
    }
    r = allocation.ratios
    for g in p.groups:
        if r[g] < 0:
            raise AllocationInvariantError("negative ratio")
        for k in p.categories:
            x = a[g][k] * r[g]
            if x > p.demand[g][k] or allocation.amounts[g][k] != x:
                raise AllocationInvariantError("allocation does not match committed packing ray")
    used = {k: sum((a[g][k] * r[g] for g in p.groups), Fraction(0)) for k in p.categories}
    if any(used[k] > p.stock[k] for k in p.categories):
        raise AllocationInvariantError("stock exceeded")
    certificates = {}
    for g in p.groups:
        if p.aggregate_demand[g] == 0:
            certificates[g] = ("zero-demand", "")
            continue
        caps = [k for k in p.categories if a[g][k] > 0 and a[g][k] * r[g] == p.demand[g][k]]
        rows = [
            k
            for k in p.categories
            if a[g][k] > 0
            and used[k] == p.stock[k]
            and all(a[h][k] == 0 or r[h] <= r[g] for h in p.groups)
        ]
        if caps:
            certificates[g] = ("demand-cap", caps[0])
        elif rows:
            certificates[g] = ("stock-bottleneck", rows[0])
        else:
            raise AllocationInvariantError(f"no max-min certificate for {g}")
    return certificates


def certify_rounding(problem, divisible, integer):
    """Verify feasibility and both additive bounds against original inputs."""
    p = normalize_problem(problem)
    for k in p.categories:
        if sum(integer.amounts[g][k] for g in p.groups) > p.stock[k]:
            raise AllocationInvariantError("rounded stock exceeded")
    for g in p.groups:
        loss = Fraction(0)
        bound = Fraction(0)
        for k in p.categories:
            x, y, unit = divisible.amounts[g][k], integer.amounts[g][k], p.units[k]
            if y < 0 or y > p.demand[g][k] or (y / unit).denominator != 1:
                raise AllocationInvariantError("rounded allocation infeasible or nonintegral")
            if not y > x - unit:
                raise AllocationInvariantError("category additive bound failed")
            loss += p.conversion_factors[k] * (x - y)
            bound += p.conversion_factors[k] * unit
        if p.aggregate_demand[g] > 0 and not loss < bound:
            raise AllocationInvariantError("normalized additive bound failed")
    return True
