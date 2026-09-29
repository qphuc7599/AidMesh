"""Two-level temporal-contact screening design for frontier mechanisms."""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from aidmesh.experiments.temporal_frontier import (
    HOUR_US,
    TemporalContactSimulator,
    TemporalPathIndex,
    _calibration_roles,
    _evaluate_method,
    _sha256,
    generate_heldout_requests,
    load_contacts_csv,
)
from aidmesh.simulator.dtn import RoutingPolicy

FACTOR_LEVELS: dict[str, tuple[str, str]] = {
    "demand_drift": ("stable", "reversed"),
    "cap_tightness": ("tight", "loose"),
    "witness_availability": ("low", "high"),
    "transfer_quality": ("good", "degraded"),
    "gateway_reachability": ("central", "peripheral"),
    "buffer_provisioning": ("constrained", "provisioned"),
}


def _factor_settings(levels: dict[str, str]) -> dict[str, Any]:
    return {
        "service_weights": (
            (0.55, 0.25, 0.15, 0.05)
            if levels["demand_drift"] == "stable"
            else (0.05, 0.15, 0.25, 0.55)
        ),
        "cap_fraction": 0.15 if levels["cap_tightness"] == "tight" else 0.60,
        "quorum_probability": (
            0.20 if levels["witness_availability"] == "low" else 0.80
        ),
        "transfer_loss": 0.0 if levels["transfer_quality"] == "good" else 0.25,
        "transfer_delay_us": (
            0 if levels["transfer_quality"] == "good" else 2 * HOUR_US
        ),
        "buffer_bytes": (
            16 * 1024 if levels["buffer_provisioning"] == "constrained" else 96 * 1024
        ),
        "copy_budget": 2 if levels["buffer_provisioning"] == "constrained" else 8,
    }


def _all_cells() -> list[dict[str, str]]:
    names = tuple(FACTOR_LEVELS)
    return [
        dict(zip(names, values, strict=True))
        for values in itertools.product(*(FACTOR_LEVELS[name] for name in names))
    ]


