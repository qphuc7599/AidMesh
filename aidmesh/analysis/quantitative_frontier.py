"""Exact placement identities for the AidMesh availability-risk frontier.

The results in this module are deliberately deterministic.  They separate the
aggregate fallback budget from the placement penalty and from capacity lost by
fail-closed transfers.  The corresponding identities are stated as theorems in
the manuscript and exhaustively/property tested in the artifact.
"""

from __future__ import annotations

from collections.abc import Sequence


def _nonnegative_vector(values: Sequence[int], *, name: str) -> tuple[int, ...]:
    vector = tuple(int(value) for value in values)
    if not vector:
        raise ValueError(f"{name} must not be empty")
    if any(value < 0 for value in vector):
        raise ValueError(f"{name} must be nonnegative")
    return vector


def fallback_completion(capacity: Sequence[int], residual_demand: Sequence[int]) -> int:
    """Return fallback value completed at co-located capacity and demand."""

    placed = _nonnegative_vector(capacity, name="capacity")
    demand = _nonnegative_vector(residual_demand, name="residual_demand")
    if len(placed) != len(demand):
        raise ValueError("capacity and residual_demand must have the same dimension")
    return sum(min(rights, requests) for rights, requests in zip(placed, demand, strict=True))


def l1_misplacement(capacity: Sequence[int], residual_demand: Sequence[int]) -> int:
    """Return the L1 distance between placed fallback capacity and demand."""

    placed = _nonnegative_vector(capacity, name="capacity")
    demand = _nonnegative_vector(residual_demand, name="residual_demand")
    if len(placed) != len(demand):
        raise ValueError("capacity and residual_demand must have the same dimension")
    return sum(abs(rights - requests) for rights, requests in zip(placed, demand, strict=True))


def completion_from_identity(capacity: Sequence[int], residual_demand: Sequence[int]) -> int:
    """Evaluate (B + D - ||b-d||_1) / 2 and require an integral result."""

    placed = _nonnegative_vector(capacity, name="capacity")
    demand = _nonnegative_vector(residual_demand, name="residual_demand")
    if len(placed) != len(demand):
        raise ValueError("capacity and residual_demand must have the same dimension")
    numerator = sum(placed) + sum(demand) - l1_misplacement(placed, demand)
    if numerator % 2:
        raise AssertionError("integer vectors must yield an even placement numerator")
    return numerator // 2


def oracle_completion(capacity: Sequence[int], residual_demand: Sequence[int]) -> int:
    """Completion of an oracle that can place aggregate capacity without delay."""

    placed = _nonnegative_vector(capacity, name="capacity")
    demand = _nonnegative_vector(residual_demand, name="residual_demand")
    if len(placed) != len(demand):
        raise ValueError("capacity and residual_demand must have the same dimension")
    return min(sum(placed), sum(demand))


def placement_gap(capacity: Sequence[int], residual_demand: Sequence[int]) -> int:
    """Return the exact completion loss relative to aggregate oracle placement."""

    return oracle_completion(capacity, residual_demand) - fallback_completion(
        capacity, residual_demand
    )


def relocation_gain(
    static_capacity: Sequence[int],
    delivered_capacity: Sequence[int],
    residual_demand: Sequence[int],
) -> int:
    """Return the exact completion gain after causal relocation.

    ``delivered_capacity`` excludes capacity stranded in fail-closed transfers.
    Conservation therefore implies ``sum(delivered_capacity) <=
    sum(static_capacity)``.  A positive return value means relocation strictly
    improves completion under the same initial risk budget.
    """

    static = _nonnegative_vector(static_capacity, name="static_capacity")
    delivered = _nonnegative_vector(delivered_capacity, name="delivered_capacity")
    demand = _nonnegative_vector(residual_demand, name="residual_demand")
    if len(static) != len(delivered) or len(static) != len(demand):
        raise ValueError("all vectors must have the same dimension")
    if sum(delivered) > sum(static):
        raise ValueError("delivered capacity exceeds the initial conserved budget")
    return fallback_completion(delivered, demand) - fallback_completion(static, demand)


def relocation_gain_from_identity(
    static_capacity: Sequence[int],
    delivered_capacity: Sequence[int],
    residual_demand: Sequence[int],
) -> int:
    """Evaluate the exact L1 reduction minus fail-closed stranding identity.

    If ``ell = sum(static)-sum(delivered)``, then

        gain = (||static-demand||_1 - ||delivered-demand||_1 - ell) / 2.
    """

    static = _nonnegative_vector(static_capacity, name="static_capacity")
    delivered = _nonnegative_vector(delivered_capacity, name="delivered_capacity")
    demand = _nonnegative_vector(residual_demand, name="residual_demand")
    if len(static) != len(delivered) or len(static) != len(demand):
        raise ValueError("all vectors must have the same dimension")
    stranded = sum(static) - sum(delivered)
    if stranded < 0:
        raise ValueError("delivered capacity exceeds the initial conserved budget")
    numerator = (
        l1_misplacement(static, demand)
        - l1_misplacement(delivered, demand)
        - stranded
    )
    if numerator % 2:
        raise AssertionError("integer vectors must yield an even relocation numerator")
    return numerator // 2


def relocation_strictly_improves(
    static_capacity: Sequence[int],
    delivered_capacity: Sequence[int],
    residual_demand: Sequence[int],
) -> bool:
    """Necessary and sufficient condition for relocation to beat static placement."""

    return relocation_gain_from_identity(static_capacity, delivered_capacity, residual_demand) > 0


def causal_policy_objective(
    initial_budget: int,
    delivered_capacity: Sequence[int],
    residual_demand: Sequence[int],
) -> int:
    """Return L1 misplacement plus fail-closed stranding for a causal policy."""

    if initial_budget < 0:
        raise ValueError("initial_budget must be nonnegative")
    delivered = _nonnegative_vector(delivered_capacity, name="delivered_capacity")
    demand = _nonnegative_vector(residual_demand, name="residual_demand")
    if len(delivered) != len(demand):
        raise ValueError("delivered_capacity and residual_demand must match")
    stranded = initial_budget - sum(delivered)
    if stranded < 0:
        raise ValueError("delivered capacity exceeds the initial conserved budget")
    return l1_misplacement(delivered, demand) + stranded


def demand_drift_bound(
    estimated_demand: Sequence[int], actual_demand: Sequence[int]
) -> int:
    """Return the pairwise static-placement regret bound under demand drift."""

    estimated = _nonnegative_vector(estimated_demand, name="estimated_demand")
    actual = _nonnegative_vector(actual_demand, name="actual_demand")
    if len(estimated) != len(actual):
        raise ValueError("estimated_demand and actual_demand must match")
    return 2 * sum(abs(left - right) for left, right in zip(estimated, actual, strict=True))
