"""Full-box lexicographic allocation with exact rational LP certificates.

HiGHS proposes solutions. Each returned LP optimum is accepted only after exact
primal feasibility, dual signs, stationarity and strong-duality checks. No
floating-point epsilon defines the lexicographic preference.
"""

from dataclasses import dataclass
from fractions import Fraction as F

import numpy as np
from scipy.optimize import linprog
from sympy import Matrix, Rational

from aidmesh.allocator.progressive_fill import normalize_problem


def _sp(v: F) -> Rational:
    value = F(v)
    return Rational(value.numerator, value.denominator)


def _dot(a, b):
    return sum((x * y for x, y in zip(a, b, strict=True)), F(0))


def _dual_bottlenecks(c, ub, rhs, eq, values, bounds, active, services, solution, level):
    """Use HiGHS only to select a supported exact bottleneck face.

    The optimum and selected tightness are checked against the exact-simplex
    solution. If numerical dual recovery is inconclusive, the caller falls back
    to exact per-group maximization.
    """
    result = linprog(
        np.asarray(c, dtype=float),
        A_ub=np.asarray(ub, dtype=float),
        b_ub=np.asarray(rhs, dtype=float),
        A_eq=np.asarray(eq, dtype=float) if eq else None,
        b_eq=np.asarray(values, dtype=float) if eq else None,
        bounds=[
            (
                None if lower is None else float(lower),
                None if upper is None else float(upper),
            )
            for lower, upper in bounds
        ],
        method="highs",
    )
    if not result.success or abs(float(result.fun) - float(_dot(c, solution))) > 1e-7:
        return []
    service_rows = len(active)
    marginals = result.ineqlin.marginals[-service_rows:]
    return [
        g
        for g, m in zip(sorted(active), marginals, strict=True)
        if abs(float(m)) > 1e-9 and _dot(services[g], solution[:-1]) == level
    ]


