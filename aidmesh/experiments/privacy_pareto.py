"""Privacy--delay Pareto campaign over all registered core runs.

The original supporting campaign selected the first paths after lexical sorting,
which accidentally sampled one method only.  This implementation makes the sampling
unit explicit, validates method/seed coverage, and computes every attacker profile
from the observations of the same immutable run.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Iterable

import matplotlib.pyplot as plt
import pandas as pd
import yaml

from aidmesh.analysis.privacy import MetadataObservation, PrivacyReport, privacy_report
from aidmesh.experiments.privacy_batch import (
    _gateway_observations,
    _ledger_observations,
    _receipt_intents,
    _site_observations,
)
from aidmesh.privacy.batching import (
    ALLOWED_HIDDEN_FIELDS,
    BatchPolicy,
    batch_receipts,
    generate_audit_key,
)


SLOT_WIDTHS_H = (4, 8, 16)
DEFAULT_POLICY = {
    "pad_bytes": 2048,
    "dummy_min_batch": 32,
    "hide_fields": sorted(ALLOWED_HIDDEN_FIELDS),
    "shuffle_seed": 20260719,
}


@dataclass(frozen=True)
class PrivacyParetoManifest:
    registry_path: str
    slot_widths_h: tuple[int, ...]
    input_root: str
    sampling_mode: str
    sampled_runs: int
    method_counts: dict[str, int]
    selected_input_sha256: str
    workload_profiles: tuple[dict[str, Any], ...]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _selected_input_sha256(paths: Iterable[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(str(path.as_posix()).encode("utf-8"))
        digest.update(b"\0")
        digest.update(_sha256(path).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _event_identity(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        first = json.loads(next(handle))
    return {
        "path": path,
        "run_id": str(first["run_id"]),
        "scenario_id": str(first["scenario_id"]),
        "method": str(first["method"]),
        "seed": int(first["seed"]),
    }


def _select_core_inputs(
    source: Path,
    *,
    scenario_id: str,
    expected_methods: tuple[str, ...],
    expected_seeds_per_method: int,
) -> list[dict[str, Any]]:
    selected = [
        _event_identity(path)
        for path in sorted(source.rglob("events.jsonl"))
        if scenario_id in str(path)
    ]
    selected = [item for item in selected if item["scenario_id"] == scenario_id]
    if not selected:
        raise FileNotFoundError(f"no {scenario_id} events.jsonl under {source}")
    identities = {
        (item["run_id"], item["method"], item["seed"])
        for item in selected
    }
    if len(identities) != len(selected):
        raise ValueError("duplicate core run identity in privacy Pareto inputs")
    counts = Counter(item["method"] for item in selected)
    expected = {method: expected_seeds_per_method for method in expected_methods}
    if dict(sorted(counts.items())) != dict(sorted(expected.items())):
        raise ValueError(
            "privacy Pareto core coverage mismatch: "
            f"observed={dict(sorted(counts.items()))}, expected={dict(sorted(expected.items()))}"
        )
    for method in expected_methods:
        seeds = {item["seed"] for item in selected if item["method"] == method}
        if len(seeds) != expected_seeds_per_method:
            raise ValueError(f"method {method} has {len(seeds)} distinct seeds")
    return selected


def _report_row(
    *,
    identity: dict[str, Any],
    slot_h: int,
    vantage: str,
    attacker: str,
    report: PrivacyReport | None,
    delay_mean_h: float,
    dummy_overhead_fraction: float,
    workload: str,
    real_receipts: int,
    dummy_receipts: int,
    cover_only_batches: int,
    wire_bytes_per_real: float,
    available: bool = True,
) -> dict[str, Any]:
    metrics = report.to_dict() if report is not None else {
        "observations": 0,
        "buckets": 0,
        "min_anonymity_set": math.nan,
        "mean_anonymity_set": math.nan,
        "unique_bucket_fraction": math.nan,
        "shannon_entropy_bits": math.nan,
        "posterior_entropy_bits": math.nan,
        "attacker_top1_success": math.nan,
        "attacker_advantage_over_global": math.nan,
        "fallback_observations": 0,
        "repeated_fallback_tag_fraction": math.nan,
    }
    return {
        "slot_width_h": slot_h,
        "run_id": identity["run_id"],
        "scenario_id": identity["scenario_id"],
        "method": identity["method"],
        "seed": identity["seed"],
        "vantage": vantage,
        "attacker": attacker,
        "available": available,
        **metrics,
        "mean_added_delay_h": delay_mean_h,
        "dummy_overhead_fraction": dummy_overhead_fraction,
        "workload": workload,
        "real_receipts": real_receipts,
        "dummy_receipts": dummy_receipts,
        "cover_only_batches": cover_only_batches,
        "wire_bytes_per_real": wire_bytes_per_real,
    }


def _collusion_observations(
    ledger: list[MetadataObservation],
    gateway: list[MetadataObservation],
) -> list[MetadataObservation]:
    """Return the actual gateway view plus ledger-visible slot/shape information.

    The gateway creates the batch and therefore knows which private intent produced
    each real ledger commitment.  Dummies are excluded because they have no gateway
    intent.  With the current equal slot widths this profile may equal the gateway
    baseline; the equality is a measured result rather than a fabricated constant view.
    """

    ledger_slots = sorted(
        {
            (item.epoch, item.time_bucket, item.size_class)
            for item in ledger
            if not item.is_dummy
        }
    )
    default_size = ledger_slots[0][2] if ledger_slots else "unspecified"
    return [
        MetadataObservation(
            epoch=item.epoch,
            time_bucket=item.time_bucket,
            risk_cell=item.risk_cell,
            category=item.category,
            path=item.path,
            fallback_tag=item.fallback_tag,
            size_class=f"{item.size_class}|ledger:{default_size}",
            vantage="ledger_gateway_collusion",
        )
        for item in gateway
    ]


def _rare_category_observations(
    observations: list[MetadataObservation],
    *,
    maximum_frequency: int,
) -> list[MetadataObservation]:
    counts = Counter(item.category for item in observations)
    rare = {
        category
        for category, count in counts.items()
        if category != "hidden" and count <= maximum_frequency
    }
    return [item for item in observations if item.category in rare]


def _profile_run(
    identity: dict[str, Any],
    *,
    slot_h: int,
    policy: BatchPolicy,
    rare_category_max_frequency: int,
    workload: dict[str, Any],
) -> list[dict[str, Any]]:
    _meta, intents = _receipt_intents(identity["path"])
    if not intents:
        return []
    keep_fraction = float(workload.get("keep_fraction", 1.0))
    if not 0 < keep_fraction <= 1:
        raise ValueError("privacy workload keep_fraction must be in (0,1]")
    retained = [
        intent
        for intent in intents
        if _stable_fraction(
            f"{identity['run_id']}|{intent.receipt_id}|{workload['name']}"
        )
        < keep_fraction
    ]
    if not retained:
        retained = [min(intents, key=lambda item: item.receipt_id)]
    stretch = int(workload.get("time_stretch", 1))
    if stretch < 1:
        raise ValueError("privacy workload time_stretch must be >= 1")
    origin = min(intent.released_us for intent in retained)
    intents = [
        replace(
            intent,
            released_us=origin + (intent.released_us - origin) * stretch,
            deadline_us=origin
            + (intent.released_us - origin) * stretch
            + (intent.deadline_us - intent.released_us),
        )
        for intent in retained
    ]
    result = batch_receipts(intents, policy=policy, audit_key=generate_audit_key())
    delay_mean_h = (
        sum(item.added_delay_us for item in result.delays)
        / len(result.delays)
        / 3_600_000_000
    )
    public_count = sum(item.public_receipts for item in result.manifests)
    dummy_count = sum(item.dummy_receipts for item in result.manifests)
    dummy_fraction = dummy_count / public_count if public_count else 0.0
    cover_only_batches = sum(item.cover_only for item in result.manifests)
    wire_bytes = sum(item.public_receipts * item.padded_size for item in result.manifests)
    wire_bytes_per_real = wire_bytes / len(intents)
    ledger = _ledger_observations(result)
    gateway = _gateway_observations(intents, policy)
    site = _site_observations(intents, policy)
    rows = [
        _report_row(
            identity=identity,
            slot_h=slot_h,
            vantage=name,
            attacker="baseline",
            report=privacy_report(observations),
            delay_mean_h=delay_mean_h,
            dummy_overhead_fraction=dummy_fraction,
            workload=str(workload["name"]),
            real_receipts=len(intents),
            dummy_receipts=dummy_count,
            cover_only_batches=cover_only_batches,
            wire_bytes_per_real=wire_bytes_per_real,
        )
        for name, observations in (
            ("ledger", ledger),
            ("gateway", gateway),
            ("site", site),
        )
    ]
    collusion = _collusion_observations(ledger, gateway)
    rows.append(
        _report_row(
            identity=identity,
            slot_h=slot_h,
            vantage="ledger+gateway",
            attacker="collusion",
            report=privacy_report(collusion),
            delay_mean_h=delay_mean_h,
            dummy_overhead_fraction=dummy_fraction,
            workload=str(workload["name"]),
            real_receipts=len(intents),
            dummy_receipts=dummy_count,
            cover_only_batches=cover_only_batches,
            wire_bytes_per_real=wire_bytes_per_real,
        )
    )
    rare = _rare_category_observations(
        site,
        maximum_frequency=rare_category_max_frequency,
    )
    rows.append(
        _report_row(
            identity=identity,
            slot_h=slot_h,
            vantage="site",
            attacker="rare_category",
            report=privacy_report(rare) if rare else None,
            delay_mean_h=delay_mean_h,
            dummy_overhead_fraction=dummy_fraction,
            workload=str(workload["name"]),
            real_receipts=len(intents),
            dummy_receipts=dummy_count,
            cover_only_batches=cover_only_batches,
            wire_bytes_per_real=wire_bytes_per_real,
            available=bool(rare),
        )
    )
    return rows


def _stable_fraction(label: str) -> float:
    digest = hashlib.sha256(label.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / float(2**64)


def run_privacy_pareto(
    registry: dict[str, Any],
) -> tuple[pd.DataFrame, PrivacyParetoManifest]:
    input_root = Path(registry["input_root"])
    slot_widths = tuple(int(value) for value in registry.get("slot_widths_h", SLOT_WIDTHS_H))
    scenario_id = str(registry.get("scenario_id", "CONF_CORE_W08_P24"))
    expected_methods = tuple(str(value) for value in registry["expected_methods"])
    expected_seeds_per_method = int(registry.get("expected_seeds_per_method", 50))
    rare_max = int(registry.get("rare_category_max_frequency", 2))
    workload_profiles = tuple(
        dict(profile)
        for profile in registry.get(
            "workload_profiles",
            ({"name": "observed", "keep_fraction": 1.0, "time_stretch": 1},),
        )
    )
    selected = _select_core_inputs(
        input_root,
        scenario_id=scenario_id,
        expected_methods=expected_methods,
        expected_seeds_per_method=expected_seeds_per_method,
    )
    rows: list[dict[str, Any]] = []
    for workload in workload_profiles:
        if not workload.get("name"):
            raise ValueError("privacy workload profile requires name")
        for slot_h in slot_widths:
            policy = BatchPolicy(
                slot_us=slot_h * 3_600_000_000,
                pad_bytes=int(workload.get("pad_bytes", DEFAULT_POLICY["pad_bytes"])),
                dummy_min_batch=int(
                    workload.get("dummy_min_batch", DEFAULT_POLICY["dummy_min_batch"])
                ),
                hide_fields=frozenset(DEFAULT_POLICY["hide_fields"]),
                shuffle_seed=int(DEFAULT_POLICY["shuffle_seed"]),
                gateway_bucket_us=slot_h * 3_600_000_000,
                cover_empty_slots=bool(workload.get("cover_empty_slots", False)),
                cover_boundary_slots=int(workload.get("cover_boundary_slots", 0)),
            )
            for identity in selected:
                rows.extend(
                    _profile_run(
                        identity,
                        slot_h=slot_h,
                        policy=policy,
                        rare_category_max_frequency=rare_max,
                        workload=workload,
                    )
                )
    method_counts = dict(sorted(Counter(item["method"] for item in selected).items()))
    manifest = PrivacyParetoManifest(
        registry_path=str(registry.get("_path", "inline")),
        slot_widths_h=slot_widths,
        input_root=str(input_root),
        sampling_mode=str(registry.get("sampling_mode", "all_core_runs")),
        sampled_runs=len(selected),
        method_counts=method_counts,
        selected_input_sha256=_selected_input_sha256(item["path"] for item in selected),
        workload_profiles=workload_profiles,
    )
    return pd.DataFrame(rows), manifest


def summarize_privacy_pareto(frame: pd.DataFrame) -> pd.DataFrame:
    available = frame[frame["available"] & frame["min_anonymity_set"].notna()].copy()
    if available.empty:
        return pd.DataFrame()
    return (
        available.groupby(
            ["workload", "slot_width_h", "method", "vantage", "attacker"],
            as_index=False,
            sort=True,
        )
        .agg(
            runs=("run_id", "nunique"),
            min_anonymity_worst=("min_anonymity_set", "min"),
            min_anonymity_median=("min_anonymity_set", "median"),
            min_anonymity_p25=("min_anonymity_set", lambda values: values.quantile(0.25)),
            min_anonymity_p75=("min_anonymity_set", lambda values: values.quantile(0.75)),
            posterior_entropy_median=("posterior_entropy_bits", "median"),
            top1_success_median=("attacker_top1_success", "median"),
            mean_added_delay_h=("mean_added_delay_h", "mean"),
            dummy_overhead_fraction_mean=("dummy_overhead_fraction", "mean"),
            dummy_receipts_mean=("dummy_receipts", "mean"),
            cover_only_batches_mean=("cover_only_batches", "mean"),
            wire_bytes_per_real_mean=("wire_bytes_per_real", "mean"),
        )
    )


def write_privacy_pareto(
    frame: pd.DataFrame,
    manifest: PrivacyParetoManifest,
    output: Path,
) -> dict[str, Path]:
    output.mkdir(parents=True, exist_ok=True)
    csv_path = output / "privacy_pareto.csv"
    frame.to_csv(csv_path, index=False)
    summary = summarize_privacy_pareto(frame)
    summary_path = output / "privacy_pareto_summary.csv"
    summary.to_csv(summary_path, index=False)
    manifest_path = output / "manifest.json"
    manifest_path.write_text(json.dumps(asdict(manifest), indent=2) + "\n", encoding="utf-8")

    baselines = summary[summary["attacker"] == "baseline"]
    methods = list(manifest.method_counts)
    colors = dict(
        zip(
            methods,
            ("#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd"),
            strict=False,
        )
    )
    plotted_workload = (
        "sparse_cover"
        if "sparse_cover" in set(baselines["workload"])
        else sorted(baselines["workload"].unique())[0]
    )
    baselines = baselines[baselines["workload"] == plotted_workload]
    fig, axes = plt.subplots(1, 3, figsize=(12.5, 4.1), sharex=True)
    for axis, vantage in zip(axes, ("ledger", "gateway", "site"), strict=True):
        subset = baselines[baselines["vantage"] == vantage]
        for method, group in subset.groupby("method", sort=True):
            group = group.sort_values("mean_added_delay_h")
            axis.plot(
                group["mean_added_delay_h"],
                group["min_anonymity_median"],
                marker="o",
                linewidth=1.5,
                color=colors.get(method),
                label=method.replace("aidmesh_", "").replace("_", " "),
            )
            axis.fill_between(
                group["mean_added_delay_h"].astype(float),
                group["min_anonymity_p25"].astype(float),
                group["min_anonymity_p75"].astype(float),
                color=colors.get(method),
                alpha=0.12,
            )
        axis.set_title(f"{vantage.capitalize()} observer")
        axis.set_xlabel("Mean added delay (hours)")
        axis.set_yscale("symlog", linthresh=1)
        axis.grid(alpha=0.2)
    axes[0].set_ylabel("Run-level minimum anonymity set\n(median and IQR across 50 seeds)")
    axes[1].legend(fontsize=7, loc="best")
    fig.suptitle(
        f"Privacy--delay frontier: {plotted_workload} workload, "
        f"{manifest.sampled_runs} paired source runs"
    )
    fig.tight_layout()
    figure_path = output / "privacy_pareto.pdf"
    fig.savefig(figure_path, bbox_inches="tight")
    plt.close(fig)

    checksums = output / "CHECKSUMS.sha256"
    checksum_inputs = [csv_path, summary_path, manifest_path, figure_path]
    checksums.write_text(
        "\n".join(f"{_sha256(path)}  {path.name}" for path in checksum_inputs) + "\n",
        encoding="utf-8",
    )
    return {
        "csv": csv_path,
        "summary": summary_path,
        "figure": figure_path,
        "manifest": manifest_path,
        "checksums": checksums,
    }


def load_privacy_pareto_registry(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"registry must be a mapping: {path}")
    payload["_path"] = str(path)
    return payload
