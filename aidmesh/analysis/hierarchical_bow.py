"""Hierarchical and mechanism-aware analysis for the BOW temporal campaign."""

from __future__ import annotations

import hashlib
from typing import Any

import numpy as np
import pandas as pd
import statsmodels.formula.api as smf

from aidmesh.experiments.bow_factorial import FACTOR_LEVELS


def _paired_deltas(
    frame: pd.DataFrame,
    *,
    outcome: str,
    reference: str,
) -> pd.DataFrame:
    candidate_keys = [
        "family",
        "registered_context",
        "context",
        "seed",
        "cap",
        "quorum_availability",
        "cell",
        *FACTOR_LEVELS,
    ]
    keys = [key for key in candidate_keys if key in frame.columns]
    required = {*keys, "policy", outcome}
    if missing := required - set(frame.columns):
        raise ValueError(f"missing analysis columns: {sorted(missing)}")
    reference_rows = frame.loc[frame["policy"] == reference, keys + [outcome]].rename(
        columns={outcome: "reference_outcome"}
    )
    if reference_rows.empty:
        raise ValueError(f"reference policy is absent: {reference}")
    paired = frame.merge(reference_rows, on=keys, how="inner", validate="many_to_one")
    paired = paired.loc[paired["policy"] != reference].copy()
    paired["delta"] = paired[outcome] - paired["reference_outcome"]
    return paired


def hierarchical_policy_effects(
    frame: pd.DataFrame,
    *,
    outcome: str = "completion_rate",
    reference: str = "optimized_static",
    repetitions: int = 2000,
    seed: int = 20260822,
) -> pd.DataFrame:
    """Family/context/seed bootstrap of paired policy effects.

    When ``family`` is present it is the outer sampling unit, contexts are
    resampled within family, and seeds within context.  Otherwise context is
    the outer unit.  All factorial/cap/witness cells for a seed move together.
    Point estimates use the same equal-family/equal-context weighting as the
    bootstrap instead of allowing a family with more contexts to dominate.
    """

    if repetitions <= 0:
        raise ValueError("bootstrap repetitions must be positive")
    paired = _paired_deltas(frame, outcome=outcome, reference=reference)
    context_column = "registered_context" if "registered_context" in paired else "context"
    contexts = tuple(sorted(paired[context_column].unique()))
    if not contexts:
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    for policy, policy_frame in paired.groupby("policy", sort=True):
        context_effects = policy_frame.groupby(context_column)["delta"].mean()
        has_families = "family" in policy_frame.columns
        cluster_columns = [context_column, "seed"]
        if has_families:
            cluster_columns.insert(0, "family")
        # Collapse all cap/witness/factorial cells once.  The previous
        # implementation repeatedly filtered the full DataFrame inside every
        # bootstrap draw (millions of boolean scans for the final campaign).
        # These immutable per-seed arrays implement the identical resampling
        # hierarchy without changing its statistical unit.
        clustered = (
            policy_frame.groupby(cluster_columns, sort=True, as_index=False)["delta"]
            .mean()
        )
        if has_families:
            outer_units = tuple(sorted(policy_frame["family"].unique()))
            family_effects = (
                policy_frame.groupby(["family", context_column])["delta"]
                .mean()
                .groupby("family")
                .mean()
            )
            estimate = float(family_effects.mean())
        else:
            outer_units = contexts
            family_effects = context_effects
            estimate = float(context_effects.mean())
        nested: dict[str, dict[str, np.ndarray]] = {}
        if has_families:
            for outer, outer_rows in clustered.groupby("family", sort=True):
                nested[str(outer)] = {
                    str(context): context_rows["delta"].to_numpy(dtype=float)
                    for context, context_rows in outer_rows.groupby(
                        context_column, sort=True
                    )
                }
        else:
            nested = {
                str(context): {
                    str(context): context_rows["delta"].to_numpy(dtype=float)
                }
                for context, context_rows in clustered.groupby(
                    context_column, sort=True
                )
            }
        rng = np.random.default_rng(
            seed
            + int(
                hashlib.sha256(f"{policy}|{outcome}".encode()).hexdigest()[:8],
                16,
            )
        )
        draws = np.empty(repetitions, dtype=float)
        for repetition in range(repetitions):
            sampled_outer = rng.choice(outer_units, size=len(outer_units), replace=True)
            outer_values: list[float] = []
            for outer in sampled_outer:
                context_arrays = nested[str(outer)]
                available_contexts = tuple(context_arrays)
                sampled_contexts = rng.choice(
                    available_contexts,
                    size=len(available_contexts),
                    replace=True,
                )
                context_values: list[float] = []
                for context in sampled_contexts:
                    seed_values = context_arrays[str(context)]
                    sampled_seeds = rng.choice(
                        seed_values, size=len(seed_values), replace=True
                    )
                    context_values.append(float(np.mean(sampled_seeds)))
                outer_values.append(float(np.mean(context_values)))
            draws[repetition] = float(np.mean(outer_values))
        rows.append(
            {
                "policy": policy,
                "reference": reference,
                "outcome": outcome,
                "paired_rows": len(policy_frame),
                "families": len(outer_units) if has_families else None,
                "contexts": len(contexts),
                "context_seed_clusters": policy_frame[
                    ["context", "seed"]
                ].drop_duplicates().shape[0],
                "mean_effect": estimate,
                "raw_row_weighted_effect": float(policy_frame["delta"].mean()),
                "ci_low": float(np.quantile(draws, 0.025)),
                "ci_high": float(np.quantile(draws, 0.975)),
                "min_context_effect": float(context_effects.min()),
                "max_context_effect": float(context_effects.max()),
                "sign_heterogeneous": bool(
                    family_effects.min() < 0 < family_effects.max()
                ),
            }
        )
    return pd.DataFrame(rows)


