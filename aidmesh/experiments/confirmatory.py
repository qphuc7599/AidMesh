"""Frozen, paired confirmatory execution and analysis.

This module deliberately refuses to treat ordinary development directories as
confirmatory evidence.  A registry binds scenario inputs, methods, seeds,
claim decision rules and analysis settings before any raw result is emitted.
"""

from __future__ import annotations

import copy
import json
import platform
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import jsonschema
import numpy as np
import pandas as pd
import yaml
from scipy.stats import bootstrap
from statsmodels.stats.multitest import multipletests

from aidmesh.analysis.pipeline import load_run_summaries
from aidmesh.config import canonical_json, load_scenario
from aidmesh.manifest import build_manifest, host_report, sha256_file
from aidmesh.simulator import METHODS, run_experiment

REGISTRY_SCHEMA_PATH = (
    Path(__file__).resolve().parents[2] / "schemas" / "confirmatory_registry.schema.json"
)
PENDING = "PENDING_MANUAL_RESEARCH_DECISION"


class RegistryError(ValueError):
    """Raised when a proposed confirmatory registry is incomplete or inconsistent."""


@dataclass(frozen=True)
class ConfirmatoryScenario:
    config_path: Path
    seed_start: int
    seed_count: int
    methods: tuple[str, ...]

    @property
    def seeds(self) -> tuple[int, ...]:
        return tuple(range(self.seed_start, self.seed_start + self.seed_count))


@dataclass(frozen=True)
class ConfirmatoryRegistry:
    path: Path
    root: Path
    raw: dict[str, Any]
    scenarios: tuple[ConfirmatoryScenario, ...]

    @property
    def registry_id(self) -> str:
        return str(self.raw["registry_id"])

    @property
    def is_frozen(self) -> bool:
        return self.raw["freeze_status"] == "FROZEN"

    @property
    def all_seeds(self) -> tuple[int, ...]:
        return tuple(seed for scenario in self.scenarios for seed in scenario.seeds)

    @property
    def registry_hash(self) -> str:
        return sha256_file(self.path)


def _validate_registry_shape(value: dict[str, Any]) -> None:
    schema = json.loads(REGISTRY_SCHEMA_PATH.read_text(encoding="utf-8"))
    try:
        jsonschema.Draft202012Validator(schema).validate(value)
    except jsonschema.ValidationError as exc:
        location = ".".join(str(part) for part in exc.absolute_path) or "<root>"
        raise RegistryError(f"registry schema error at {location}: {exc.message}") from exc


def _inside_root(root: Path, candidate: Path) -> Path:
    resolved_root = root.resolve()
    resolved = candidate.resolve()
    if resolved_root != resolved and resolved_root not in resolved.parents:
        raise RegistryError(f"registry path escapes workspace: {candidate}")
    return resolved


def load_confirmatory_registry(path: Path, *, root: Path) -> ConfirmatoryRegistry:
    """Load and cross-validate one machine-readable confirmatory registry."""

    root = root.resolve()
    path = _inside_root(root, path)
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RegistryError("registry root must be a mapping")
    _validate_registry_shape(value)

    scenarios: list[ConfirmatoryScenario] = []
    seen_seeds: set[tuple[str, int]] = set()
    for item in value["scenarios"]:
        config_path = _inside_root(root, root / item["config"])
        if not config_path.exists():
            raise RegistryError(f"scenario config does not exist: {item['config']}")
        config = load_scenario(config_path)
        if config["phase"] != "confirmatory":
            raise RegistryError(f"scenario must have phase=confirmatory: {item['config']}")
        methods = tuple(str(method) for method in item["methods"])
        unknown_methods = set(methods) - set(METHODS)
        if unknown_methods:
            raise RegistryError(f"unknown simulation methods: {sorted(unknown_methods)}")
        scenario = ConfirmatoryScenario(
            config_path=config_path,
            seed_start=int(item["seed_start"]),
            seed_count=int(item["seed_count"]),
            methods=methods,
        )
        for seed in scenario.seeds:
            identity = (config["scenario_id"], seed)
            if identity in seen_seeds:
                raise RegistryError(f"duplicate scenario/seed pair: {identity}")
            seen_seeds.add(identity)
        scenarios.append(scenario)

    available = {method for scenario in scenarios for method in scenario.methods}
    scheduled_ids = {
        load_scenario(scenario.config_path)["scenario_id"] for scenario in scenarios
    }
    comparison_ids: set[str] = set()
    for comparison in value["analysis"]["comparisons"]:
        comparison_id = str(comparison["id"])
        if comparison_id in comparison_ids:
            raise RegistryError(f"duplicate comparison id: {comparison_id}")
        comparison_ids.add(comparison_id)
        required = {str(comparison["treatment"]), str(comparison["control"])}
        if not required <= available:
            raise RegistryError(f"comparison {comparison_id} names a method not scheduled")
        if comparison["treatment"] == comparison["control"]:
            raise RegistryError(f"comparison {comparison_id} has identical treatment/control")
        registered_scenarios = comparison.get("scenarios")
        if registered_scenarios is not None:
            unknown = set(map(str, registered_scenarios)) - scheduled_ids
            if unknown:
                raise RegistryError(
                    f"comparison {comparison_id} names unscheduled scenarios: {sorted(unknown)}"
                )

    return ConfirmatoryRegistry(path=path, root=root, raw=value, scenarios=tuple(scenarios))


