"""Frozen confirmatory design for conservative, reclaimable transfer (CATR).

This module is deliberately separate from the exploratory BOW campaigns.  It
adds one predeclared recovery factor and evaluates the final policy without
changing the historical V1--V4 definitions.
"""

from __future__ import annotations

import itertools
from pathlib import Path
from typing import Any

import pandas as pd

from aidmesh.experiments.bow_factorial import (
    FACTOR_LEVELS,
    _contacts_for_predictability,
    _requests_for_drift,
    factor_settings,
)
from aidmesh.experiments.bow_temporal import (
    CausalCampaignConfig,
    calibration_path_reliability,
    evaluate_causal_policy,
)
from aidmesh.experiments.temporal_frontier import (
    HOUR_US,
    _calibration_roles,
    generate_heldout_requests,
)
from aidmesh.simulator.dtn import Contact, load_contacts_csv

CATR_FACTOR_LEVELS: dict[str, tuple[str, str]] = {
    **FACTOR_LEVELS,
    "recovery_timing": ("prompt", "delayed"),
}

CATR_PRIMARY_POLICIES = (
    "quorum_only",
    "equal_static",
    "optimized_static",
    "robust_static",
    "periodic_sync",
    "adatransfer",
    "reclaimable_transfer",
    "catr",
)

CATR_FACTORIAL_POLICIES = (
    "optimized_static",
    "robust_static",
    "periodic_sync",
    "adatransfer",
    "reclaimable_transfer",
    "catr",
)


def all_catr_factorial_cells() -> tuple[dict[str, str], ...]:
    names = tuple(CATR_FACTOR_LEVELS)
    return tuple(
        dict(zip(names, values, strict=True))
        for values in itertools.product(*(CATR_FACTOR_LEVELS[name] for name in names))
    )


def catr_factor_settings(levels: dict[str, str]) -> dict[str, Any]:
    if set(levels) != set(CATR_FACTOR_LEVELS):
        raise ValueError("CATR factor cell must specify all eight factors")
    settings = factor_settings({name: levels[name] for name in FACTOR_LEVELS})
    if levels["recovery_timing"] == "prompt":
        settings.update(
            reclaim_delay_us=HOUR_US,
            transfer_decision_ttl_us=4 * HOUR_US,
        )
    elif levels["recovery_timing"] == "delayed":
        settings.update(
            reclaim_delay_us=6 * HOUR_US,
            transfer_decision_ttl_us=12 * HOUR_US,
        )
    else:  # pragma: no cover - protected by the frozen cell generator
        raise ValueError(f"unknown recovery timing: {levels['recovery_timing']}")
    return settings