def fit_factorial_mechanism_model(
    frame: pd.DataFrame,
    *,
    policy: str = "adatransfer",
    reference: str = "optimized_static",
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Fit a context-adjusted, cluster-robust mechanism model on paired gains."""

    paired = _paired_deltas(
        frame,
        outcome="completion_rate",
        reference=reference,
    )
    paired = paired.loc[paired["policy"] == policy].copy()
    missing = set(FACTOR_LEVELS) - set(paired.columns)
    if missing:
        raise ValueError(f"factorial mechanism columns are absent: {sorted(missing)}")
    if paired.empty:
        raise ValueError(f"policy is absent after pairing: {policy}")
    terms = [f"C({factor})" for factor in FACTOR_LEVELS]
    terms.extend(
        (
            "C(demand_drift):C(cap_tightness)",
            "C(demand_drift):C(transfer_reliability)",
            "C(buffer_provisioning):C(cross_class_traffic)",
            "C(contact_predictability):C(transfer_reliability)",
            "C(witness_availability):C(cross_class_traffic)",
        )
    )
    fixed_effects = ["C(context)"]
    if "family" in paired:
        fixed_effects.insert(0, "C(family)")
    formula = "delta ~ " + " + ".join((*terms, *fixed_effects))
    groups = paired["context"].astype(str) + ":" + paired["seed"].astype(str)
    if "family" in paired:
        groups = paired["family"].astype(str) + ":" + groups
    fitted = smf.ols(formula, data=paired).fit(
        cov_type="cluster", cov_kwds={"groups": groups}
    )
    confidence = fitted.conf_int()
    coefficients = pd.DataFrame(
        {
            "term": fitted.params.index,
            "estimate": fitted.params.to_numpy(),
            "std_error": fitted.bse.to_numpy(),
            "p_value": fitted.pvalues.to_numpy(),
            "ci_low": confidence[0].to_numpy(),
            "ci_high": confidence[1].to_numpy(),
        }
    )
    diagnostics = {
        "policy": policy,
        "reference": reference,
        "formula": formula,
        "observations": int(fitted.nobs),
        "context_seed_clusters": int(groups.nunique()),
        "r_squared": float(fitted.rsquared),
        "covariance": "cluster robust by context:seed",
    }
    return coefficients, diagnostics
