"""Matched sensitivity of full-box maximin allocation to policy parameters.

The primary allocation campaign uses exact rational certificates.  This secondary
campaign changes published weights, category conversion factors, and an essential-
goods floor while holding every generated demand/stock instance fixed.  HiGHS is
used only for sensitivity; every returned allocation is independently checked.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
import time
from dataclasses import replace
from fractions import Fraction as F
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import linprog

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aidmesh.allocator import PackingProblem, normalize_problem  # noqa: E402
from aidmesh.experiments.allocation import generate_problem  # noqa: E402


SEEDS = tuple(range(99701, 99731))
SKEWS = (0.2, 1.0, 5.0)
SUPPLY = (0.35, 0.6, 0.9)
PROFILES = (
    "registered",
    "weights-compressed",
    "weights-cyclic-pm20",
    "conversion-forward-pm20",
    "conversion-reverse-pm20",
    "essential-floor-10pct",
    "combined-stress",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def perturb(problem: PackingProblem, profile: str) -> tuple[PackingProblem, dict]:
    groups = tuple(sorted(problem.demand))
    categories = tuple(sorted(problem.stock))
    weights = {group: F(problem.weights[group]) for group in groups}
    conversions = {category: F(problem.conversion_factors[category]) for category in categories}
    essential_fraction = F(0)
    if profile in {"weights-compressed"}:
        weights = {group: F(1) + (weights[group] - 1) / 2 for group in groups}
    if profile in {"weights-cyclic-pm20", "combined-stress"}:
        multipliers = (F(4, 5), F(1), F(6, 5))
        weights = {
            group: weights[group] * multipliers[index % len(multipliers)]
            for index, group in enumerate(groups)
        }
    if profile in {"conversion-forward-pm20", "combined-stress"}:
        multipliers = tuple(F(4, 5) + F(index, 5 * max(1, len(categories) - 1)) * 2
                            for index in range(len(categories)))
        conversions = {
            category: conversions[category] * multipliers[index]
            for index, category in enumerate(categories)
        }
    if profile == "conversion-reverse-pm20":
        multipliers = tuple(F(6, 5) - F(index, 5 * max(1, len(categories) - 1)) * 2
                            for index in range(len(categories)))
        conversions = {
            category: conversions[category] * multipliers[index]
            for index, category in enumerate(categories)
        }
    if profile in {"essential-floor-10pct", "combined-stress"}:
        essential_fraction = F(1, 10)
    directions = {}
    for group in groups:
        converted_demand = sum(
            (conversions[category] * F(problem.demand[group][category]) for category in categories),
            F(0),
        )
        directions[group] = {
            category: F(problem.demand[group][category]) / converted_demand
            for category in categories
        }
    return (
        replace(
            problem,
            weights=weights,
            conversion_factors=conversions,
            directions=directions,
        ),
        {"essential_category": categories[0], "essential_fraction": essential_fraction},
    )


def solve(problem: PackingProblem, policy: dict) -> dict:
    p = normalize_problem(problem)
    coordinates = [(group, category) for group in p.groups for category in p.categories]
    index = {coordinate: position for position, coordinate in enumerate(coordinates)}
    n = len(coordinates)
    floor = {coordinate: F(0) for coordinate in coordinates}
    essential = policy["essential_category"]
    fraction = F(policy["essential_fraction"])
    if fraction:
        for group in p.groups:
            floor[group, essential] = fraction * p.demand[group][essential]
    for category in p.categories:
        if sum((floor[group, category] for group in p.groups), F(0)) > p.stock[category]:
            raise ValueError("essential-goods floor exceeds stock")

    stock_rows: list[list[float]] = []
    stock_rhs: list[float] = []
    for category in p.categories:
        row = [0.0] * n
        for group in p.groups:
            row[index[group, category]] = 1.0
        stock_rows.append(row)
        stock_rhs.append(float(p.stock[category]))
    bounds = [
        (float(floor[group, category]), float(p.demand[group][category]))
        for group, category in coordinates
    ]
    denominators = {
        group: float(p.weights[group] * p.aggregate_demand[group]) for group in p.groups
    }
    service_rows: dict[str, list[float]] = {}
    for group in p.groups:
        row = [0.0] * n
        for category in p.categories:
            row[index[group, category]] = (
                float(p.conversion_factors[category]) / denominators[group]
            )
        service_rows[group] = row

    # Floating-point replica of the exact primary lexicographic solver.  Each
    # stage freezes every service coordinate certified as a bottleneck by the
    # HiGHS dual; a per-group LP resolves degenerate faces.
    active = set(p.groups)
    equality_rows: list[list[float]] = []
    equality_rhs: list[float] = []
    first_floor = None
    while active:
        ordered = sorted(active)
        active_rows = []
        for group in ordered:
            active_rows.append([-value for value in service_rows[group]] + [1.0])
        stage = linprog(
            np.r_[np.zeros(n), -1.0],
            A_ub=np.asarray([row + [0.0] for row in stock_rows] + active_rows),
            b_ub=np.asarray(stock_rhs + [0.0] * len(active_rows)),
            A_eq=(
                np.asarray([row + [0.0] for row in equality_rows])
                if equality_rows
                else None
            ),
            b_eq=np.asarray(equality_rhs) if equality_rows else None,
            bounds=bounds + [(0.0, None)],
            method="highs",
            options={"primal_feasibility_tolerance": 1e-9, "dual_feasibility_tolerance": 1e-9},
        )
        if not stage.success:
            raise RuntimeError(stage.message)
        level = float(stage.x[-1])
        if first_floor is None:
            first_floor = level
        marginals = stage.ineqlin.marginals[-len(ordered):]
        forced = [
            group
            for group, marginal in zip(ordered, marginals, strict=True)
            if abs(float(marginal)) > 1e-8
        ]
        if not forced:
            lower_rows = stock_rows + [
                [-value for value in service_rows[group]] for group in ordered
            ]
            lower_rhs = stock_rhs + [-level + 1e-9] * len(ordered)
            for group in ordered:
                probe = linprog(
                    -np.asarray(service_rows[group]),
                    A_ub=np.asarray(lower_rows),
                    b_ub=np.asarray(lower_rhs),
                    A_eq=np.asarray(equality_rows) if equality_rows else None,
                    b_eq=np.asarray(equality_rhs) if equality_rows else None,
                    bounds=bounds,
                    method="highs",
                )
                if not probe.success:
                    raise RuntimeError(probe.message)
                if -float(probe.fun) <= level + 1e-7:
                    forced.append(group)
        if not forced:
            raise RuntimeError("no lexicographic bottleneck identified")
        for group in forced:
            equality_rows.append(service_rows[group])
            equality_rhs.append(level)
            active.remove(group)

    utility_coefficients = np.asarray(
        [float(p.weights[group] * p.conversion_factors[category]) for group, category in coordinates]
    )
    fair = linprog(
        -utility_coefficients,
        A_ub=np.asarray(stock_rows),
        b_ub=np.asarray(stock_rhs),
        A_eq=np.asarray(equality_rows),
        b_eq=np.asarray(equality_rhs),
        bounds=bounds,
        method="highs",
        options={"primal_feasibility_tolerance": 1e-9, "dual_feasibility_tolerance": 1e-9},
    )
    optimum = linprog(
        -utility_coefficients,
        A_ub=np.asarray(stock_rows),
        b_ub=np.asarray(stock_rhs),
        bounds=bounds,
        method="highs",
        options={"primal_feasibility_tolerance": 1e-9, "dual_feasibility_tolerance": 1e-9},
    )
    if not fair.success or not optimum.success:
        raise RuntimeError("stage-two or utility-only LP failed")

    def metrics(values: np.ndarray) -> tuple[float, float, float]:
        services = []
        for group in p.groups:
            fulfilled = sum(
                float(p.conversion_factors[category]) * values[index[group, category]]
                for category in p.categories
            )
            services.append(fulfilled / denominators[group])
        utility = float(np.dot(utility_coefficients, values))
        max_stock_excess = max(
            sum(values[index[group, category]] for group in p.groups) - float(p.stock[category])
            for category in p.categories
        )
        max_bound_violation = max(
            max(lower - value, value - upper, 0.0)
            for value, (lower, upper) in zip(values, bounds, strict=True)
        )
        if max_stock_excess > 1e-6 or max_bound_violation > 1e-6:
            raise AssertionError("independent feasibility check failed")
        return min(services), utility, max(max_stock_excess, max_bound_violation, 0.0)

    fair_minimum, fair_utility, fair_violation = metrics(fair.x)
    utility_minimum, utility_utility, utility_violation = metrics(optimum.x)
    return {
        "fair_minimum": fair_minimum,
        "utility_minimum": utility_minimum,
        "minimum_gain": fair_minimum - utility_minimum,
        "fair_utility": fair_utility,
        "utility_optimum": utility_utility,
        "utility_retention": fair_utility / utility_utility,
        "max_feasibility_violation": max(fair_violation, utility_violation),
        "stage_one_floor": first_floor,
    }


def clustered_interval(frame: pd.DataFrame, column: str, rng: np.random.Generator) -> list[float]:
    by_seed = frame.groupby("seed", sort=True)[column].mean().to_numpy()
    draws = rng.choice(by_seed, size=(20_000, len(by_seed)), replace=True).mean(axis=1)
    return [float(value) for value in np.quantile(draws, [0.025, 0.975])]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    rows = []
    for seed in SEEDS:
        for skew in SKEWS:
            for supply in SUPPLY:
                base = generate_problem(
                    seed, groups=12, categories=3, demand_skew=skew, stock_sufficiency=supply
                )
                for profile in PROFILES:
                    problem, policy = perturb(base, profile)
                    rows.append(
                        {
                            "seed": seed,
                            "demand_skew": skew,
                            "stock_sufficiency": supply,
                            "profile": profile,
                            **solve(problem, policy),
                        }
                    )
    frame = pd.DataFrame(rows)
    raw_path = args.output / "allocation_policy_sensitivity.csv"
    frame.to_csv(raw_path, index=False)
    rng = np.random.default_rng(20260926)
    summaries = []
    for profile, group in frame.groupby("profile", sort=False):
        seed_utility = group.groupby("seed", sort=True).apply(
            lambda rows: rows.fair_utility.sum() / rows.utility_optimum.sum(),
            include_groups=False,
        )
        utility_draws = rng.choice(
            seed_utility.to_numpy(), size=(20_000, len(seed_utility)), replace=True
        ).mean(axis=1)
        summaries.append(
            {
                "profile": profile,
                "instances": len(group),
                "mean_minimum_gain": float(group.minimum_gain.mean()),
                "minimum_gain_seed_cluster_95": clustered_interval(group, "minimum_gain", rng),
                "fairness_wins": int((group.minimum_gain > 1e-8).sum()),
                "fairness_ties": int((group.minimum_gain.abs() <= 1e-8).sum()),
                "fairness_losses": int((group.minimum_gain < -1e-8).sum()),
                "mean_utility_retention": float(group.utility_retention.mean()),
                "utility_retention_seed_cluster_95": clustered_interval(
                    group, "utility_retention", rng
                ),
                "mean_seed_aggregate_utility_retention": float(seed_utility.mean()),
                "seed_aggregate_utility_retention_95": [
                    float(value) for value in np.quantile(utility_draws, [0.025, 0.975])
                ],
                "minimum_seed_aggregate_utility_retention": float(seed_utility.min()),
                "minimum_utility_retention": float(group.utility_retention.min()),
                "maximum_feasibility_violation": float(group.max_feasibility_violation.max()),
            }
        )
    script = Path(__file__).resolve()
    generator = ROOT / "aidmesh/experiments/allocation.py"
    summary = {
        "profile": "allocation-policy-sensitivity",
        "design": {
            "seeds": [SEEDS[0], SEEDS[-1]],
            "demand_skew": SKEWS,
            "stock_sufficiency": SUPPLY,
            "policy_profiles": PROFILES,
            "instances_per_profile": len(SEEDS) * len(SKEWS) * len(SUPPLY),
            "paired_unit": "same seed, demand skew, and supply setting",
            "interval": "20,000 seed-cluster bootstrap draws",
            "role": "secondary policy sensitivity; exact rational primary campaign unchanged",
        },
        "summaries": summaries,
        "all_feasible": bool(frame.max_feasibility_violation.max() <= 1e-6),
        "runtime_seconds": time.perf_counter() - started,
        "environment": {"python": sys.version, "platform": platform.platform()},
        "source_sha256": {
            str(script.relative_to(ROOT)): sha256(script),
            str(generator.relative_to(ROOT)): sha256(generator),
        },
        "raw_sha256": sha256(raw_path),
    }
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
