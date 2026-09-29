"""Seed-clustered paired intervals for the post-review allocation experiments."""
from __future__ import annotations

import hashlib
import json
import sys
from collections import defaultdict
from fractions import Fraction
from pathlib import Path

import numpy as np

strong_dir = Path(sys.argv[1]).resolve()
coupled_dir = Path(sys.argv[2]).resolve()


def load_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def number(value):
    if isinstance(value, str) and "/" in value:
        return float(Fraction(value))
    return float(value)


def clustered_contrast(records, left, right, endpoint, *, cells):
    by_seed = defaultdict(list)
    wins = losses = ties = 0
    for record in records:
        rows = {row["method"]: row for row in record["rows"]}
        delta = number(rows[left][endpoint]) - number(rows[right][endpoint])
        by_seed[int(record["seed"])].append(delta)
        wins += delta > 0
        losses += delta < 0
        ties += delta == 0
    if any(len(values) != cells for values in by_seed.values()):
        raise AssertionError("incomplete seed cluster")
    cluster = np.array([np.mean(by_seed[seed]) for seed in sorted(by_seed)])
    rng = np.random.default_rng(20260910)
    indices = rng.integers(0, len(cluster), size=(20000, len(cluster)))
    boot = cluster[indices].mean(axis=1)
    return {
        "left": left,
        "right": right,
        "endpoint": endpoint,
        "pairedObservations": wins + losses + ties,
        "seedClusters": len(cluster),
        "meanDifference": float(cluster.mean()),
        "clusterBootstrap95": [float(np.quantile(boot, 0.025)), float(np.quantile(boot, 0.975))],
        "wins": int(wins),
        "ties": int(ties),
        "losses": int(losses),
    }


def clustered_relative_loss(records, left, optimum, endpoint, *, cells):
    by_seed = defaultdict(list)
    for record in records:
        rows = {row["method"]: row for row in record["rows"]}
        best = number(rows[optimum][endpoint])
        by_seed[int(record["seed"])].append((number(rows[left][endpoint]), best))
    if any(len(values) != cells for values in by_seed.values()):
        raise AssertionError("incomplete seed cluster")
    cluster = np.array([
        (sum(best for _, best in by_seed[seed]) - sum(value for value, _ in by_seed[seed]))
        / sum(best for _, best in by_seed[seed])
        for seed in sorted(by_seed)
    ])
    rng = np.random.default_rng(20260910)
    indices = rng.integers(0, len(cluster), size=(20000, len(cluster)))
    boot = cluster[indices].mean(axis=1)
    return {
        "method": left,
        "optimum": optimum,
        "endpoint": endpoint,
        "seedClusters": len(cluster),
        "meanSeedAggregateRelativeLoss": float(cluster.mean()),
        "clusterBootstrap95": [float(np.quantile(boot, .025)), float(np.quantile(boot, .975))],
    }


strong = load_jsonl(strong_dir / "runs.jsonl")
coupled = load_jsonl(coupled_dir / "runs.jsonl")
if len(strong) != 270 or len(coupled) != 120:
    raise AssertionError((len(strong), len(coupled)))

strong_contrasts = []
for method in [
    "full-box-leximin-then-weighted-utility",
    "weighted-utility-optimum-then-leximin",
    "fair-floor-completion",
]:
    for endpoint in ["minimum", "weighted_utility", "fulfillment"]:
        strong_contrasts.append(
            clustered_contrast(strong, method, "progressive-fill", endpoint, cells=9)
        )
for reference in ["demand-proportional", "utility-only"]:
    strong_contrasts.append(clustered_contrast(
        strong, "full-box-leximin-then-weighted-utility", reference, "minimum", cells=9
    ))

utility_loss = clustered_relative_loss(
    strong, "full-box-leximin-then-weighted-utility",
    "weighted-utility-optimum-then-leximin", "weighted_utility", cells=9,
)

coupled_contrasts = []
for reference in ["progressive-fill", "full-box-leximin"]:
    for endpoint in ["delivered", "settled", "delivered_min", "settled_min"]:
        coupled_contrasts.append(
            clustered_contrast(
                coupled, "delivery-aware-leximin", reference, endpoint, cells=4
            )
        )

by_partition = {}
for partition in sorted({int(row["partition"]) for row in coupled}):
    subset = [row for row in coupled if int(row["partition"]) == partition]
    by_partition[str(partition)] = {}
    for method in ["progressive-fill", "full-box-leximin", "delivery-aware-leximin"]:
        rows = [next(item for item in record["rows"] if item["method"] == method) for record in subset]
        by_partition[str(partition)][method] = {
            endpoint: sum(number(row[endpoint]) for row in rows) / len(rows)
            for endpoint in ["delivered", "settled", "delivered_min", "settled_min"]
        }

summary = {
    "method": "paired percentile bootstrap over the 30 independent seed clusters; settings remain paired within seed",
    "bootstrapReplicates": 20000,
    "rngSeed": 20260910,
    "strongAllocation": strong_contrasts,
    "strongAllocationUtilityLoss": utility_loss,
    "coupledDeliveredFairness": coupled_contrasts,
    "coupledMeansByPartitionHours": by_partition,
    "inputSha256": {
        "strong": hashlib.sha256((strong_dir / "runs.jsonl").read_bytes()).hexdigest(),
        "coupled": hashlib.sha256((coupled_dir / "runs.jsonl").read_bytes()).hexdigest(),
    },
    "scope": "post-review prospective synthetic experiments; intervals do not retrofit the original H1/H3/H4 holdout",
}
(strong_dir / "paired_analysis.json").write_text(json.dumps(summary, indent=2))
print(json.dumps(summary, indent=2))
