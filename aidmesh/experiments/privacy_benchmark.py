"""Privacy analysis over simulator event logs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from aidmesh.analysis.privacy import bucketed_observation, privacy_report
from aidmesh.common.canonical import stable_hash

HOUR_US = 3_600_000_000


@dataclass(frozen=True)
class PrivacyDefenseProfile:
    name: str
    bucket_us: int
    hide_risk_cell: bool = False
    hide_category: bool = False
    hide_path: bool = False
    hide_fallback_tag: bool = False
    category_bucket_size: int | None = None


def _category_value(category: str) -> int | None:
    try:
        return int(category.rsplit("-", 1)[1])
    except (IndexError, ValueError):
        return None


def _coarsen_category(category: str, bucket_size: int | None) -> str:
    if bucket_size is None:
        return category
    value = _category_value(category)
    if value is None:
        return category
    lower = (value // bucket_size) * bucket_size
    upper = lower + bucket_size - 1
    return f"category-{lower}-{upper}"


def _observations_for_event_file(
    event_file: Path, profile: PrivacyDefenseProfile
) -> tuple[dict, list]:
    observations = []
    request_meta: dict[str, dict] = {}
    run_meta: dict = {}
    with event_file.open("r", encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            run_meta = {
                "run_id": record["run_id"],
                "scenario_id": record["scenario_id"],
                "method": record["method"],
                "seed": record["seed"],
            }
            nullifier = record.get("nullifier_hash")
            if record.get("event_type") == "redemption_requested" and nullifier:
                request_meta[str(nullifier)] = {
                    "risk_cell": str(record.get("risk_cell") or "hidden"),
                    "category": str(record.get("category") or "hidden"),
                }
                continue
            if record.get("event_type") != "inventory_released" or not nullifier:
                continue
            meta = request_meta.get(str(nullifier), {})
            path = str(record.get("path") or "unknown")
            public_path = "redeem" if profile.hide_path else path
            risk_cell = str(meta.get("risk_cell", "hidden"))
            category = str(meta.get("category", "hidden"))
            if profile.hide_risk_cell:
                risk_cell = "cell-*"
            if profile.hide_category:
                category = "category-*"
            else:
                category = _coarsen_category(category, profile.category_bucket_size)
            fallback_tag = None
            if path == "fallback" and not profile.hide_fallback_tag:
                window = int(record["sim_time_us"]) // profile.bucket_us
                fallback_tag = stable_hash(
                    {
                        "nullifier": str(nullifier),
                        "window": window,
                    },
                    domain="aidmesh/privacy/modeled-fallback-tag/v1",
                )
            observations.append(
                bucketed_observation(
                    created_us=int(record["sim_time_us"]),
                    epoch=1,
                    risk_cell=risk_cell,
                    category=category,
                    path=public_path,
                    bucket_us=profile.bucket_us,
                    fallback_tag=fallback_tag,
                )
            )
    return run_meta, observations


def run_privacy_analysis(
    input_root: Path,
    *,
    bucket_us: int = 3_600_000_000,
) -> pd.DataFrame:
    rows: list[dict] = []
    profile = PrivacyDefenseProfile("baseline", bucket_us)
    for event_file in sorted(input_root.rglob("events.jsonl")):
        run_meta, observations = _observations_for_event_file(event_file, profile)
        if observations:
            report = privacy_report(observations)
            rows.append(
                {
                    **run_meta,
                    **report.to_dict(),
                }
            )
    return pd.DataFrame(rows)


def write_privacy_results(frame: pd.DataFrame, output: Path) -> dict[str, Path]:
    output.mkdir(parents=True, exist_ok=True)
    raw_path = output / "privacy_runs.csv"
    summary_path = output / "privacy_summary.csv"
    frame.to_csv(raw_path, index=False)
    if not frame.empty:
        summary = (
            frame.groupby("method", sort=True)
            .agg(
                seeds=("seed", "nunique"),
                min_anonymity_set_mean=("min_anonymity_set", "mean"),
                unique_bucket_fraction_mean=("unique_bucket_fraction", "mean"),
                shannon_entropy_bits_mean=("shannon_entropy_bits", "mean"),
                posterior_entropy_bits_mean=("posterior_entropy_bits", "mean"),
                attacker_top1_success_mean=("attacker_top1_success", "mean"),
                attacker_advantage_over_global_mean=(
                    "attacker_advantage_over_global",
                    "mean",
                ),
                repeated_fallback_tag_fraction_mean=("repeated_fallback_tag_fraction", "mean"),
            )
            .reset_index()
        )
    else:
        summary = pd.DataFrame()
    summary.to_csv(summary_path, index=False)
    return {"raw": raw_path, "summary": summary_path}


def run_privacy_sweep(input_root: Path, *, bucket_hours: tuple[int, ...]) -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    for hours in bucket_hours:
        if hours <= 0:
            raise ValueError("bucket hours must be positive")
        frame = run_privacy_analysis(input_root, bucket_us=hours * 3_600_000_000)
        if not frame.empty:
            frame.insert(0, "bucket_hours", hours)
        rows.append(frame)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def write_privacy_sweep(frame: pd.DataFrame, output: Path) -> dict[str, Path]:
    output.mkdir(parents=True, exist_ok=True)
    raw_path = output / "privacy_sweep_runs.csv"
    summary_path = output / "privacy_sweep_summary.csv"
    frame.to_csv(raw_path, index=False)
    if not frame.empty:
        summary = (
            frame.groupby(["bucket_hours", "method"], sort=True)
            .agg(
                seeds=("seed", "nunique"),
                min_anonymity_set_mean=("min_anonymity_set", "mean"),
                unique_bucket_fraction_mean=("unique_bucket_fraction", "mean"),
                shannon_entropy_bits_mean=("shannon_entropy_bits", "mean"),
                posterior_entropy_bits_mean=("posterior_entropy_bits", "mean"),
                attacker_top1_success_mean=("attacker_top1_success", "mean"),
                attacker_advantage_over_global_mean=(
                    "attacker_advantage_over_global",
                    "mean",
                ),
                repeated_fallback_tag_fraction_mean=("repeated_fallback_tag_fraction", "mean"),
            )
            .reset_index()
        )
    else:
        summary = pd.DataFrame()
    summary.to_csv(summary_path, index=False)
    return {"raw": raw_path, "summary": summary_path}


def default_privacy_defense_profiles() -> tuple[PrivacyDefenseProfile, ...]:
    return (
        PrivacyDefenseProfile("baseline_1h", 1 * HOUR_US),
        PrivacyDefenseProfile("bucket_4h", 4 * HOUR_US),
        PrivacyDefenseProfile("category_bucket2_4h", 4 * HOUR_US, category_bucket_size=2),
        PrivacyDefenseProfile("hide_category_4h", 4 * HOUR_US, hide_category=True),
        PrivacyDefenseProfile(
            "hide_cell_category_4h",
            4 * HOUR_US,
            hide_risk_cell=True,
            hide_category=True,
        ),
        PrivacyDefenseProfile(
            "batched_public_8h",
            8 * HOUR_US,
            hide_risk_cell=True,
            hide_category=True,
            hide_path=True,
            hide_fallback_tag=True,
        ),
    )


def run_privacy_defense_sweep(
    input_root: Path,
    *,
    profiles: tuple[PrivacyDefenseProfile, ...] | None = None,
) -> pd.DataFrame:
    selected = profiles or default_privacy_defense_profiles()
    rows: list[dict] = []
    for profile in selected:
        if profile.bucket_us <= 0:
            raise ValueError("bucket_us must be positive")
        for event_file in sorted(input_root.rglob("events.jsonl")):
            run_meta, observations = _observations_for_event_file(event_file, profile)
            if not observations:
                continue
            report = privacy_report(observations)
            rows.append(
                {
                    "defense_profile": profile.name,
                    "bucket_hours": profile.bucket_us / HOUR_US,
                    "hide_risk_cell": profile.hide_risk_cell,
                    "hide_category": profile.hide_category,
                    "hide_path": profile.hide_path,
                    "hide_fallback_tag": profile.hide_fallback_tag,
                    "category_bucket_size": profile.category_bucket_size,
                    **run_meta,
                    **report.to_dict(),
                }
            )
    return pd.DataFrame(rows)


def write_privacy_defense_sweep(frame: pd.DataFrame, output: Path) -> dict[str, Path]:
    output.mkdir(parents=True, exist_ok=True)
    raw_path = output / "privacy_defense_runs.csv"
    summary_path = output / "privacy_defense_summary.csv"
    frame.to_csv(raw_path, index=False)
    if not frame.empty:
        summary = (
            frame.groupby(["defense_profile", "method"], sort=True)
            .agg(
                seeds=("seed", "nunique"),
                bucket_hours=("bucket_hours", "first"),
                min_anonymity_set_mean=("min_anonymity_set", "mean"),
                unique_bucket_fraction_mean=("unique_bucket_fraction", "mean"),
                shannon_entropy_bits_mean=("shannon_entropy_bits", "mean"),
                posterior_entropy_bits_mean=("posterior_entropy_bits", "mean"),
                attacker_top1_success_mean=("attacker_top1_success", "mean"),
                attacker_advantage_over_global_mean=(
                    "attacker_advantage_over_global",
                    "mean",
                ),
                repeated_fallback_tag_fraction_mean=("repeated_fallback_tag_fraction", "mean"),
            )
            .reset_index()
        )
    else:
        summary = pd.DataFrame()
    summary.to_csv(summary_path, index=False)
    return {"raw": raw_path, "summary": summary_path}