def run_catr_factorial(
    trace_root: Path,
    *,
    contexts: tuple[str, ...],
    seeds: tuple[int, ...],
    policies: tuple[str, ...] = CATR_FACTORIAL_POLICIES,
    cells: tuple[dict[str, str], ...] | None = None,
    calibration_fraction: float = 0.40,
    conservative_protection_fraction: float = 0.10,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Run the untouched-family 2^8 confirmatory mechanism design."""

    selected_cells = cells or all_catr_factorial_cells()
    rows: list[dict[str, Any]] = []
    trace_manifest: dict[str, Any] = {}
    for context in contexts:
        contacts_path = trace_root / context / "contacts.csv"
        contacts = load_contacts_csv(contacts_path)
        start_us = min(row.start_us for row in contacts)
        end_us = max(row.end_us for row in contacts)
        cutoff_us = start_us + round((end_us - start_us) * calibration_fraction)
        roles = _calibration_roles(contacts, cutoff_us)
        sites = roles["sites"]
        witness_pool = tuple(dict.fromkeys((*roles["relays"], *sites)))
        witnesses = witness_pool[: min(5, len(witness_pool))]
        threshold = min(3, len(witnesses))
        reliability = calibration_path_reliability(contacts, sites=sites, cutoff_us=cutoff_us)
        heldout = tuple(row for row in contacts if row.start_us >= cutoff_us)
        trace_manifest[context] = {
            "contacts_path": str(contacts_path),
            "contacts": len(contacts),
            "heldout_contacts": len(heldout),
            "cutoff_us": cutoff_us,
            "roles": {name: list(value) for name, value in roles.items()},
        }
        for seed in seeds:
            base_requests = generate_heldout_requests(
                contacts,
                roles,
                context=context,
                seed=seed,
                cutoff_us=cutoff_us,
            )
            request_cache: dict[str, tuple[Any, ...]] = {}
            contact_cache: dict[str, tuple[Contact, ...]] = {}
            for cell_index, levels in enumerate(selected_cells):
                settings = catr_factor_settings(levels)
                drift = levels["demand_drift"]
                if drift not in request_cache:
                    request_cache[drift] = _requests_for_drift(
                        base_requests,
                        sites=sites,
                        context=context,
                        seed=seed,
                        level=drift,
                    )
                predictability = levels["contact_predictability"]
                if predictability not in contact_cache:
                    contact_cache[predictability] = _contacts_for_predictability(
                        heldout,
                        context=context,
                        seed=seed,
                        level=predictability,
                    )
                requests = request_cache[drift]
                cap = max(
                    len(sites),
                    round(len(requests) * settings["cap_fraction"]),
                )
                for policy in policies:
                    config = CausalCampaignConfig(
                        cap=cap,
                        policy=policy,
                        gateway=roles["relays"][0],
                        witnesses=witnesses,
                        quorum_threshold=threshold,
                        quorum_availability=settings["quorum_availability"],
                        transfer_witness_availability=settings["quorum_availability"],
                        buffer_bytes=settings["buffer_bytes"],
                        copy_budget=settings["copy_budget"],
                        transfer_loss_probability=settings["transfer_loss_probability"],
                        transfer_processing_delay_us=settings["transfer_processing_delay_us"],
                        reclaim_delay_us=settings["reclaim_delay_us"],
                        transfer_decision_ttl_us=settings["transfer_decision_ttl_us"],
                        background_bundles_per_hour=settings["background_bundles_per_hour"],
                        conservative_protection_fraction=(conservative_protection_fraction),
                        adatransfer_gate_mode="expected_value",
                        experiment_id=(f"catr-confirmatory:{context}:{seed}:{cell_index}"),
                    )
                    result = evaluate_causal_policy(
                        contacts=contact_cache[predictability],
                        requests=requests,
                        sites=sites,
                        config=config,
                        path_reliability=reliability,
                        compute_oracle=False,
                    )
                    rows.append(
                        {
                            "context": context,
                            "seed": seed,
                            "cell": cell_index,
                            **levels,
                            "cap": cap,
                            "gateway": roles["relays"][0],
                            **result,
                        }
                    )
    frame = pd.DataFrame(rows)
    manifest = {
        "schema_version": 1,
        "design": "untouched Asturies-Wildfires 2^8 CATR confirmatory factorial",
        "contexts": list(contexts),
        "seeds": list(seeds),
        "policies": list(policies),
        "factor_levels": {name: list(values) for name, values in CATR_FACTOR_LEVELS.items()},
        "selected_cells": len(selected_cells),
        "full_cells": len(all_catr_factorial_cells()),
        "calibration_fraction": calibration_fraction,
        "conservative_protection_fraction": conservative_protection_fraction,
        "policy_tuning_on_family": False,
        "traces": trace_manifest,
        "rows": len(frame),
    }
    return frame, manifest


def catr_factorial_contrasts(
    frame: pd.DataFrame, *, reference: str = "optimized_static"
) -> pd.DataFrame:
    keys = ["context", "seed", "cell", *CATR_FACTOR_LEVELS]
    values = [
        "completion_rate",
        "end_to_end_settlement_probability",
        "exposure_per_1000_requests",
        "forwarded_bytes",
        "transfers_stranded",
        "transfers_reclaimed",
    ]
    pivot = frame.pivot(index=keys, columns="policy", values=values)
    if reference not in pivot.columns.get_level_values(1):
        raise ValueError(f"reference policy is absent: {reference}")
    rows = []
    reference_frame = pivot.xs(reference, axis=1, level=1).reset_index()
    for policy in sorted(set(pivot.columns.get_level_values(1)) - {reference}):
        candidate = pivot.xs(policy, axis=1, level=1).reset_index()
        merged = candidate.merge(
            reference_frame,
            on=keys,
            suffixes=("", "_reference"),
            validate="one_to_one",
        )
        merged["policy"] = policy
        merged["reference"] = reference
        merged["completion_delta"] = merged["completion_rate"] - merged["completion_rate_reference"]
        merged["stranding_delta"] = (
            merged["transfers_stranded"] - merged["transfers_stranded_reference"]
        )
        rows.append(merged)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