def certified_lp(c, ub, rhs, eq, values, bounds):
    """Minimize a rational LP and recover an exact primal/dual basis certificate."""
    c = list(map(F, c))
    ub = [list(map(F, a)) for a in ub]
    rhs = list(map(F, rhs))
    eq = [list(map(F, a)) for a in eq]
    values = list(map(F, values))
    bounds = [
        (
            None if lower is None else F(lower),
            None if upper is None else F(upper),
        )
        for lower, upper in bounds
    ]
    result = linprog(
        np.asarray(c, dtype=float),
        A_ub=np.asarray(ub, dtype=float) if ub else None,
        b_ub=np.asarray(rhs, dtype=float) if ub else None,
        A_eq=np.asarray(eq, dtype=float) if eq else None,
        b_eq=np.asarray(values, dtype=float) if eq else None,
        bounds=[
            (
                None if lower is None else float(lower),
                None if upper is None else float(upper),
            )
            for lower, upper in bounds
        ],
        method="highs",
        options={"primal_feasibility_tolerance": 1e-9, "dual_feasibility_tolerance": 1e-9},
    )
    if not result.success:
        raise ValueError("LP failed: " + result.message)
    approximate = list(map(float, result.x))
    n = len(c)
    candidates = []

    def active(left, right):
        return abs(float(left) - float(right)) <= 1e-7 * (1 + abs(float(right)))

    # Each candidate is (row, rhs, equality, approximate multiplier, label).
    for i, (row, b) in enumerate(zip(eq, values, strict=True)):
        candidates.append((row, b, True, float(result.eqlin.marginals[i]), f"eq:{i}"))
    for i, (row, b) in enumerate(zip(ub, rhs, strict=True)):
        if active(sum(float(a) * v for a, v in zip(row, approximate, strict=True)), b):
            candidates.append((row, b, False, float(result.ineqlin.marginals[i]), f"ub:{i}"))
    for j, (lower, upper) in enumerate(bounds):
        unit = [F(0)] * n
        if lower is not None and active(approximate[j], lower):
            unit[j] = F(-1)
            candidates.append(
                (unit, -lower, False, -float(result.lower.marginals[j]), f"lower:{j}")
            )
        if upper is not None and active(approximate[j], upper):
            unit = [F(0)] * n
            unit[j] = F(1)
            candidates.append((unit, upper, False, float(result.upper.marginals[j]), f"upper:{j}"))
    # Nonzero dual rows first usually recover the optimal HiGHS basis; remaining
    # tight rows complete a degenerate basis without changing the optimum face.
    candidates.sort(key=lambda item: (abs(item[3]) <= 1e-10, -abs(item[3]), item[4]))
    selected = []
    rank = 0
    for row, b, equality, marginal, label in candidates:
        new_rank = Matrix(
            [[_sp(v) for v in r] for r, *_ in selected + [(row, b, equality, marginal, label)]]
        ).rank()
        if new_rank > rank:
            selected.append((row, b, equality, marginal, label))
            rank = new_rank
        if rank == n:
            break
    if rank != n:
        raise ArithmeticError(f"cannot recover full exact LP basis ({rank}/{n})")
    basis = Matrix([[_sp(v) for v in row] for row, *_ in selected])
    exact_rhs = Matrix([_sp(b) for _, b, *_ in selected])
    solution = basis.inv() * exact_rhs
    x = [F(str(v)) for v in solution]
    bad_ub = [
        (i, _dot(a, x), b) for i, (a, b) in enumerate(zip(ub, rhs, strict=True)) if _dot(a, x) > b
    ]
    bad_eq = [
        (i, _dot(a, x), b)
        for i, (a, b) in enumerate(zip(eq, values, strict=True))
        if _dot(a, x) != b
    ]
    bad_bounds = [
        (i, v, lower, upper)
        for i, (v, (lower, upper)) in enumerate(zip(x, bounds, strict=True))
        if (lower is not None and v < lower) or (upper is not None and v > upper)
    ]
    if bad_ub or bad_eq or bad_bounds:
        raise ArithmeticError(
            f"exact simplex returned infeasible solution: "
            f"ub={bad_ub[:2]} eq={bad_eq[:2]} bounds={bad_bounds[:2]}"
        )
    dual = basis.T.inv() * Matrix([_sp(v) for v in c])
    multipliers = [F(str(v)) for v in dual]
    bad_sign = [
        (row[4], multiplier)
        for row, multiplier in zip(selected, multipliers, strict=True)
        if not row[2] and multiplier > 0
    ]
    objective = _dot(c, x)
    dual_objective = sum((b * y for (_, b, *_), y in zip(selected, multipliers, strict=True)), F(0))
    if bad_sign or objective != dual_objective:
        raise ArithmeticError(
            f"invalid exact LP dual certificate: signs={bad_sign[:2]} "
            f"primal={objective} dual={dual_objective}"
        )
    return x, dict(
        objective=str(objective),
        primal=list(map(str, x)),
        solver="HiGHS with exact rational primal/dual basis recovery",
        basis=[row[4] for row in selected],
        basis_dual=list(map(str, multipliers)),
        c=list(map(str, c)),
        A_ub=[[str(v) for v in a] for a in ub],
        b_ub=list(map(str, rhs)),
        A_eq=[[str(v) for v in a] for a in eq],
        b_eq=list(map(str, values)),
        bounds=[[None if v is None else str(v) for v in pair] for pair in bounds],
    )


@dataclass
class FullBoxResult:
    amounts: dict
    ratios: dict
    utility: F
    certificates: list
    profile: str


