"""Evaluate the unchanged fulfilled-unit criterion from the original H3 contract."""

from __future__ import annotations

import hashlib
import json
from fractions import Fraction
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "configs/postreview_20260928/h3_original_contract_analysis.json"
CAMPAIGN = ROOT / "results/h3_fixed_replication_20260928"
ANALYSIS_FREEZE = CAMPAIGN / "ORIGINAL_CONTRACT_ANALYSIS_FREEZE.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def number(value: str | int | float) -> float:
    return float(Fraction(str(value)))


def bootstrap_mean(values: np.ndarray, replicates: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    result = np.empty(replicates, dtype=float)
    for start in range(0, replicates, 2000):
        stop = min(replicates, start + 2000)
        indices = rng.integers(0, len(values), size=(stop - start, len(values)))
        result[start:stop] = values[indices].mean(axis=1)
    return result


def main() -> int:
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    freeze = json.loads((CAMPAIGN / "freeze_manifest.json").read_text(encoding="utf-8"))
    analysis_freeze = json.loads(ANALYSIS_FREEZE.read_text(encoding="utf-8"))
    for relative, digest in analysis_freeze["sha256"].items():
        if sha256(ROOT / relative) != digest:
            raise RuntimeError(f"original H3 analysis freeze mismatch: {relative}")
    seed_start = 200001
    paths = [
        CAMPAIGN / "shards" / f"seed-{seed:06d}.json"
        for seed in range(seed_start, seed_start + int(plan["seedCount"]))
    ]
    missing = [path.name for path in paths if not path.is_file()]
    if missing:
        raise RuntimeError(f"cannot analyze incomplete campaign: {len(missing)} shards missing")

    target = plan["target"]
    control = plan["efficiencyComparator"]
    fulfilled_losses = []
    fairness = {name: [] for name in plan["fairnessComparators"]}
    counts = {name: {"wins": 0, "ties": 0, "losses": 0} for name in fairness}
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        target_fulfilled = 0.0
        control_fulfilled = 0.0
        seed_deltas = {name: [] for name in fairness}
        for condition in payload["conditions"]:
            rows = {row["method"]: row for row in condition["rows"]}
            target_fulfilled += number(rows[target]["fulfillment"])
            control_fulfilled += number(rows[control]["fulfillment"])
            for name in fairness:
                delta = number(rows[target]["minimum"]) - number(rows[name]["minimum"])
                seed_deltas[name].append(delta)
                bucket = "wins" if delta > 0 else "losses" if delta < 0 else "ties"
                counts[name][bucket] += 1
        fulfilled_losses.append((control_fulfilled - target_fulfilled) / control_fulfilled)
        for name in fairness:
            fairness[name].append(float(np.mean(seed_deltas[name])))

    repetitions = int(plan["bootstrapReplicates"])
    bootstrap_seed = int(plan["bootstrapSeed"])
    efficiency_values = np.asarray(fulfilled_losses)
    efficiency_boot = bootstrap_mean(efficiency_values, repetitions, bootstrap_seed)
    efficiency = {
        "seedClusters": len(efficiency_values),
        "meanRelativeFulfilledUnitLoss": float(efficiency_values.mean()),
        "oneSided95Upper": float(np.quantile(efficiency_boot, 0.95)),
        "twoSided95": [
            float(np.quantile(efficiency_boot, 0.025)),
            float(np.quantile(efficiency_boot, 0.975)),
        ],
        "margin": float(plan["margin"]),
    }
    contrasts = []
    for offset, (name, values) in enumerate(fairness.items(), start=1):
        array = np.asarray(values)
        boot = bootstrap_mean(array, repetitions, bootstrap_seed + offset)
        contrasts.append(
            {
                "comparator": name,
                "seedClusters": len(array),
                "meanMinimumServiceDifference": float(array.mean()),
                "twoSided95": [
                    float(np.quantile(boot, 0.025)),
                    float(np.quantile(boot, 0.975)),
                ],
                "conditionCounts": counts[name],
            }
        )
    decision = efficiency["oneSided95Upper"] <= efficiency["margin"] and all(
        row["twoSided95"][0] > 0 for row in contrasts
    )
    result = {
        "profile": "inherited original H3 fulfilled-unit contract",
        "status": "complete",
        "decision": "supported" if decision else "not-supported",
        "decisionRule": plan["decisionRule"],
        "efficiency": efficiency,
        "fairnessContrasts": contrasts,
        "coverage": {
            "seedClusters": len(paths),
            "conditions": len(paths) * int(plan["repeatedConditionsPerSeed"]),
            "missingShards": 0,
        },
        "provenance": {
            "analysisPlanSha256": sha256(PLAN),
            "campaignRegistrySha256": freeze["registrySha256"],
            "campaignSummarySha256": sha256(CAMPAIGN / "summary.json"),
        },
    }
    output = CAMPAIGN / "original_contract_analysis.json"
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    (CAMPAIGN / "ORIGINAL_CONTRACT_CHECKSUMS.sha256").write_text(
        "".join(
            f"{sha256(path)}  {path.relative_to(ROOT).as_posix()}\n"
            for path in (PLAN, Path(__file__).resolve(), CAMPAIGN / "summary.json", output)
        ),
        encoding="ascii",
    )
    print(json.dumps(result, indent=2))
    return 0 if decision else 2


if __name__ == "__main__":
    raise SystemExit(main())
