"""Add the prespecified equal-share baseline to the immutable 270-instance suite.

The script regenerates each problem from its recorded key and refuses to continue
unless the regenerated demand-proportional result exactly matches the archived row.
It therefore adds a comparator without rerunning or replacing the selected methods.
"""

from __future__ import annotations

import hashlib
import json
import sys
from fractions import Fraction as F
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aidmesh.allocator import (  # noqa: E402
    allocation_baselines,
    normalize_problem,
)
from aidmesh.experiments.allocation import generate_problem  # noqa: E402

SOURCE = ROOT / "results/strong_allocation_20260910_v6/runs.jsonl"
OUTPUT = ROOT / "results/strong_allocation_equal_share_20260910"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def endpoints(problem, allocation) -> dict[str, F]:
    problem = normalize_problem(problem)
    amounts = allocation.amounts
    ratios = {
        group: sum(
            problem.conversion_factors[category] * amounts[group][category]
            for category in problem.categories
        )
        / (problem.weights[group] * problem.aggregate_demand[group])
        for group in problem.groups
    }
    return {
        "minimum": min(ratios.values()),
        "weighted_utility": sum(
            (
                problem.weights[group]
                * problem.conversion_factors[category]
                * amounts[group][category]
                for group in problem.groups
                for category in problem.categories
            ),
            F(0),
        ),
        "fulfillment": sum(
            (
                amounts[group][category]
                for group in problem.groups
                for category in problem.categories
            ),
            F(0),
        ),
    }


records = [json.loads(line) for line in SOURCE.read_text(encoding="utf-8").splitlines()]
if len(records) != 270 or len({row["key"] for row in records}) != 270:
    raise ValueError("source suite is not the complete 270-instance campaign")

rows = []
deltas = []
for record in records:
    problem = normalize_problem(
        generate_problem(
            record["seed"],
            groups=12,
            categories=3,
            demand_skew=record["skew"],
            stock_sufficiency=record["supply"],
        )
    )
    baselines = allocation_baselines(problem)
    regenerated = endpoints(problem, baselines["demand-proportional"])
    archived = next(row for row in record["rows"] if row["method"] == "demand-proportional")
    for endpoint, value in regenerated.items():
        if value != F(archived[endpoint]):
            raise ValueError(f"problem regeneration mismatch at {record['key']}/{endpoint}")

    equal = endpoints(problem, baselines["equal-share"])
    leximin = next(
        row for row in record["rows"] if row["method"] == "full-box-leximin-then-weighted-utility"
    )
    rows.append(
        {
            "key": record["key"],
            "seed": record["seed"],
            "skew": record["skew"],
            "supply": record["supply"],
            "method": "equal-share",
            **{name: str(value) for name, value in equal.items()},
        }
    )
    deltas.append(
        {
            "seed": record["seed"],
            "minimum": float(F(leximin["minimum"]) - equal["minimum"]),
            "utility": float(F(leximin["weighted_utility"]) - equal["weighted_utility"]),
        }
    )

OUTPUT.mkdir(parents=True, exist_ok=False)
raw = OUTPUT / "runs.jsonl"
raw.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

seed_values = {}
for seed in sorted({row["seed"] for row in deltas}):
    sample = [row for row in deltas if row["seed"] == seed]
    seed_values[seed] = {
        endpoint: float(np.mean([row[endpoint] for row in sample]))
        for endpoint in ("minimum", "utility")
    }

rng = np.random.default_rng(20260910)
seed_ids = np.asarray(sorted(seed_values))


def interval(endpoint: str) -> list[float]:
    values = np.asarray([seed_values[seed][endpoint] for seed in seed_ids])
    draws = rng.choice(values, size=(20_000, len(values)), replace=True).mean(axis=1)
    return [float(np.quantile(draws, 0.025)), float(np.quantile(draws, 0.975))]


summary = {
    "profile": "post-review equal-share addition to exact 270-instance suite",
    "instances": len(rows),
    "problemRegenerationChecks": 270 * 3,
    "allProblemRegenerationChecksPassed": True,
    "equalShareMeans": {
        endpoint: float(np.mean([float(F(row[endpoint])) for row in rows]))
        for endpoint in ("minimum", "weighted_utility", "fulfillment")
    },
    "fullBoxLeximinMinusEqualShare": {
        "minimum": {
            "mean": float(np.mean([row["minimum"] for row in deltas])),
            "seedClusterBootstrap95": interval("minimum"),
            "winsTiesLosses": [
                sum(row["minimum"] > 0 for row in deltas),
                sum(row["minimum"] == 0 for row in deltas),
                sum(row["minimum"] < 0 for row in deltas),
            ],
        },
        "weightedUtility": {
            "mean": float(np.mean([row["utility"] for row in deltas])),
            "seedClusterBootstrap95": interval("utility"),
        },
    },
    "sourceRunsSha256": sha256(SOURCE),
    "rawSha256": sha256(raw),
    "scope": "same immutable problems and endpoint definitions as strong_allocation_20260910_v6",
}
(OUTPUT / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
(OUTPUT / "manifest.json").write_text(
    json.dumps(
        {
            "scriptSha256": sha256(Path(__file__)),
            "sourceRuns": str(SOURCE.relative_to(ROOT)).replace("\\", "/"),
            "sourceRunsSha256": sha256(SOURCE),
            "rawSha256": sha256(raw),
        },
        indent=2,
    ),
    encoding="utf-8",
)
print(json.dumps(summary, indent=2))
