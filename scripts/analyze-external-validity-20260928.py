"""Analyze the fixed three-family external-validity replication.

Inference is over the 30 registered synthetic-overlay seeds. Contexts are first
averaged within trace family; the pooled row gives each of the three families
equal weight. It is therefore a trace-bounded replication, not inference to a
population of mobility datasets.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results/external_validity_three_family_20260928"
REGISTRY = ROOT / "configs/postreview_20260928/external_validity_three_family.yaml"
RUNS = RESULTS / "bow_primary_runs.csv"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def interval(values: np.ndarray, repetitions: int, seed: int) -> dict:
    values = np.asarray(values, dtype=float)
    rng = np.random.default_rng(seed)
    samples = values[rng.integers(0, len(values), size=(repetitions, len(values)))]
    means = samples.mean(axis=1)
    return {
        "mean": float(values.mean()),
        "ci95": [float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))],
        "seeds": int(len(values)),
        "wins": int((values > 0).sum()),
        "ties": int((values == 0).sum()),
        "losses": int((values < 0).sum()),
    }


def verify_freeze(registry: dict) -> dict:
    freeze = json.loads((RESULTS / "PRE_RUN_FREEZE.json").read_text(encoding="utf-8"))
    source_checks = {
        relative: sha256(ROOT / relative) == digest
        for relative, digest in freeze["sourceSha256"].items()
    }
    trace_checks = {
        relative: sha256(ROOT / relative) == digest
        for relative, digest in freeze["traceSha256"].items()
    }
    checks = {
        "registryHash": sha256(REGISTRY) == freeze["registrySha256"],
        "analysisScriptHash": sha256(ROOT / freeze["analysisScript"])
        == freeze["analysisScriptSha256"],
        "sourceHashes": all(source_checks.values()),
        "traceHashes": all(trace_checks.values()),
        "registeredSeeds": len(registry["seeds"]) == 30
        and len(set(registry["seeds"])) == 30,
    }
    return {"checks": checks, "passed": all(checks.values())}


def main() -> int:
    registry = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
    frozen = verify_freeze(registry)
    if not frozen["passed"]:
        raise RuntimeError(f"pre-run freeze verification failed: {frozen}")
    frame = pd.read_csv(RUNS)
    keys = ["family", "registered_context", "seed", "cap", "quorum_availability"]
    if frame.duplicated([*keys, "policy"]).any():
        raise RuntimeError("duplicate matched policy cells")
    wide = frame.pivot(index=keys, columns="policy", values="completion_rate")
    expected_policies = {
        "adatransfer",
        "optimized_static",
        "periodic_sync",
    }
    if not expected_policies.issubset(wide.columns):
        raise RuntimeError("one or more registered contrast policies are absent")

    repetitions = int(registry["bootstrap_repetitions"])
    rows = []
    raw = []
    controls = ("optimized_static", "periodic_sync")
    for control_index, control in enumerate(controls):
        differences = (wide["adatransfer"] - wide[control]).rename("difference").reset_index()
        context_seed = (
            differences.groupby(["family", "registered_context", "seed"], as_index=False)[
                "difference"
            ].mean()
        )
        family_seed = (
            context_seed.groupby(["family", "seed"], as_index=False)["difference"].mean()
        )
        for family_index, (family, group) in enumerate(family_seed.groupby("family")):
            result = interval(
                group["difference"].to_numpy(),
                repetitions,
                20260928 + 100 * control_index + family_index,
            )
            rows.append({"level": "family", "family": family, "control": control, **result})
        overall = family_seed.groupby("seed", as_index=False)["difference"].mean()
        result = interval(
            overall["difference"].to_numpy(),
            repetitions,
            20261928 + control_index,
        )
        rows.append(
            {"level": "family-balanced", "family": "all-three", "control": control, **result}
        )
        context_seed["control"] = control
        raw.append(context_seed)

    effects = pd.DataFrame(rows)
    raw_effects = pd.concat(raw, ignore_index=True)
    effects.to_csv(RESULTS / "fixed_family_effects.csv", index=False, lineterminator="\n")
    raw_effects.to_csv(
        RESULTS / "fixed_context_seed_effects.csv", index=False, lineterminator="\n"
    )

    expected_cells = sum(
        json.loads(path.read_text(encoding="utf-8"))["rows"]
        for path in sorted((RESULTS / "shards").glob("*.json"))
    )
    safety = {
        "riskCapViolations": int(frame["risk_cap_violation"].sum()),
        "generatedRequests": int(frame["generated_requests"].sum()),
        "duplicateExposure": float(frame["duplicate_exposure"].sum()),
    }
    checks = {
        "mergedRowsMatchShards": len(frame) == expected_cells,
        "thirtySeeds": sorted(frame["seed"].unique().tolist())
        == sorted(registry["seeds"]),
        "threeFamilies": frame["family"].nunique() == 3,
        "fiveContexts": frame["registered_context"].nunique() == 5,
        "noDuplicateCells": not frame.duplicated([*keys, "policy"]).any(),
        "zeroRiskCapViolations": safety["riskCapViolations"] == 0,
    }

    palette = {"optimized_static": "#1f5a93", "periodic_sync": "#d36b1f"}
    order = ["ene5ai", "sociopatterns", "umass_dieselnet", "all-three"]
    labels = ["ENE5AI", "SocioPatterns", "DieselNet", "Family-balanced"]
    fig, axes = plt.subplots(1, 2, figsize=(8.2, 3.0), sharey=True)
    for axis, control in zip(axes, controls, strict=True):
        subset = effects.set_index(["control", "family"])
        for y, family in enumerate(order):
            row = subset.loc[(control, family)]
            axis.errorbar(
                row["mean"],
                y,
                xerr=[[row["mean"] - row["ci95"][0]], [row["ci95"][1] - row["mean"]]],
                fmt="o",
                color=palette[control],
                capsize=3,
                linewidth=1.4,
            )
        axis.axvline(0, color="#555555", linestyle="--", linewidth=0.8)
        axis.set_title(f"AdaTransfer minus {control.replace('_', ' ')}", fontsize=9)
        axis.set_xlabel("Completion-rate difference", fontsize=8)
        axis.grid(axis="x", color="#dddddd", linewidth=0.5)
        axis.tick_params(labelsize=8)
    axes[0].set_yticks(range(len(labels)), labels)
    axes[0].invert_yaxis()
    fig.tight_layout()
    figure = ROOT / "source/figs/three_family_replication.pdf"
    fig.savefig(figure, bbox_inches="tight")
    plt.close(fig)

    result = {
        "profile": "fixed three-family, disjoint-overlay-seed replication",
        "rows": int(len(frame)),
        "families": int(frame["family"].nunique()),
        "contexts": int(frame["registered_context"].nunique()),
        "seeds": int(frame["seed"].nunique()),
        "effects": rows,
        "safety": safety,
        "checks": checks,
        "freeze": frozen,
        "allChecksPassed": all(checks.values()) and frozen["passed"],
        "inferenceBoundary": (
            "Intervals resample the 30 synthetic-overlay seeds after averaging contexts "
            "within family. The three observed trace families are fixed and do not "
            "support population-wide mobility-dataset inference."
        ),
    }
    summary = RESULTS / "fixed_analysis.json"
    summary.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    checksums = [RUNS, RESULTS / "fixed_family_effects.csv", summary, figure]
    (RESULTS / "FIXED_ANALYSIS_CHECKSUMS.sha256").write_text(
        "".join(f"{sha256(path)}  {path.relative_to(ROOT).as_posix()}\n" for path in checksums),
        encoding="ascii",
    )
    print(json.dumps(result, indent=2))
    return 0 if result["allChecksPassed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
