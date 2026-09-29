"""Mechanism factorial for shared-queue causal authorization policies."""

from __future__ import annotations

import hashlib
import itertools
import json
import math
from dataclasses import replace
from pathlib import Path
from typing import Any

import pandas as pd

from aidmesh.experiments.bow_temporal import (
    CausalCampaignConfig,
    _stable_unit,
    calibration_path_reliability,
    evaluate_causal_policy,
)
from aidmesh.experiments.temporal_frontier import (
    HOUR_US,
    _calibration_roles,
    generate_heldout_requests,
)
from aidmesh.simulator.dtn import Contact, load_contacts_csv

FACTOR_LEVELS: dict[str, tuple[str, str]] = {
    "demand_drift": ("stable", "shifting"),
    "cap_tightness": ("tight", "loose"),
    "witness_availability": ("low", "high"),
    "transfer_reliability": ("degraded", "reliable"),
    "contact_predictability": ("volatile", "predictable"),
    "buffer_provisioning": ("constrained", "provisioned"),
    "cross_class_traffic": ("congested", "quiet"),
}

FACTORIAL_POLICIES = (
    "optimized_static",
    "robust_static",
    "rolling_relocation",
    "myopic_transfer",
    "adatransfer",
    "periodic_sync",
)


def all_factorial_cells() -> tuple[dict[str, str], ...]:
    names = tuple(FACTOR_LEVELS)
    return tuple(
        dict(zip(names, values, strict=True))
        for values in itertools.product(*(FACTOR_LEVELS[name] for name in names))
    )


def factor_settings(levels: dict[str, str]) -> dict[str, Any]:
    if set(levels) != set(FACTOR_LEVELS):
        raise ValueError("factor cell must specify every registered factor")
    return {
        "cap_fraction": 0.15 if levels["cap_tightness"] == "tight" else 0.60,
        "quorum_availability": (
            0.25 if levels["witness_availability"] == "low" else 0.90
        ),
        "transfer_loss_probability": (
            0.30 if levels["transfer_reliability"] == "degraded" else 0.0
        ),
        "transfer_processing_delay_us": (
            2 * HOUR_US if levels["transfer_reliability"] == "degraded" else 0
        ),
        "buffer_bytes": (
            16 * 1024 if levels["buffer_provisioning"] == "constrained" else 96 * 1024
        ),
        "copy_budget": 2 if levels["buffer_provisioning"] == "constrained" else 8,
        "background_bundles_per_hour": (
            24.0 if levels["cross_class_traffic"] == "congested" else 0.0
        ),
    }


def _weighted_site(sites: tuple[str, ...], unit: float, weights: tuple[float, ...]) -> str:
    cumulative = 0.0
    for site, weight in zip(sites, weights, strict=True):
        cumulative += weight
        if unit < cumulative:
            return site
    return sites[-1]


def _requests_for_drift(
    requests: tuple[Any, ...],
    *,
    sites: tuple[str, ...],
    context: str,
    seed: int,
    level: str,
) -> tuple[Any, ...]:
    """Apply a seed-varying causal demand trajectory to a common request set."""

    if level == "stable":
        return requests
    if level != "shifting":
        raise ValueError(f"unknown demand drift level: {level}")
    base = (0.55, 0.25, 0.15, 0.05)[: len(sites)]
    rotation = 1 + int(
        _stable_unit(
            {"context": context, "seed": seed},
            domain="aidmesh/factorial/drift-rotation/v1",
        )
        * max(1, len(sites) - 1)
    )
    target = base[-rotation:] + base[:-rotation]
    start_us = min(row.bundle.created_us for row in requests)
    end_us = max(row.bundle.created_us for row in requests)
    horizon = max(1, end_us - start_us)
    shifted = []
    for row in requests:
        progress = (row.bundle.created_us - start_us) / horizon
        alpha = min(1.0, max(0.0, (progress - 0.20) / 0.60))
        alpha = alpha * alpha * (3 - 2 * alpha)
        weights = tuple(
            (1 - alpha) * left + alpha * right
            for left, right in zip(base, target, strict=True)
        )
        destination = _weighted_site(
            sites,
            _stable_unit(
                {"context": context, "seed": seed, "request": row.bundle.bundle_id},
                domain="aidmesh/factorial/drifting-destination/v1",
            ),
            weights,
        )
        shifted.append(row if destination == row.bundle.destination else replace(
            row, bundle=replace(row.bundle, destination=destination)
        ))
    return tuple(shifted)