def lexicographic_full_box(
    problem, *, floors=None, utility_first=False, utilities=None, service_factors=None
):
    """Exact lexicographic service on full box, optionally after utility maximization.

    Floors are irrevocable per-group/category amounts. Final within-service ties
    use lexicographically maximal coordinates in canonical (group,category) order.
    Zero-demand groups have no service ratio and cannot consume positive stock.
    """
    p = normalize_problem(problem)
    coordinates = [(g, k) for g in p.groups for k in p.categories]
    n = len(coordinates)
    bounds = []
    if floors is not None and (
        set(floors) != set(p.groups) or any(set(floors[g]) != set(p.categories) for g in p.groups)
    ):
        raise ValueError("floor axes mismatch")
    for g, k in coordinates:
        lower = F(0) if floors is None else F(floors[g][k])
        if not 0 <= lower <= p.demand[g][k]:
            raise ValueError("invalid floor")
        bounds.append((lower, p.demand[g][k]))
    ub = [[F(k == category) for g, k in coordinates] for category in p.categories]
    rhs = [p.stock[k] for k in p.categories]
    eq = []
    vals = []
    certs = []
    utility = [
        p.weights[g] * p.conversion_factors[k] if utilities is None else F(utilities[g][k])
        for g, k in coordinates
    ]
    factors = (
        {g: F(1) for g in p.groups}
        if service_factors is None
        else {g: F(service_factors[g]) for g in p.groups}
    )
    if set(factors) != set(p.groups) or any(not 0 < factors[g] <= 1 for g in p.groups):
        raise ValueError("service factors must cover all groups and lie in (0,1]")
    services = {
        g: [
            factors[g] * p.conversion_factors[k] / (p.weights[g] * p.aggregate_demand[g])
            if h == g
            else F(0)
            for h, k in coordinates
        ]
        for g in p.groups
        if p.aggregate_demand[g] > 0
    }

    def solve(c, A=ub, b=rhs, E=eq, v=vals, B=bounds):
        x, cert = certified_lp(c, A, b, E, v, B)
        certs.append(cert)
        return x

    if utility_first:
        x = solve([-v for v in utility])
        eq.append(utility)
        vals.append(_dot(utility, x))
    active = set(services)
    while active:
        # max t subject to each remaining normalized service >= t.
        A = [[*a, F(0)] for a in ub] + [[-v for v in services[g]] + [F(1)] for g in sorted(active)]
        b = [*rhs, *([F(0)] * len(active))]
        x = solve(
            [F(0)] * n + [F(-1)], A, b, [a + [F(0)] for a in eq], vals, bounds + [(None, None)]
        )
        level = x[-1]
        stage_A = ub + [[-v for v in services[g]] for g in sorted(active)]
        stage_b = rhs + [-level] * len(active)
        forced = _dual_bottlenecks(
            [F(0)] * n + [F(-1)],
            A,
            b,
            [a + [F(0)] for a in eq],
            vals,
            bounds + [(None, None)],
            active,
            services,
            x,
            level,
        )
        if not forced:
            for g in sorted(active):
                candidate = solve([-v for v in services[g]], stage_A, stage_b, eq, vals, bounds)
                if _dot(services[g], candidate) == level:
                    forced.append(g)
        if not forced:
            raise ArithmeticError("no certified bottleneck at lexicographic stage")
        # Retain this stage's lower bounds while freeing unfrozen groups to improve.
        ub.extend([[-v for v in services[g]] for g in sorted(active)])
        rhs.extend([-level] * len(active))
        for g in forced:
            eq.append(services[g])
            vals.append(level)
            active.remove(g)
    # The service vector is fixed. The exact simplex receives variables and rows in
    # canonical order, which makes its utility-optimal representative reproducible.
    x = solve([-v for v in utility])
    eq.append(utility)
    vals.append(_dot(utility, x))
    amounts = {g: {k: x[coordinates.index((g, k))] for k in p.categories} for g in p.groups}
    return FullBoxResult(
        amounts,
        {g: _dot(a, x) for g, a in services.items()},
        _dot(utility, x),
        certs,
        ("utility-first-" if utility_first else "")
        + "full-box-lexicographic"
        + ("-committed" if floors is not None else ""),
    )
