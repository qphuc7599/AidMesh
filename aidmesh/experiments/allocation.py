"""Paired synthetic allocation benchmark for the proved packing allocator."""

from __future__ import annotations

import hashlib
import math
from fractions import Fraction
from pathlib import Path

import pandas as pd

from aidmesh.allocator import (
    PackingProblem,
    allocation_baselines,
    compute_allocation_metrics,
    maximin_then_utility,
    progressive_fill,
)


def _uniform(seed: int, *parts: object) -> float:
    payload = "|".join([str(seed), *(str(part) for part in parts)]).encode()
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big") / 2**64


def generate_problem(
    seed: int,
    *,
    groups: int = 50,
    categories: int = 5,
    demand_skew: float = 1.0,
    stock_sufficiency: float = 0.6,
) -> PackingProblem:
    group_ids = tuple(f"g{i:03d}" for i in range(groups))
    category_ids = tuple(f"k{i:02d}" for i in range(categories))
    demand: dict[str, dict[str, int]] = {}
    directions: dict[str, dict[str, Fraction]] = {}
    weights: dict[str, Fraction] = {}
    for group_index, group in enumerate(group_ids):
        row: dict[str, int] = {}
        for category_index, category in enumerate(category_ids):
            uniform = max(1e-12, _uniform(seed, "demand", group_index, category_index))
            heavy_tail = (-math.log(uniform)) ** (1 / max(0.1, demand_skew))
            row[category] = max(1, 2 + int(12 * heavy_tail))
        demand[group] = row
        total = sum(row.values())
        directions[group] = {category: Fraction(row[category], total) for category in category_ids}
        weights[group] = Fraction(1 + (group_index % 3), 1)
    stock = {
        category: max(
            1,
            int(stock_sufficiency * sum(demand[group][category] for group in group_ids)),
        )
        for category in category_ids
    }
    return PackingProblem(
        cell="synthetic-cell",
        demand=demand,
        stock=stock,
        weights=weights,
        conversion_factors={category: 1 for category in category_ids},
        directions=directions,
        units={category: 1 for category in category_ids},
    )


def run_allocation_matrix(
    seeds: range,
    *,
    groups: int = 50,
    categories: int = 5,
    demand_skew: float = 1.0,
    stock_sufficiency: float = 0.6,
) -> pd.DataFrame:
    rows: list[dict] = []
    for seed in seeds:
        problem = generate_problem(
            seed,
            groups=groups,
            categories=categories,
            demand_skew=demand_skew,
            stock_sufficiency=stock_sufficiency,
        )
        allocations = dict(allocation_baselines(problem))
        allocations["aidmesh-progressive-fill"] = progressive_fill(problem)
        allocations["aidmesh-maximin-utility"] = maximin_then_utility(problem)
        metrics = {
            method: compute_allocation_metrics(allocation)
            for method, allocation in allocations.items()
        }
        utility = metrics["utility-only"].equivalent_fulfilled
        for method, result in metrics.items():
            rows.append(
                {
                    "seed": seed,
                    "method": method,
                    "groups": groups,
                    "categories": categories,
                    "demand_skew": demand_skew,
                    "stock_sufficiency": stock_sufficiency,
                    "minimum_service_ratio": float(result.minimum_service_ratio or 0),
                    "median_service_ratio": float(result.median_service_ratio or 0),
                    "gini_service_ratio": float(result.gini_service_ratio or 0),
                    "jain_service_ratio": float(result.jain_service_ratio or 0),
                    "equivalent_fulfilled": float(result.equivalent_fulfilled),
                    "utility_ratio": float(result.equivalent_fulfilled / utility),
                }
            )
    return pd.DataFrame(rows)


def write_allocation_results(frame: pd.DataFrame, output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output, index=False)
    return output
