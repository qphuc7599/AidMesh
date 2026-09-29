"""Registered cap-grid safety-availability frontier and paired hypervolume.

The frontier estimand is registered before the confirmatory run: for one seed
and one quorum family, each registered cap produces a point
``(normalized exposure, completion)``.  The scalar frontier quality is the
2-D hypervolume dominated by those points with respect to the reference point
``(exposure = 1, completion = 0)``, where exposure is normalized by the largest
registered cap so every placement policy shares one axis scale.
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import bootstrap
from statsmodels.stats.multitest import multipletests

FRONTIER_PATTERN = re.compile(r"^FRONT_C(?P<cap>\d{3})_K(?P<K>\d{2})Q(?P<q>\d{2})$")


def hypervolume_2d(points: list[tuple[float, float]], *, reference_exposure: float = 1.0) -> float:
    """Dominated area for maximize-completion / minimize-exposure points.

    A point dominates the region right of its exposure and below its
    completion.  The union area equals the integral over exposure of the best
    completion achievable at or below that exposure.
    """

    if not points:
        return 0.0
    ordered = sorted(points)
    area = 0.0
    best = 0.0
    for index, (exposure, completion) in enumerate(ordered):
        if exposure >= reference_exposure:
            break
        best = max(best, completion)
        next_exposure = (
            ordered[index + 1][0] if index + 1 < len(ordered) else reference_exposure
        )
        segment = min(next_exposure, reference_exposure) - exposure
        if segment > 0:
            area += segment * best
    return area


def load_frontier_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Extract frontier scenario rows and annotate cap and quorum family."""

    rows = []
    for _, row in frame.iterrows():
        match = FRONTIER_PATTERN.match(str(row["scenario_id"]))
        if match is None:
            continue
        rows.append(
            {
                "scenario_id": row["scenario_id"],
                "method": row["method"],
                "seed": int(row["seed"]),
                "cap": int(match.group("cap")),
                "quorum_family": f"K{int(match.group('K'))}q{int(match.group('q'))}",
                "completion_rate": float(row["completion_rate"]),
                "duplicate_exposure": float(row["duplicate_exposure"]),
                "exposure_cap_ratio": float(row["exposure_cap_ratio"]),
            }
        )
    if not rows:
        raise ValueError("no frontier scenario rows (FRONT_C***_K**Q**) found")
    return pd.DataFrame(rows)


def per_seed_hypervolume(frontier: pd.DataFrame) -> pd.DataFrame:
    """One hypervolume per (quorum family, method, seed) over the cap grid."""

    max_cap = float(frontier["cap"].max())
    records = []
    grouped = frontier.groupby(["quorum_family", "method", "seed"], sort=True)
    expected_caps = set(frontier["cap"].unique())
    for (family, method, seed), group in grouped:
        if set(group["cap"]) != expected_caps:
            raise ValueError(
                f"incomplete cap grid for {family}/{method}/seed {seed}: {sorted(group['cap'])}"
            )
        points = [
            (float(row["duplicate_exposure"]) / max_cap, float(row["completion_rate"]))
            for _, row in group.iterrows()
        ]
        records.append(
            {
                "quorum_family": family,
                "method": method,
                "seed": seed,
                "hypervolume": hypervolume_2d(points),
            }
        )
    return pd.DataFrame(records)


def paired_hypervolume_contrasts(
    volumes: pd.DataFrame,
    *,
    treatment: str = "aidmesh_transferable_rights",
    control: str = "aidmesh_static_rights",
    confidence_level: float = 0.95,
    bootstrap_resamples: int = 20000,
    permutation_resamples: int = 20000,
    rng_seed: int = 20260712,
) -> pd.DataFrame:
    """Registered paired BCa interval and sign-flip permutation per quorum family."""

    rng = np.random.default_rng(rng_seed)
    rows = []
    for family, group in volumes.groupby("quorum_family", sort=True):
        pivot = group.pivot(index="seed", columns="method", values="hypervolume")
        if treatment not in pivot or control not in pivot:
            raise ValueError(f"missing methods for family {family}")
        differences = (pivot[treatment] - pivot[control]).dropna().to_numpy(dtype=float)
        if len(differences) < 2:
            raise ValueError(f"not enough paired seeds for family {family}")
        interval = bootstrap(
            (differences,),
            np.mean,
            confidence_level=confidence_level,
            n_resamples=bootstrap_resamples,
            method="BCa",
            rng=rng,
        ).confidence_interval
        observed = float(np.mean(differences))
        signs = rng.choice(np.array([-1.0, 1.0]), size=(permutation_resamples, len(differences)))
        simulated = np.mean(signs * differences, axis=1)
        p_value = float((np.count_nonzero(simulated >= observed) + 1) / (permutation_resamples + 1))
        rows.append(
            {
                "quorum_family": family,
                "treatment": treatment,
                "control": control,
                "pairs": len(differences),
                "mean_hypervolume_treatment": float(pivot[treatment].mean()),
                "mean_hypervolume_control": float(pivot[control].mean()),
                "mean_difference": observed,
                "ci_low": float(interval.low),
                "ci_high": float(interval.high),
                "permutation_p_value_greater": p_value,
            }
        )
    frame = pd.DataFrame(rows)
    if not frame.empty:
        frame["holm_adjusted_p_value"] = multipletests(
            frame["permutation_p_value_greater"],
            alpha=1 - confidence_level,
            method="holm",
        )[1]
    return frame


def frontier_point_summary(frontier: pd.DataFrame) -> pd.DataFrame:
    """Mean frontier coordinates per (quorum family, method, cap) for figures."""

    records = []
    grouped = frontier.groupby(["quorum_family", "method", "cap"], sort=True)
    max_cap = float(frontier["cap"].max())
    for (family, method, cap), group in grouped:
        records.append(
            {
                "quorum_family": family,
                "method": method,
                "cap": cap,
                "seeds": int(group["seed"].nunique()),
                "completion_mean": float(group["completion_rate"].mean()),
                "completion_sd": float(group["completion_rate"].std(ddof=1)),
                "exposure_mean": float(group["duplicate_exposure"].mean()),
                "exposure_norm_mean": float(group["duplicate_exposure"].mean() / max_cap),
                "exposure_cap_ratio_mean": float(group["exposure_cap_ratio"].mean()),
                "exposure_cap_ratio_max": float(group["exposure_cap_ratio"].max()),
            }
        )
    return pd.DataFrame(records)


def write_frontier_assets(frame: pd.DataFrame, output: Path) -> dict[str, Path]:
    """Compute and persist the full registered frontier analysis."""

    output.mkdir(parents=True, exist_ok=True)
    frontier = load_frontier_frame(frame)
    volumes = per_seed_hypervolume(frontier)
    contrasts = paired_hypervolume_contrasts(volumes)
    summary = frontier_point_summary(frontier)
    paths = {
        "points": output / "frontier_points.csv",
        "hypervolumes": output / "frontier_hypervolumes.csv",
        "contrasts": output / "frontier_hypervolume_contrasts.csv",
        "summary": output / "frontier_point_summary.csv",
    }
    frontier.to_csv(paths["points"], index=False)
    volumes.to_csv(paths["hypervolumes"], index=False)
    contrasts.to_csv(paths["contrasts"], index=False)
    summary.to_csv(paths["summary"], index=False)
    return paths
