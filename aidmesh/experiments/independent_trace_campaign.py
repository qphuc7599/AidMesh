"""Family-aware external-validity campaign over independent contact traces."""

from __future__ import annotations

import hashlib
import json
import math
import platform
import random
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml
from matplotlib.ticker import NullFormatter

from aidmesh.simulator.dtn import load_contacts_csv

from .temporal_frontier import (
    _frontier_rows,
    run_heldout_temporal_frontier,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class TraceContext:
    family: str
    context: str
    path: Path
    profile: str
    primary: bool


@dataclass(frozen=True)
class IndependentTraceRegistry:
    registry_id: str
    seeds: tuple[int, ...]
    cap_fractions: tuple[float, ...]
    calibration_fraction: float
    buffer_bytes: int
    contexts: tuple[TraceContext, ...]


def load_independent_trace_registry(path: Path) -> IndependentTraceRegistry:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    root = path.resolve().parents[2]
    contexts = tuple(
        TraceContext(
            family=str(item["family"]),
            context=str(item["context"]),
            path=(root / str(item["path"])).resolve(),
            profile=str(item.get("profile", "primary")),
            primary=bool(item.get("primary", True)),
        )
        for item in raw["contexts"]
    )
    registry = IndependentTraceRegistry(
        registry_id=str(raw["registry_id"]),
        seeds=tuple(int(value) for value in raw["seeds"]),
        cap_fractions=tuple(float(value) for value in raw["cap_fractions"]),
        calibration_fraction=float(raw["calibration_fraction"]),
        buffer_bytes=int(raw["buffer_bytes"]),
        contexts=contexts,
    )
    if not registry.registry_id or len({item.family for item in contexts if item.primary}) < 2:
        raise ValueError("registry requires an ID and at least two primary trace families")
    if not registry.seeds or any(not 0 < value <= 1 for value in registry.cap_fractions):
        raise ValueError("invalid seeds or cap fractions")
    if not 0 < registry.calibration_fraction < 1 or registry.buffer_bytes <= 0:
        raise ValueError("invalid calibration fraction or buffer budget")
    keys = [(item.context, item.profile) for item in contexts]
    if len(keys) != len(set(keys)):
        raise ValueError("context/profile pairs must be unique")
    for item in contexts:
        for name in ("contacts.csv", "provenance.json"):
            if not (item.path / name).is_file():
                raise FileNotFoundError(item.path / name)
    return registry


def _trace_diagnostics(spec: TraceContext) -> dict[str, Any]:
    contacts_path = spec.path / "contacts.csv"
    contacts = load_contacts_csv(contacts_path)
    nodes = sorted({node for row in contacts for node in (row.node_a, row.node_b)})
    pairs = {(min(row.node_a, row.node_b), max(row.node_a, row.node_b)) for row in contacts}
    starts = np.asarray([row.start_us for row in contacts], dtype=np.int64)
    ends = np.asarray([row.end_us for row in contacts], dtype=np.int64)
    by_pair: dict[tuple[str, str], list[int]] = {}
    for row in contacts:
        key = (min(row.node_a, row.node_b), max(row.node_a, row.node_b))
        by_pair.setdefault(key, []).append(row.start_us)
    gaps = [
        (right - left) / 1_000_000
        for values in by_pair.values()
        for left, right in zip(sorted(values), sorted(values)[1:], strict=False)
    ]
    provenance = json.loads((spec.path / "provenance.json").read_text(encoding="utf-8"))
    return {
        "family": spec.family,
        "context": spec.context,
        "profile": spec.profile,
        "primary": spec.primary,
        "nodes": len(nodes),
        "contacts": len(contacts),
        "unique_pairs": len(pairs),
        "duration_days": (int(ends.max()) - int(starts.min())) / 86_400_000_000,
        "median_contact_seconds": float(np.median((ends - starts) / 1_000_000)),
        "median_intercontact_seconds": float(np.median(gaps)) if gaps else math.nan,
        "contacts_sha256": _sha256(contacts_path),
        "provenance_sha256": _sha256(spec.path / "provenance.json"),
        "license": provenance.get("license", "not recorded"),
        "dataset": provenance.get("dataset", "not recorded"),
    }


def run_independent_trace_campaign(
    registry: IndependentTraceRegistry,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    rows: list[pd.DataFrame] = []
    diagnostics = []
    trace_manifests: dict[str, Any] = {}
    for spec in registry.contexts:
        frame, manifest = run_heldout_temporal_frontier(
            spec.path.parent,
            contexts=(spec.path.name,),
            seeds=registry.seeds,
            cap_fractions=registry.cap_fractions,
            calibration_fraction=registry.calibration_fraction,
            buffer_bytes=registry.buffer_bytes,
        )
        frame["context"] = spec.context
        frame["family"] = spec.family
        frame["profile"] = spec.profile
        frame["primary"] = spec.primary
        rows.append(frame)
        diagnostics.append(_trace_diagnostics(spec))
        trace_manifests[f"{spec.context}:{spec.profile}"] = manifest["traces"][spec.path.name]
    combined = pd.concat(rows, ignore_index=True)
    manifest = {
        "schema_version": 1,
        "registry": {
            **asdict(registry),
            "contexts": [
                {**asdict(item), "path": item.path.as_posix()} for item in registry.contexts
            ],
        },
        "design": (
            "family-aware calibration-prefix/held-out-suffix replication; family is the "
            "external-validity unit; context/seed pooling is descriptive"
        ),
        "inference_warning": (
            "two primary families do not support population-level trace-family inference"
        ),
        "trace_semantics": (
            "public proximity/vehicle transfer traces with synthetic protocol roles and demand; "
            "not humanitarian participants or field deployments"
        ),
        "traces": trace_manifests,
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
            "machine": platform.machine(),
            "processor": platform.processor(),
        },
    }
    return combined, pd.DataFrame(diagnostics), manifest


def _bootstrap_mean(values: list[float], *, seed: int) -> tuple[float, float]:
    if not values:
        return math.nan, math.nan
    rng = random.Random(seed)
    sampled = [
        sum(values[rng.randrange(len(values))] for _ in values) / len(values)
        for _ in range(10_000)
    ]
    sampled.sort()
    return sampled[249], sampled[9749]


def analyze_independent_trace_campaign(
    frame: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    primary = frame[frame["primary"]].copy()
    volumes = _frontier_rows(primary).merge(
        primary[["context", "family"]].drop_duplicates(), on="context", how="left"
    )
    pivot = volumes.pivot_table(
        index=["family", "context", "seed"], columns="method", values="hypervolume"
    ).reset_index()
    pivot["transferable_minus_static"] = (
        pivot["transferable_rights"] - pivot["static_rights"]
    )
    context_rows = []
    for (family, context), group in pivot.groupby(["family", "context"], sort=True):
        values = group["transferable_minus_static"].tolist()
        lower, upper = _bootstrap_mean(values, seed=20260718)
        context_rows.append(
            {
                "family": family,
                "context": context,
                "paired_seeds": len(values),
                "mean_effect": float(np.mean(values)),
                "bootstrap_95ci_lower": lower,
                "bootstrap_95ci_upper": upper,
                "positive_seeds": sum(value > 0 for value in values),
                "zero_seeds": sum(value == 0 for value in values),
                "negative_seeds": sum(value < 0 for value in values),
            }
        )
    context_contrasts = pd.DataFrame(context_rows)

    # Each seed first receives equal context weight inside its family. This prevents
    # the three SocioPatterns contexts from outvoting the single DieselNet context.
    family_seed = (
        pivot.groupby(["family", "seed"], as_index=False)["transferable_minus_static"].mean()
    )
    family_rows = []
    for family, group in family_seed.groupby("family", sort=True):
        values = group["transferable_minus_static"].tolist()
        lower, upper = _bootstrap_mean(values, seed=20260719)
        family_rows.append(
            {
                "family": family,
                "paired_seeds": len(values),
                "contexts": int(pivot[pivot["family"] == family]["context"].nunique()),
                "mean_effect": float(np.mean(values)),
                "bootstrap_95ci_lower": lower,
                "bootstrap_95ci_upper": upper,
                "positive_seeds": sum(value > 0 for value in values),
            }
        )
    family_contrasts = pd.DataFrame(family_rows)
    leave_one_out = family_contrasts[["family", "mean_effect"]].rename(
        columns={"family": "held_out_family", "mean_effect": "held_out_effect"}
    )
    leave_one_out["other_family_effect"] = leave_one_out["held_out_family"].map(
        {
            row.family: float(
                family_contrasts[family_contrasts["family"] != row.family]["mean_effect"].mean()
            )
            for row in family_contrasts.itertuples()
        }
    )
    leave_one_out["direction_agrees"] = (
        np.sign(leave_one_out["held_out_effect"])
        == np.sign(leave_one_out["other_family_effect"])
    )

    sensitivity = frame[
        (~frame["primary"]) | (frame["family"] == "umass_dieselnet")
    ].copy()
    if sensitivity.empty:
        sensitivity_contrasts = pd.DataFrame()
    else:
        sensitivity_volumes = _frontier_rows(sensitivity)
        metadata = sensitivity[["context", "family", "profile"]].drop_duplicates()
        sensitivity_volumes = sensitivity_volumes.merge(metadata, on="context", how="left")
        sensitivity_pivot = sensitivity_volumes.pivot_table(
            index=["family", "context", "profile", "seed"],
            columns="method",
            values="hypervolume",
        ).reset_index()
        sensitivity_pivot["effect"] = (
            sensitivity_pivot["transferable_rights"] - sensitivity_pivot["static_rights"]
        )
        sensitivity_contrasts = (
            sensitivity_pivot.groupby(["family", "context", "profile"], as_index=False)
            .agg(
                mean_effect=("effect", "mean"),
                min_effect=("effect", "min"),
                max_effect=("effect", "max"),
            )
        )
    return context_contrasts, family_contrasts, leave_one_out, sensitivity_contrasts


def write_independent_trace_campaign(
    frame: pd.DataFrame,
    diagnostics: pd.DataFrame,
    manifest: dict[str, Any],
    output: Path,
) -> dict[str, Path]:
    output.mkdir(parents=True, exist_ok=True)
    manifest.setdefault(
        "environment",
        {
            "python": sys.version,
            "platform": platform.platform(),
            "machine": platform.machine(),
            "processor": platform.processor(),
        },
    )
    context, family, leave_one_out, sensitivity = analyze_independent_trace_campaign(frame)
    paths = {
        "rows": output / "independent_trace_rows.csv",
        "diagnostics": output / "trace_family_diagnostics.csv",
        "context_contrasts": output / "context_contrasts.csv",
        "family_contrasts": output / "family_contrasts.csv",
        "leave_one_family_out": output / "leave_one_family_out.csv",
        "sensitivity": output / "dieselnet_window_sensitivity.csv",
        "claim_decision": output / "claim_decision.json",
        "figure": output / "independent_trace_family_campaign.pdf",
        "manifest": output / "manifest.json",
    }
    frame.to_csv(paths["rows"], index=False)
    diagnostics.to_csv(paths["diagnostics"], index=False)
    context.to_csv(paths["context_contrasts"], index=False)
    family.to_csv(paths["family_contrasts"], index=False)
    leave_one_out.to_csv(paths["leave_one_family_out"], index=False)
    sensitivity.to_csv(paths["sensitivity"], index=False)
    cap_violations = int(
        frame[frame["method"].isin(["fixed_home", "static_rights", "transferable_rights"])][
            "cap_violation"
        ].sum()
    )
    diesel = family[family["family"] == "umass_dieselnet"]
    decision = {
        "cap_violations": cap_violations,
        "independent_family_effect": float(diesel["mean_effect"].iloc[0]),
        "independent_family_ci_includes_zero": bool(
            diesel["bootstrap_95ci_lower"].iloc[0] <= 0 <= diesel["bootstrap_95ci_upper"].iloc[0]
        ),
        "external_validity_replication": (
            "supported" if float(diesel["mean_effect"].iloc[0]) >= 0 else "failed"
        ),
        "universal_superiority_claim": "not_identified_with_two_families",
        "paper_wording": (
            "Report family-conditional replication and exact negative/boundary results; "
            "do not claim population-wide superiority."
        ),
    }
    paths["claim_decision"].write_text(
        json.dumps(decision, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    fig, axes = plt.subplots(1, 3, figsize=(11.2, 3.5))
    positions = np.arange(len(context))
    axes[0].errorbar(
        positions,
        context["mean_effect"],
        yerr=np.vstack(
            [
                context["mean_effect"] - context["bootstrap_95ci_lower"],
                context["bootstrap_95ci_upper"] - context["mean_effect"],
            ]
        ),
        fmt="o",
        capsize=3,
        color="#1f5a85",
    )
    axes[0].axhline(0, color="black", linewidth=0.8)
    axes[0].set_xticks(positions, context["context"], rotation=25, ha="right")
    axes[0].set_ylabel("Transferable - static hypervolume")
    axes[0].set_title("Held-out context effects")

    family_positions = np.arange(len(family))
    axes[1].errorbar(
        family_positions,
        family["mean_effect"],
        yerr=np.vstack(
            [
                family["mean_effect"] - family["bootstrap_95ci_lower"],
                family["bootstrap_95ci_upper"] - family["mean_effect"],
            ]
        ),
        fmt="s",
        capsize=3,
        color="#a23b32",
    )
    axes[1].axhline(0, color="black", linewidth=0.8)
    axes[1].set_xticks(family_positions, family["family"], rotation=20, ha="right")
    axes[1].set_title("Equal-weight family estimates")

    primary_diag = diagnostics[diagnostics["primary"]]
    axes[2].scatter(
        primary_diag["contacts"],
        primary_diag["duration_days"],
        s=35 + primary_diag["nodes"],
        color=[
            "#a23b32" if value == "umass_dieselnet" else "#1f5a85"
            for value in primary_diag["family"]
        ],
        alpha=0.85,
    )
    annotation_offsets = {
        "dieselnet05": (-58, -2),
        "sfhh": (-34, 5),
        "lh10": (5, 5),
        "invs13": (5, 5),
    }
    for row in primary_diag.itertuples():
        x_offset, y_offset = annotation_offsets.get(row.context, (5, 5))
        axes[2].annotate(
            row.context,
            (row.contacts, row.duration_days),
            xytext=(x_offset, y_offset),
            textcoords="offset points",
            fontsize=8,
        )
    axes[2].set_xscale("log")
    axes[2].set_xticks([10_000, 30_000, 70_000], ["10k", "30k", "70k"])
    axes[2].xaxis.set_minor_formatter(NullFormatter())
    axes[2].set_xlabel("Contact observations (log scale)")
    axes[2].set_ylabel("Trace span (days)")
    axes[2].set_title("Independent trace regimes")
    for axis in axes:
        axis.grid(alpha=0.22)
    fig.tight_layout()
    fig.savefig(paths["figure"], bbox_inches="tight")
    plt.close(fig)

    manifest["decision"] = decision
    manifest["output_sha256"] = {
        name: _sha256(path) for name, path in paths.items() if name != "manifest"
    }
    paths["manifest"].write_text(
        json.dumps(manifest, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )
    checksums = write_independent_trace_checksums(output)
    paths["checksums"] = checksums
    return paths


def write_independent_trace_checksums(output: Path) -> Path:
    checksums = output / "CHECKSUMS.sha256"
    files = sorted(path for path in output.rglob("*") if path.is_file() and path != checksums)
    checksums.write_text(
        "".join(f"{_sha256(path)}  {path.relative_to(output).as_posix()}\n" for path in files),
        encoding="utf-8",
    )
    return checksums


def verify_independent_trace_checksums(output: Path) -> dict[str, Any]:
    checksum_path = output / "CHECKSUMS.sha256"
    checked = mismatches = missing = 0
    for line in checksum_path.read_text(encoding="utf-8").splitlines():
        expected, relative = line.split("  ", maxsplit=1)
        path = output / Path(relative)
        if not path.is_file():
            missing += 1
            continue
        checked += 1
        mismatches += int(_sha256(path) != expected)
    return {
        "status": "valid" if mismatches == 0 and missing == 0 else "invalid",
        "checked": checked,
        "mismatches": mismatches,
        "missing": missing,
        "checksums_sha256": _sha256(checksum_path),
    }