def require_frozen(registry: ConfirmatoryRegistry) -> None:
    """Block final evidence generation until the researcher signs the registry."""

    if not registry.is_frozen:
        raise RegistryError("registry is DRAFT; use --allow-draft only for smoke validation")
    approval = registry.raw["approval"]
    if any(str(value).strip() == PENDING for value in approval.values()):
        raise RegistryError("registry has an unresolved manual research decision")


def _tool_version(command: list[str], *, accept_nonzero_banner: bool = False) -> str | None:
    try:
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
    except OSError:
        return None
    text = (completed.stdout or completed.stderr).strip().splitlines()
    if not text:
        return None
    if completed.returncode == 0 or accept_nonzero_banner:
        return text[0]
    return None


def _crypto_artifact_hashes(root: Path) -> dict[str, str | None]:
    paths = {
        "circuit": root / "zk" / "circuits" / "aidmesh_voucher.circom",
        "r1cs": root / "zk" / "build" / "aidmesh_voucher.r1cs",
        "zkey": root / "zk" / "build" / "aidmesh_dev_final.zkey",
        "verification_key": root / "zk" / "build" / "verification_key.json",
        "solidity_verifier": root / "contracts" / "src" / "AidMeshGroth16Verifier.sol",
    }
    return {name: sha256_file(path) if path.exists() else None for name, path in paths.items()}


def write_freeze_manifest(registry: ConfirmatoryRegistry, output: Path) -> Path:
    """Write a hash-addressed snapshot before a confirmatory or smoke run."""

    output.mkdir(parents=True, exist_ok=True)
    scenario_files = [scenario.config_path for scenario in registry.scenarios]
    base = build_manifest(
        registry.root,
        phase="confirmatory",
        crypto_profile="P1_EXACT_PRIVATE",
        crypto_gate_passed=False,
        scenario_files=scenario_files,
        seeds=registry.all_seeds,
        primary_estimands=list(registry.raw["claims"]["primary_questions"]),
        decision_rules=list(registry.raw["claims"]["decision_rules"]),
    )
    payload = {
        **base,
        "confirmatory_registry": {
            "path": registry.path.relative_to(registry.root).as_posix(),
            "sha256": registry.registry_hash,
            "freeze_status": registry.raw["freeze_status"],
            "approval": registry.raw["approval"],
        },
        "toolchain": {
            "python": sys.version,
            "platform": platform.platform(),
            "node": _tool_version(["node", "--version"]),
            "circom": _tool_version(["circom", "--version"]),
            # snarkjs 0.7.x prints its version banner but exits 1 for --version.
            "snarkjs": _tool_version(
                ["npx.cmd", "snarkjs", "--version"], accept_nonzero_banner=True
            ),
            "solc": _tool_version(["solc", "--version"]),
        },
        "crypto_artifact_sha256": _crypto_artifact_hashes(registry.root),
        "crypto_gate_scope": (
            "false because the registered frontier simulator does not execute a Groth16 proof "
            "per redemption; P1 parity is evaluated by its separate benchmark"
        ),
        "host": host_report(),
        "written_at_utc": datetime.now(UTC).isoformat(),
    }
    path = output / "confirmatory_freeze_manifest.json"
    path.write_bytes(canonical_json(payload) + b"\n")
    return path


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json(value) + b"\n")