def _contacts_for_predictability(
    contacts: tuple[Contact, ...],
    *,
    context: str,
    seed: int,
    level: str,
) -> tuple[Contact, ...]:
    """Preserve contact marginals while perturbing held-out temporal correlation."""

    if level == "predictable" or not contacts:
        return contacts
    if level != "volatile":
        raise ValueError(f"unknown contact predictability level: {level}")
    origin = min(row.start_us for row in contacts)
    horizon = max(row.end_us for row in contacts) - origin
    blocks = min(8, max(2, math.ceil(horizon / (5 * 60 * 1_000_000))))
    block_us = max(1, math.ceil(horizon / blocks))
    permutation = sorted(
        range(blocks),
        key=lambda block: _stable_unit(
            {"context": context, "seed": seed, "block": block},
            domain="aidmesh/factorial/contact-permutation/v1",
        ),
    )
    remapped = []
    for row in contacts:
        old_block = min(blocks - 1, (row.start_us - origin) // block_us)
        offset = (row.start_us - origin) % block_us
        start_us = origin + permutation[old_block] * block_us + offset
        remapped.append(
            Contact(
                start_us,
                start_us + (row.end_us - row.start_us),
                row.node_a,
                row.node_b,
                row.bytes_per_second,
            )
        )
    return tuple(sorted(remapped, key=lambda row: (
        row.start_us, row.end_us, row.node_a, row.node_b
    )))


def run_bow_factorial(
    trace_root: Path,
    *,
    contexts: tuple[str, ...] = ("invs13", "lh10", "sfhh"),
    seeds: tuple[int, ...] = tuple(range(8501, 8505)),
    policies: tuple[str, ...] = FACTORIAL_POLICIES,
    cells: tuple[dict[str, str], ...] | None = None,
    calibration_fraction: float = 0.40,
    adatransfer_gate_mode: str = "confidence",
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Run a complete or caller-supplied subset of the registered 2^7 design."""

    selected_cells = cells or all_factorial_cells()
    rows: list[dict[str, Any]] = []
    trace_manifest: dict[str, Any] = {}
    for context in contexts:
        contacts_path = trace_root / context / "contacts.csv"
        contacts = load_contacts_csv(contacts_path)
        start_us = min(contact.start_us for contact in contacts)
        end_us = max(contact.end_us for contact in contacts)
        cutoff_us = start_us + round((end_us - start_us) * calibration_fraction)
        roles = _calibration_roles(contacts, cutoff_us)
        sites = roles["sites"]
        witness_pool = tuple(dict.fromkeys((*roles["relays"], *sites)))
        witnesses = witness_pool[: min(5, len(witness_pool))]
        threshold = min(3, len(witnesses))
        reliability = calibration_path_reliability(
            contacts, sites=sites, cutoff_us=cutoff_us
        )
        heldout = tuple(contact for contact in contacts if contact.start_us >= cutoff_us)
        trace_manifest[context] = {
            "path": str(contacts_path),
            "contacts": len(contacts),
            "heldout_contacts": len(heldout),
            "cutoff_us": cutoff_us,
            "roles": {key: list(value) for key, value in roles.items()},
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
                settings = factor_settings(levels)
                drift = levels["demand_drift"]
                if drift not in request_cache:
                    request_cache[drift] = _requests_for_drift(
                        base_requests,
                        sites=sites,
                        context=context,
                        seed=seed,
                        level=drift,
                    )
                requests = request_cache[drift]
                predictability = levels["contact_predictability"]
                if predictability not in contact_cache:
                    contact_cache[predictability] = _contacts_for_predictability(
                        heldout,
                        context=context,
                        seed=seed,
                        level=predictability,
                    )
                cell_contacts = contact_cache[predictability]
                cap = max(len(sites), round(len(requests) * settings["cap_fraction"]))
                gateway = roles["relays"][0]
                for policy in policies:
                    config = CausalCampaignConfig(
                        cap=cap,
                        policy=policy,
                        gateway=gateway,
                        witnesses=witnesses,
                        quorum_threshold=threshold,
                        quorum_availability=settings["quorum_availability"],
                        buffer_bytes=settings["buffer_bytes"],
                        copy_budget=settings["copy_budget"],
                        transfer_loss_probability=settings[
                            "transfer_loss_probability"
                        ],
                        transfer_processing_delay_us=settings[
                            "transfer_processing_delay_us"
                        ],
                        background_bundles_per_hour=settings[
                            "background_bundles_per_hour"
                        ],
                        experiment_id=(
                            f"bow-factorial:{context}:{seed}:{cell_index}"
                        ),
                        adatransfer_gate_mode=adatransfer_gate_mode,
                    )
                    result = evaluate_causal_policy(
                        contacts=cell_contacts,
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
                            "gateway": gateway,
                            **result,
                        }
                    )
    frame = pd.DataFrame(rows)
    manifest = {
        "schema_version": 1,
        "design": "complete 2^7 shared-queue causal mechanism factorial",
        "contexts": list(contexts),
        "seeds": list(seeds),
        "policies": list(policies),
        "factor_levels": {key: list(value) for key, value in FACTOR_LEVELS.items()},
        "fixed_gateway_rule": "highest calibration-prefix relay centrality",
        "contact_capacity": "observed trace capacity, not experimentally rescaled",
        "contact_predictability_intervention": (
            "deterministic held-out block permutation preserving contact identities, "
            "durations, rates and within-block offsets"
        ),
        "demand_drift_intervention": (
            "smooth seed-varying rotation away from the preregistered calibration demand"
        ),
        "selected_cells": len(selected_cells),
        "adatransfer_gate_mode": adatransfer_gate_mode,
        "full_cells": len(all_factorial_cells()),
        "trace_semantics": "public proximity traces with deterministic service overlay",
        "traces": trace_manifest,
        "rows": len(frame),
    }
    return frame, manifest


def factorial_policy_contrasts(
    frame: pd.DataFrame, *, reference: str = "optimized_static"
) -> pd.DataFrame:
    keys = ["context", "seed", "cell", *FACTOR_LEVELS]
    values = [
        "completion_rate",
        "end_to_end_settlement_probability",
        "exposure_per_1000_requests",
        "forwarded_bytes",
        "transfers_stranded",
    ]
    pivot = frame.pivot(index=keys, columns="policy", values=values)
    if reference not in pivot.columns.get_level_values(1):
        raise ValueError(f"reference policy is absent: {reference}")
    rows: list[pd.DataFrame] = []
    for policy in sorted(set(pivot.columns.get_level_values(1)) - {reference}):
        block = pivot.xs(policy, axis=1, level=1).reset_index()
        baseline = pivot.xs(reference, axis=1, level=1).reset_index()
        merged = block.merge(baseline, on=keys, suffixes=("", "_reference"))
        merged["policy"] = policy
        merged["reference"] = reference
        merged["completion_delta"] = (
            merged["completion_rate"] - merged["completion_rate_reference"]
        )
        merged["settlement_delta"] = (
            merged["end_to_end_settlement_probability"]
            - merged["end_to_end_settlement_probability_reference"]
        )
        rows.append(merged)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def write_bow_factorial(
    trace_root: Path,
    output: Path,
    *,
    contexts: tuple[str, ...] = ("invs13", "lh10", "sfhh"),
    seeds: tuple[int, ...] = tuple(range(8501, 8505)),
    policies: tuple[str, ...] = FACTORIAL_POLICIES,
    cells: tuple[dict[str, str], ...] | None = None,
) -> dict[str, Path]:
    from aidmesh.analysis.hierarchical_bow import (
        fit_factorial_mechanism_model,
        hierarchical_policy_effects,
    )

    output.mkdir(parents=True, exist_ok=True)
    frame, manifest = run_bow_factorial(
        trace_root,
        contexts=contexts,
        seeds=seeds,
        policies=policies,
        cells=cells,
    )
    contrasts = factorial_policy_contrasts(frame)
    effects = hierarchical_policy_effects(frame, repetitions=2000)
    clusters = frame[["context", "seed"]].drop_duplicates().shape[0]
    complete_levels = all(frame[factor].nunique() == 2 for factor in FACTOR_LEVELS)
    if clusters >= 2 and complete_levels:
        coefficients, diagnostics = fit_factorial_mechanism_model(frame)
        diagnostics["status"] = "fitted"
    else:
        coefficients = pd.DataFrame(
            columns=("term", "estimate", "std_error", "p_value", "ci_low", "ci_high")
        )
        diagnostics = {
            "status": "not_fitted",
            "reason": "requires at least two context-seed clusters and both levels of every factor",
            "context_seed_clusters": clusters,
            "complete_factor_levels": complete_levels,
        }
    paths = {
        "runs": output / "bow_factorial_runs.csv",
        "contrasts": output / "bow_factorial_contrasts.csv",
        "effects": output / "bow_factorial_hierarchical_effects.csv",
        "coefficients": output / "bow_factorial_mechanism_coefficients.csv",
        "diagnostics": output / "bow_factorial_mechanism_diagnostics.json",
        "manifest": output / "manifest.json",
    }
    frame.to_csv(paths["runs"], index=False, lineterminator="\n")
    contrasts.to_csv(paths["contrasts"], index=False, lineterminator="\n")
    effects.to_csv(paths["effects"], index=False, lineterminator="\n")
    coefficients.to_csv(paths["coefficients"], index=False, lineterminator="\n")
    paths["diagnostics"].write_text(
        json.dumps(diagnostics, indent=2) + "\n", encoding="utf-8"
    )
    paths["manifest"].write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    checksums = output / "CHECKSUMS.sha256"
    checksums.write_text(
        "".join(
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
            for path in paths.values()
        ),
        encoding="ascii",
    )
    paths["checksums"] = checksums
    return paths