def run_temporal_factorial(
    trace_root: Path,
    *,
    contexts: tuple[str, ...] = ("invs13", "lh10", "sfhh"),
    seeds: tuple[int, ...] = tuple(range(7301, 7309)),
    calibration_fraction: float = 0.40,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    trace_manifest: dict[str, Any] = {}
    cells = _all_cells()
    for context in contexts:
        contacts_path = trace_root / context / "contacts.csv"
        contacts = load_contacts_csv(contacts_path)
        start_us = min(contact.start_us for contact in contacts)
        end_us = max(contact.end_us for contact in contacts)
        cutoff_us = start_us + round((end_us - start_us) * calibration_fraction)
        roles = _calibration_roles(contacts, cutoff_us)
        path_index = TemporalPathIndex(contacts)
        trace_manifest[context] = {
            "contacts_path": str(contacts_path),
            "contacts_sha256": _sha256(contacts_path),
            "contacts": len(contacts),
            "cutoff_us": cutoff_us,
            "roles": {key: list(value) for key, value in roles.items()},
        }
        for seed in seeds:
            network_cache: dict[tuple[str, str], tuple[Any, dict[str, int]]] = {}
            request_cache: dict[str, tuple[Any, ...]] = {}
            for levels in cells:
                settings = _factor_settings(levels)
                demand_level = levels["demand_drift"]
                if demand_level not in request_cache:
                    request_cache[demand_level] = generate_heldout_requests(
                        contacts,
                        roles,
                        context=context,
                        seed=seed,
                        cutoff_us=cutoff_us,
                        service_weights=settings["service_weights"],
                    )
                requests = request_cache[demand_level]
                network_key = (demand_level, levels["buffer_provisioning"])
                if network_key not in network_cache:
                    network = TemporalContactSimulator(
                        contacts=contacts,
                        bundles=tuple(request.bundle for request in requests),
                        policy=RoutingPolicy.SPRAY_WAIT,
                        buffer_bytes=settings["buffer_bytes"],
                        copy_budget=settings["copy_budget"],
                    ).run()
                    deliveries = {
                        delivery.bundle_id: delivery.delivered_us
                        for delivery in network.deliveries
                    }
                    network_cache[network_key] = (network, deliveries)
                network, deliveries = network_cache[network_key]
                cap = max(
                    len(roles["sites"]),
                    round(len(requests) * settings["cap_fraction"]),
                )
                gateway = (
                    roles["relays"][0]
                    if levels["gateway_reachability"] == "central"
                    else roles["relays"][-1]
                )
                experiment_id = "|".join(
                    ["factorial-v1", context, str(seed)]
                    + [f"{name}={levels[name]}" for name in FACTOR_LEVELS]
                )
                for method in ("static_rights", "transferable_rights"):
                    result = _evaluate_method(
                        requests,
                        deliveries,
                        method=method,
                        cap=cap,
                        sites=roles["sites"],
                        path_index=path_index,
                        gateway=gateway,
                        quorum_probability=settings["quorum_probability"],
                        transfer_loss=settings["transfer_loss"],
                        transfer_delay_us=settings["transfer_delay_us"],
                        experiment_id=experiment_id,
                    )
                    rows.append(
                        {
                            "context": context,
                            "seed": seed,
                            **levels,
                            "cap": cap,
                            "quorum_probability": settings["quorum_probability"],
                            "transfer_loss": settings["transfer_loss"],
                            "transfer_delay_h": settings["transfer_delay_us"] / HOUR_US,
                            "buffer_bytes": settings["buffer_bytes"],
                            "copy_budget": settings["copy_budget"],
                            "gateway": gateway,
                            "network_event_checksum": network.event_checksum,
                            **result,
                        }
                    )
    frame = pd.DataFrame(rows)
    manifest = {
        "schema_version": 1,
        "design": "complete 2^6 temporal-contact screening factorial",
        "contexts": list(contexts),
        "seeds": list(seeds),
        "factor_levels": {key: list(value) for key, value in FACTOR_LEVELS.items()},
        "factor_values": {
            "demand_weights": {
                "stable": [0.55, 0.25, 0.15, 0.05],
                "reversed": [0.05, 0.15, 0.25, 0.55],
            },
            "cap_fraction": {"tight": 0.15, "loose": 0.60},
            "quorum_probability": {"low": 0.20, "high": 0.80},
            "transfer_quality": {
                "good": {"loss": 0.0, "extra_delay_h": 0.0},
                "degraded": {"loss": 0.25, "extra_delay_h": 2.0},
            },
            "gateway": {"central": "first relay", "peripheral": "last relay"},
            "buffer_provisioning": {
                "constrained": {"bytes": 16 * 1024, "copies": 2},
                "provisioned": {"bytes": 96 * 1024, "copies": 8},
            },
        },
        "methods": ["static_rights", "transferable_rights"],
        "trace_semantics": (
            "public proximity contacts with deterministic synthetic overlay and "
            "binary Spray-and-Wait request routing"
        ),
        "traces": trace_manifest,
    }
    return frame, manifest


def paired_factorial_outcomes(frame: pd.DataFrame) -> pd.DataFrame:
    keys = ["context", "seed", *FACTOR_LEVELS]
    pivot = frame.pivot(index=keys, columns="method", values="completed").reset_index()
    pivot["transfer_gain"] = pivot["transferable_rights"] - pivot["static_rights"]
    return pivot


def _bootstrap_mean(
    values: np.ndarray, *, seed: int, repetitions: int = 5000
) -> tuple[float, float]:
    if len(values) == 0:
        return float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    draws = rng.choice(values, size=(repetitions, len(values)), replace=True).mean(axis=1)
    return float(np.quantile(draws, 0.025)), float(np.quantile(draws, 0.975))


def factorial_main_effects(outcomes: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for context in (*sorted(outcomes["context"].unique()), "pooled"):
        subset = outcomes if context == "pooled" else outcomes[outcomes["context"] == context]
        for factor, (low, high) in FACTOR_LEVELS.items():
            match = ["context", "seed", *[name for name in FACTOR_LEVELS if name != factor]]
            paired = subset.pivot_table(
                index=match,
                columns=factor,
                values="transfer_gain",
                aggfunc="first",
            ).dropna(subset=[low, high])
            cell_effects = (paired[high] - paired[low]).rename("effect").reset_index()
            cluster_effects = (
                cell_effects.groupby(["context", "seed"], as_index=False)["effect"]
                .mean()["effect"]
                .to_numpy(dtype=float)
            )
            ci_low, ci_high = _bootstrap_mean(
                cluster_effects,
                seed=int(hashlib.sha256(f"{context}|{factor}".encode()).hexdigest()[:8], 16),
            )
            rows.append(
                {
                    "context": context,
                    "factor": factor,
                    "contrast": f"{high} minus {low}",
                    "paired_cells": len(cell_effects),
                    "context_seed_clusters": len(cluster_effects),
                    "effect_on_transfer_gain": float(cluster_effects.mean()),
                    "ci_low": ci_low,
                    "ci_high": ci_high,
                }
            )
    return pd.DataFrame(rows)


def write_temporal_factorial(
    trace_root: Path,
    output: Path,
    *,
    seeds: tuple[int, ...] = tuple(range(7301, 7309)),
) -> dict[str, Path]:
    output.mkdir(parents=True, exist_ok=True)
    frame, manifest = run_temporal_factorial(trace_root, seeds=seeds)
    outcomes = paired_factorial_outcomes(frame)
    effects = factorial_main_effects(outcomes)
    raw_path = output / "temporal_factorial_runs.csv"
    outcomes_path = output / "temporal_factorial_paired_outcomes.csv"
    effects_path = output / "temporal_factorial_main_effects.csv"
    manifest_path = output / "temporal_factorial_manifest.json"
    frame.to_csv(raw_path, index=False, lineterminator="\n")
    outcomes.to_csv(outcomes_path, index=False, lineterminator="\n")
    effects.to_csv(effects_path, index=False, lineterminator="\n")
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    pooled = effects[effects["context"] == "pooled"].copy()
    fig, axis = plt.subplots(figsize=(8.2, 4.4))
    positions = np.arange(len(pooled))
    estimates = pooled["effect_on_transfer_gain"].to_numpy()
    errors = np.vstack(
        [estimates - pooled["ci_low"].to_numpy(), pooled["ci_high"].to_numpy() - estimates]
    )
    axis.bar(positions, estimates, color="#277da1", yerr=errors, capsize=4)
    axis.axhline(0.0, color="black", linewidth=0.8)
    axis.set_xticks(positions, [name.replace("_", "\n") for name in pooled["factor"]])
    axis.set_ylabel("Main effect on transfer-minus-static completions")
    axis.set_title("Complete $2^6$ temporal-contact screening design")
    axis.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    figure_path = output / "temporal_factorial_main_effects.pdf"
    fig.savefig(figure_path, bbox_inches="tight")
    plt.close(fig)

    checksum_path = output / "CHECKSUMS.sha256"
    files = [raw_path, outcomes_path, effects_path, manifest_path, figure_path]
    checksum_path.write_text(
        "".join(
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
            for path in files
        ),
        encoding="ascii",
    )
    return {
        "runs": raw_path,
        "outcomes": outcomes_path,
        "effects": effects_path,
        "manifest": manifest_path,
        "figure": figure_path,
        "checksums": checksum_path,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seeds", nargs="+", type=int, default=list(range(7301, 7309)))
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    paths = write_temporal_factorial(
        args.trace_root,
        args.output,
        seeds=tuple(args.seeds),
    )
    print(json.dumps({key: str(value) for key, value in paths.items()}, indent=2))