def _configured_run(config_path: Path, seed: int) -> dict[str, Any]:
    config = copy.deepcopy(load_scenario(config_path))
    config["seed"] = seed
    return config


def _execute_registered_run(
    task: tuple[str, int, str, str, str, str],
) -> str:
    """Execute one registered (scenario, seed, method) cell in any process.

    The cell is fully determined by its scenario file and seed; parallel
    scheduling therefore cannot change any result, only wall-clock order.
    """

    config_path, seed, method, run_dir_text, software_commit, registry_hash = task
    config = _configured_run(Path(config_path), seed)
    run_dir = Path(run_dir_text)
    result = run_experiment(
        config,
        method,
        raw_path=run_dir / "events.jsonl",
        software_commit=software_commit,
    )
    _write_json(
        run_dir / "summary.json",
        {
            **result.to_dict(),
            "phase": config["phase"],
            "registry_sha256": registry_hash,
        },
    )
    return run_dir_text


def run_confirmatory_registry(
    registry: ConfirmatoryRegistry,
    *,
    output: Path,
    software_commit: str,
    allow_draft: bool = False,
    resume: bool = True,
    workers: int = 1,
) -> dict[str, int]:
    """Run exactly the paired scenarios authorized by ``registry``.

    ``workers`` schedules independent registered cells across processes; each
    cell remains a deterministic function of its frozen scenario and seed.
    """

    if not allow_draft:
        require_frozen(registry)
    if "pilot" in {part.lower() for part in output.parts}:
        raise RegistryError("confirmatory output path must not contain a pilot directory")
    if workers < 1:
        raise RegistryError("workers must be at least 1")
    manifest_path = write_freeze_manifest(registry, output)
    tasks: list[tuple[str, int, str, str, str, str]] = []
    skipped = 0
    for scheduled in registry.scenarios:
        scenario_id = load_scenario(scheduled.config_path)["scenario_id"]
        for seed in scheduled.seeds:
            for method in scheduled.methods:
                run_dir = (
                    output / registry.registry_id / scenario_id / method / f"seed-{seed:04d}"
                )
                if resume and (run_dir / "summary.json").exists() and (
                    run_dir / "events.jsonl"
                ).exists():
                    skipped += 1
                    continue
                tasks.append(
                    (
                        str(scheduled.config_path),
                        seed,
                        method,
                        str(run_dir),
                        software_commit,
                        registry.registry_hash,
                    )
                )
    completed = 0
    if workers == 1 or len(tasks) <= 1:
        for task in tasks:
            _execute_registered_run(task)
            completed += 1
    else:
        from concurrent.futures import ProcessPoolExecutor

        with ProcessPoolExecutor(max_workers=workers) as pool:
            for _ in pool.map(_execute_registered_run, tasks, chunksize=4):
                completed += 1
    checksums: list[str] = []
    for path in sorted(output.rglob("events.jsonl")) + sorted(output.rglob("summary.json")):
        checksums.append(f"{sha256_file(path)}  {path.relative_to(output).as_posix()}")
    (output / "CHECKSUMS.txt").write_text("\n".join(checksums) + "\n", encoding="utf-8")
    return {
        "completed": completed,
        "skipped": skipped,
        "manifest": int(manifest_path.exists()),
    }


def _paired_values(
    frame: pd.DataFrame,
    *,
    treatment: str,
    control: str,
    metric: str,
) -> np.ndarray:
    subset = frame[frame["method"].isin([treatment, control])]
    pivot = subset.pivot(index=["scenario_id", "seed"], columns="method", values=metric)
    if treatment not in pivot or control not in pivot:
        raise RegistryError(
            f"missing paired observations for {treatment} versus {control} on {metric}"
        )
    values = (pivot[treatment] - pivot[control]).dropna().to_numpy(dtype=float)
    if len(values) < 2:
        raise RegistryError("at least two complete paired observations are required")
    return values


