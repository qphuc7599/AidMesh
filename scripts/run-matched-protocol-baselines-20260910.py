"""Run the prospective, method-matched post-review protocol baseline matrix."""

from __future__ import annotations

import hashlib
import json
import platform
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aidmesh.config import load_scenario  # noqa: E402
from aidmesh.simulator.experiment import run_experiment  # noqa: E402


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def paired_ci(values: np.ndarray, *, resamples: int, rng: np.random.Generator) -> dict:
    values = np.asarray(values, dtype=float)
    draws = rng.integers(0, len(values), size=(resamples, len(values)))
    sampled = values[draws].mean(axis=1)
    return {
        "mean": float(values.mean()),
        "median": float(np.median(values)),
        "ci95": [float(np.quantile(sampled, 0.025)), float(np.quantile(sampled, 0.975))],
        "wins": int((values > 0).sum()),
        "ties": int((values == 0).sum()),
        "losses": int((values < 0).sum()),
        "pairedSeeds": int(len(values)),
    }


registry_path = ROOT / "configs/postreview_20260910/matched_protocol_baselines.json"
output = ROOT / "results/matched_protocol_baselines_20260910"
registry = json.loads(registry_path.read_text(encoding="utf-8"))
output.mkdir(parents=True, exist_ok=True)

rows = []
for scenario_path_text in registry["scenarios"]:
    scenario_path = ROOT / scenario_path_text
    base = load_scenario(scenario_path)
    for seed in range(registry["seedStart"], registry["seedStart"] + registry["seedCount"]):
        config = {**base, "seed": seed}
        for method in registry["methods"]:
            result = run_experiment(
                config,
                method,
                experiment_id="POSTREVIEW-MATCHED-PROTOCOL",
                settlement_mode="temporal",
            )
            row = result.to_dict()
            row["scenario_config"] = scenario_path_text
            row["partition_hours"] = base["network"]["partition_us"] / 3_600_000_000
            row["witness_availability"] = base["quorum"]["witness_availability"]
            row["beneficiary_attack_fraction"] = base["adversary"]["beneficiary_fraction"]
            rows.append(row)

frame = pd.DataFrame(rows)
frame.to_csv(output / "runs.csv", index=False)
means = frame.groupby(["scenario_id", "scenario_config", "method"], as_index=False).agg(
    completion_mean=("completion_rate", "mean"),
    settled_mean=("settled_receipts", "mean"),
    rmst_mean_hours=("rmst_hours", "mean"),
    duplicate_exposure_mean=("duplicate_exposure", "mean"),
    bytes_mean=("transmitted_bytes", "mean"),
)
means.to_csv(output / "means.csv", index=False)

rng = np.random.default_rng(20260910)
contrasts = []
for scenario_id, group in frame.groupby("scenario_id"):
    wide = group.pivot(index="seed", columns="method")
    treatment = registry["primaryContrast"]["treatment"]
    for control in ("coconut_online", "first_seen_dtn", "aidmesh_quorum_only"):
        for endpoint in ("completion_rate", "settled_receipts", "rmst_hours", "duplicate_exposure"):
            values = (wide[endpoint][treatment] - wide[endpoint][control]).to_numpy()
            result = paired_ci(values, resamples=registry["bootstrapResamples"], rng=rng)
            result.update(
                {
                    "scenarioId": scenario_id,
                    "treatment": treatment,
                    "control": control,
                    "endpoint": endpoint,
                }
            )
            contrasts.append(result)

safety = {
    "riskConservationFailures": int((~frame["risk_rights_conserved"]).sum()),
    "safeQuorumConflicts": int(frame["conflicting_quorum_certificates"].sum()),
    "aidmeshExposureCapViolations": int(
        (
            frame["method"].str.startswith("aidmesh_")
            & (frame["duplicate_exposure"] > frame["risk_cap"])
        ).sum()
    ),
}
primary = [
    row
    for row in contrasts
    if row["control"] == "coconut_online" and row["endpoint"] == "completion_rate"
]
margin = float(registry["primaryContrast"]["meaningfulMargin"])
summary = {
    "profile": "prospective matched protocol-model baseline matrix",
    "runs": int(len(frame)),
    "scenarios": int(frame["scenario_id"].nunique()),
    "pairedSeedsPerScenario": int(registry["seedCount"]),
    "methods": registry["methods"],
    "primaryContrasts": primary,
    "allScenarioPrimaryMarginPassed": all(row["ci95"][0] > margin for row in primary),
    "safety": safety,
    "allSafetyRulesPassed": all(value == 0 for value in safety.values()),
    "interpretation": (
        "End-to-end network state-transition model calibrated by separately executed "
        "cryptographic components; not a field deployment or per-request cryptographic replay."
    ),
}
(output / "paired_contrasts.json").write_text(json.dumps(contrasts, indent=2), encoding="utf-8")
(output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
manifest = {
    "registry": str(registry_path.relative_to(ROOT)).replace("\\", "/"),
    "registrySha256": sha256(registry_path),
    "simulatorSha256": sha256(ROOT / "aidmesh/simulator/experiment.py"),
    "coconutComponentSummary": "results/coconut_online_final_20260906/summary.json",
    "coconutComponentSummarySha256": sha256(
        ROOT / "results/coconut_online_final_20260906/summary.json"
    ),
    "python": sys.version,
    "platform": platform.platform(),
    "outputs": {
        name: sha256(output / name)
        for name in ("runs.csv", "means.csv", "paired_contrasts.json", "summary.json")
    },
}
(output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
print(json.dumps(summary, indent=2))
