"""Run and analyze the fixed-size, disjoint-seed H3 replication.

The registry fixes 640 seed clusters before execution. Each seed is one resumable
shard containing all nine paired demand-skew/stock-sufficiency conditions. Exact
rational LP certificates are retained for both full-box optimizers.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
import platform
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from fractions import Fraction as F
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aidmesh.allocator import (  # noqa: E402
    allocation_baselines,
    normalize_problem,
    progressive_fill,
)
from aidmesh.allocator.full_box import lexicographic_full_box  # noqa: E402
from aidmesh.experiments.allocation import generate_problem  # noqa: E402


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def number(value: str | float | int) -> float:
    return float(F(value)) if isinstance(value, str) else float(value)


def utility(problem, amounts) -> F:
    problem = normalize_problem(problem)
    return sum(
        (
            problem.weights[group]
            * problem.conversion_factors[category]
            * amounts[group][category]
            for group in problem.groups
            for category in problem.categories
        ),
        F(0),
    )


def service_ratios(problem, amounts) -> dict[str, F]:
    problem = normalize_problem(problem)
    ratios = {}
    for group in problem.groups:
        denominator = problem.weights[group] * problem.aggregate_demand[group]
        if denominator > 0:
            ratios[group] = sum(
                (
                    problem.conversion_factors[category] * amounts[group][category]
                    for category in problem.categories
                ),
                F(0),
            ) / denominator
    return ratios


def run_seed(task: tuple[int, dict, str]) -> dict:
    seed, registry, output_text = task
    output = Path(output_text)
    started = time.perf_counter()
    records = []
    for skew, supply in itertools.product(
        registry["demandSkew"], registry["stockSufficiency"]
    ):
        problem = normalize_problem(
            generate_problem(
                seed,
                groups=int(registry["groups"]),
                categories=int(registry["categories"]),
                demand_skew=skew,
                stock_sufficiency=supply,
            )
        )
        baselines = allocation_baselines(problem)
        pf = progressive_fill(problem)
        leximin = lexicographic_full_box(problem)
        utility_optimum = lexicographic_full_box(problem, utility_first=True)
        methods = {
            "full-box-leximin-then-weighted-utility": leximin,
            "weighted-utility-optimum-then-leximin": utility_optimum,
            "progressive-fill": pf,
            "equal-share": baselines["equal-share"],
            "demand-proportional": baselines["demand-proportional"],
            "utility-only": baselines["utility-only"],
        }
        rows = []
        for method in registry["methods"]:
            allocation = methods[method]
            amounts = allocation.amounts
            ratios = (
                allocation.ratios
                if hasattr(allocation, "ratios")
                else service_ratios(problem, amounts)
            )
            rows.append(
                {
                    "method": method,
                    "minimum": str(min(ratios.values())),
                    "weightedUtility": str(utility(problem, amounts)),
                    "fulfillment": str(
                        sum(
                            (
                                amounts[group][category]
                                for group in problem.groups
                                for category in problem.categories
                            ),
                            F(0),
                        )
                    ),
                    "exactLpCertificates": len(
                        getattr(allocation, "certificates", [])
                    ),
                }
            )
        records.append(
            {"seed": seed, "demandSkew": skew, "stockSufficiency": supply, "rows": rows}
        )
    payload = {
        "seed": seed,
        "conditions": records,
        "elapsedSeconds": time.perf_counter() - started,
    }
    shard = output / "shards" / f"seed-{seed:06d}.json"
    temporary = shard.with_suffix(f".json.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")
    os.replace(temporary, shard)
    return {"seed": seed, "seconds": payload["elapsedSeconds"]}


def freeze(registry_path: Path, output: Path) -> None:
    if output.exists():
        raise FileExistsError(f"refusing to overwrite existing campaign: {output}")
    output.mkdir(parents=True)
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    sources = [
        registry_path,
        Path(__file__).resolve(),
        ROOT / "aidmesh/allocator/full_box.py",
        ROOT / "aidmesh/allocator/progressive_fill.py",
        ROOT / "aidmesh/allocator/baselines.py",
        ROOT / "aidmesh/experiments/allocation.py",
    ]
    manifest = {
        "status": "frozen-before-execution",
        "registry": registry_path.resolve().as_posix(),
        "registrySha256": sha256(registry_path),
        "sourceSha256": {
            path.relative_to(ROOT).as_posix(): sha256(path) for path in sources[1:]
        },
        "expectedSeeds": int(registry["seedCount"]),
        "expectedConditions": int(registry["seedCount"])
        * len(registry["demandSkew"])
        * len(registry["stockSufficiency"]),
    }
    (output / "freeze_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    (output / "shards").mkdir()


def verify_freeze(registry_path: Path, output: Path) -> dict:
    manifest = json.loads((output / "freeze_manifest.json").read_text(encoding="utf-8"))
    if manifest["registrySha256"] != sha256(registry_path):
        raise RuntimeError("registry changed after freeze")
    for relative, expected in manifest["sourceSha256"].items():
        if sha256(ROOT / relative) != expected:
            raise RuntimeError(f"source changed after freeze: {relative}")
    return manifest


def execute(registry_path: Path, output: Path, workers: int) -> None:
    verify_freeze(registry_path, output)
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    seeds = range(
        int(registry["seedStart"]),
        int(registry["seedStart"]) + int(registry["seedCount"]),
    )
    pending = [
        seed for seed in seeds if not (output / "shards" / f"seed-{seed:06d}.json").is_file()
    ]
    print(
        json.dumps(
            {"seeds": registry["seedCount"], "pending": len(pending), "workers": workers}
        ),
        flush=True,
    )
    failures = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(run_seed, (seed, registry, str(output.resolve()))): seed
            for seed in pending
        }
        completed = int(registry["seedCount"]) - len(pending)
        for future in as_completed(futures):
            seed = futures[future]
            try:
                result = future.result()
                completed += 1
                print(
                    json.dumps(
                        {"progress": f"{completed}/{registry['seedCount']}", **result}
                    ),
                    flush=True,
                )
            except Exception as exc:
                failures.append({"seed": seed, "error": repr(exc)})
                print(json.dumps({"failed": seed, "error": repr(exc)}), flush=True)
    if failures:
        (output / "failures.json").write_text(json.dumps(failures, indent=2) + "\n")
        raise RuntimeError(f"{len(failures)} H3 seed shards failed")


def bootstrap_mean(values: np.ndarray, replicates: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    result = np.empty(replicates, dtype=float)
    chunk = 2000
    for start in range(0, replicates, chunk):
        stop = min(replicates, start + chunk)
        indices = rng.integers(0, len(values), size=(stop - start, len(values)))
        result[start:stop] = values[indices].mean(axis=1)
    return result


def analyze(registry_path: Path, output: Path) -> None:
    manifest = verify_freeze(registry_path, output)
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    seeds = list(
        range(
            int(registry["seedStart"]),
            int(registry["seedStart"]) + int(registry["seedCount"]),
        )
    )
    paths = [output / "shards" / f"seed-{seed:06d}.json" for seed in seeds]
    missing = [path.name for path in paths if not path.is_file()]
    if missing:
        raise RuntimeError(f"cannot analyze incomplete campaign; missing {len(missing)} shards")
    payloads = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    expected_cells = len(registry["demandSkew"]) * len(registry["stockSufficiency"])
    if any(len(payload["conditions"]) != expected_cells for payload in payloads):
        raise RuntimeError("incomplete within-seed condition grid")

    target = "full-box-leximin-then-weighted-utility"
    optimum = "weighted-utility-optimum-then-leximin"
    losses = []
    fairness: dict[str, list[float]] = {
        method: [] for method in registry["primaryFairnessComparators"]
    }
    wins = {method: 0 for method in fairness}
    ties = {method: 0 for method in fairness}
    losses_count = {method: 0 for method in fairness}
    for payload in payloads:
        target_utility = optimum_utility = 0.0
        seed_deltas = {method: [] for method in fairness}
        for condition in payload["conditions"]:
            rows = {row["method"]: row for row in condition["rows"]}
            target_utility += number(rows[target]["weightedUtility"])
            optimum_utility += number(rows[optimum]["weightedUtility"])
            for method in fairness:
                delta = number(rows[target]["minimum"]) - number(rows[method]["minimum"])
                seed_deltas[method].append(delta)
                wins[method] += int(delta > 0)
                ties[method] += int(delta == 0)
                losses_count[method] += int(delta < 0)
        losses.append((optimum_utility - target_utility) / optimum_utility)
        for method in fairness:
            fairness[method].append(float(np.mean(seed_deltas[method])))

    reps = int(registry["bootstrapReplicates"])
    rng_seed = int(registry["bootstrapSeed"])
    loss_values = np.asarray(losses)
    loss_boot = bootstrap_mean(loss_values, reps, rng_seed)
    utility_result = {
        "seedClusters": len(loss_values),
        "meanRelativeLoss": float(loss_values.mean()),
        "oneSided95Upper": float(np.quantile(loss_boot, 0.95)),
        "twoSided95": [float(np.quantile(loss_boot, 0.025)), float(np.quantile(loss_boot, 0.975))],
        "margin": float(registry["utilityLossMargin"]),
    }
    fairness_results = []
    for offset, (method, values) in enumerate(fairness.items(), start=1):
        array = np.asarray(values)
        boot = bootstrap_mean(array, reps, rng_seed + offset)
        fairness_results.append(
            {
                "comparator": method,
                "seedClusters": len(array),
                "pairedConditions": len(array) * expected_cells,
                "meanMinimumServiceDifference": float(array.mean()),
                "twoSided95": [float(np.quantile(boot, 0.025)), float(np.quantile(boot, 0.975))],
                "conditionWins": wins[method],
                "conditionTies": ties[method],
                "conditionLosses": losses_count[method],
            }
        )
    decision = (
        utility_result["oneSided95Upper"] <= utility_result["margin"]
        and all(row["twoSided95"][0] > 0 for row in fairness_results)
    )
    summary = {
        "profile": registry["profile"],
        "status": "complete",
        "decision": "supported" if decision else "not-supported",
        "decisionRule": registry["decisionRule"],
        "utilityLoss": utility_result,
        "fairnessContrasts": fairness_results,
        "coverage": {
            "seedClusters": len(payloads),
            "conditions": len(payloads) * expected_cells,
            "methodRows": len(payloads) * expected_cells * len(registry["methods"]),
            "missingShards": 0,
        },
        "runtimeSeconds": float(sum(payload["elapsedSeconds"] for payload in payloads)),
        "environment": {"python": sys.version, "platform": platform.platform()},
        "scope": registry["scope"],
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    checksums = [*paths, output / "summary.json", output / "freeze_manifest.json"]
    (output / "CHECKSUMS.sha256").write_text(
        "".join(f"{sha256(path)}  {path.relative_to(output).as_posix()}\n" for path in checksums),
        encoding="ascii",
    )
    manifest["status"] = "complete"
    manifest["summarySha256"] = sha256(output / "summary.json")
    (output / "execution_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("freeze", "run", "analyze"))
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=max(1, min(10, (os.cpu_count() or 2) - 2)))
    args = parser.parse_args()
    if args.phase == "freeze":
        freeze(args.registry, args.output)
    elif args.phase == "run":
        execute(args.registry, args.output, args.workers)
    else:
        analyze(args.registry, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