def _permutation_pvalue(
    values: np.ndarray,
    *,
    alternative: str,
    n_resamples: int,
    rng: np.random.Generator,
) -> float:
    observed = float(np.mean(values))
    signs = rng.choice(np.array([-1.0, 1.0]), size=(n_resamples, len(values)))
    simulated = np.mean(signs * values, axis=1)
    if alternative == "greater":
        tail = simulated >= observed
    elif alternative == "less":
        tail = simulated <= observed
    else:
        tail = np.abs(simulated) >= abs(observed)
    return float((np.count_nonzero(tail) + 1) / (n_resamples + 1))


def analyze_confirmatory_registry(
    registry: ConfirmatoryRegistry,
    *,
    input_root: Path,
    output: Path,
    allow_draft: bool = False,
) -> dict[str, Path]:
    """Compute registered BCa intervals and sign-flip permutation tests only."""

    if not allow_draft:
        require_frozen(registry)
    frame = load_run_summaries(input_root)
    if set(frame["phase"]) != {"confirmatory"}:
        raise RegistryError("analysis input contains non-confirmatory results")
    if frame.duplicated(["scenario_id", "method", "seed"]).any():
        raise RegistryError("analysis input has duplicate scenario/method/seed rows")

    analysis = registry.raw["analysis"]
    rng = np.random.default_rng(int(registry.registry_hash[:16], 16))
    rows: list[dict[str, Any]] = []
    p_values: list[float] = []
    for comparison in analysis["comparisons"]:
        comparison_frame = frame
        registered_scenarios = comparison.get("scenarios")
        if registered_scenarios is not None:
            wanted = set(map(str, registered_scenarios))
            comparison_frame = frame[frame["scenario_id"].isin(wanted)]
            if comparison_frame.empty:
                raise RegistryError(
                    f"comparison {comparison['id']} has no rows for scenarios {sorted(wanted)}"
                )
        for metric in comparison["metrics"]:
            values = _paired_values(
                comparison_frame,
                treatment=str(comparison["treatment"]),
                control=str(comparison["control"]),
                metric=str(metric),
            )
            interval = bootstrap(
                (values,),
                np.mean,
                confidence_level=float(analysis["confidence_level"]),
                n_resamples=int(analysis["bootstrap_resamples"]),
                method="BCa",
                rng=rng,
            ).confidence_interval
            p_value = _permutation_pvalue(
                values,
                alternative=str(comparison["alternative"]),
                n_resamples=int(analysis["permutation_resamples"]),
                rng=rng,
            )
            p_values.append(p_value)
            rows.append(
                {
                    "comparison_id": comparison["id"],
                    "treatment": comparison["treatment"],
                    "control": comparison["control"],
                    "metric": metric,
                    "scenarios": (
                        ";".join(sorted(map(str, registered_scenarios)))
                        if registered_scenarios is not None
                        else "ALL"
                    ),
                    "pairs": len(values),
                    "mean_difference": float(np.mean(values)),
                    "ci_low": float(interval.low),
                    "ci_high": float(interval.high),
                    "permutation_p_value": p_value,
                    "alternative": comparison["alternative"],
                }
            )
    adjusted = multipletests(p_values, method=str(analysis["multiplicity"]))[1]
    for row, value in zip(rows, adjusted, strict=True):
        row["holm_adjusted_p_value"] = float(value)

    output.mkdir(parents=True, exist_ok=True)
    raw_path = output / "confirmatory_run_summaries.csv"
    contrast_path = output / "confirmatory_registered_contrasts.csv"
    frame.to_csv(raw_path, index=False)
    pd.DataFrame(rows).to_csv(contrast_path, index=False)
    analysis_manifest = {
        "registry_sha256": registry.registry_hash,
        "input_root": str(input_root),
        "input_summary_count": int(len(frame)),
        "analysis": analysis,
    }
    manifest_path = output / "confirmatory_analysis_manifest.json"
    _write_json(manifest_path, analysis_manifest)
    return {"summaries": raw_path, "contrasts": contrast_path, "manifest": manifest_path}
