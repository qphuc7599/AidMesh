"""Prediction-robust causal right relocation with an immutable risk reserve."""

from __future__ import annotations

import math
from dataclasses import dataclass


def project_simplex_with_floor(
    values: tuple[float, ...], *, budget: float, floor: float
) -> tuple[float, ...]:
    """Euclidean projection onto ``sum(x)=budget, x_i>=floor``."""

    if not values or budget < 0 or floor < 0 or floor * len(values) > budget:
        raise ValueError("infeasible simplex projection")
    residual_budget = budget - floor * len(values)
    shifted = [value - floor for value in values]
    ordered = sorted(shifted, reverse=True)
    cumulative = 0.0
    threshold = 0.0
    for index, value in enumerate(ordered, start=1):
        cumulative += value
        candidate = (cumulative - residual_budget) / index
        if index == len(ordered) or candidate >= ordered[index]:
            threshold = candidate
            break
    projected = tuple(max(0.0, value - threshold) + floor for value in shifted)
    correction = budget - sum(projected)
    if abs(correction) > 1e-9:
        largest = max(range(len(projected)), key=projected.__getitem__)
        mutable = list(projected)
        mutable[largest] += correction
        projected = tuple(mutable)
    return projected


@dataclass(frozen=True)
class TransferDecision:
    donor_index: int | None
    mismatch_reduction_lcb: float
    stranding_ucb: float
    congestion_ucb: float
    safe_path_interference_ucb: float
    margin: float
    reserve_floor: int

    @property
    def approved(self) -> bool:
        return self.donor_index is not None and self.margin > 0


class AdaTransferAllocator:
    """Projected-OGD target allocator plus a confidence-gated transfer rule.

    The target update uses only demand observed before the call.  A fixed equal
    reserve is never donated, so prediction error cannot violate the risk cap or
    remove the policy's reserve-separable competitive floor.
    """

    def __init__(
        self,
        *,
        sites: int,
        budget: int,
        initial_target: tuple[float, ...],
        reserve_fraction: float = 0.5,
        step_size: float | None = None,
        confidence_delta: float = 0.05,
        gate_mode: str = "confidence",
    ) -> None:
        if sites <= 0 or budget <= 0 or len(initial_target) != sites:
            raise ValueError("invalid allocator dimension or budget")
        if not 0.0 <= reserve_fraction <= 1.0:
            raise ValueError("reserve_fraction must be in [0, 1]")
        if not 0.0 < confidence_delta < 1.0:
            raise ValueError("confidence_delta must be in (0, 1)")
        if gate_mode not in {"confidence", "expected_value"}:
            raise ValueError("gate_mode must be confidence or expected_value")
        self.sites = sites
        self.budget = budget
        self.reserve_fraction = reserve_fraction
        self.reserve_floor = math.floor(reserve_fraction * budget / sites)
        self.step_size = step_size or budget / max(1.0, math.sqrt(sites * budget))
        self.confidence_delta = confidence_delta
        self.gate_mode = gate_mode
        self.target = project_simplex_with_floor(
            initial_target,
            budget=float(budget),
            floor=float(self.reserve_floor),
        )
        self.updates = 0

    def update_target(self, observed_demand: tuple[float, ...]) -> tuple[float, ...]:
        if len(observed_demand) != self.sites or any(value < 0 for value in observed_demand):
            raise ValueError("observed_demand must match sites and be nonnegative")
        total = sum(observed_demand)
        if total <= 0:
            return self.target
        forecast = tuple(self.budget * value / total for value in observed_demand)
        gradient = tuple(
            0.0 if math.isclose(left, right) else (1.0 if left > right else -1.0)
            for left, right in zip(self.target, forecast, strict=True)
        )
        eta = self.step_size / math.sqrt(self.updates + 1)
        proposal = tuple(
            value - eta * derivative
            for value, derivative in zip(self.target, gradient, strict=True)
        )
        self.target = project_simplex_with_floor(
            proposal,
            budget=float(self.budget),
            floor=float(self.reserve_floor),
        )
        self.updates += 1
        return self.target

    def decide(
        self,
        *,
        target_index: int,
        balances: tuple[int, ...],
        observed_samples: int,
        path_reliability: tuple[float, ...],
        queue_pressure: float,
        safe_path_pressure: float,
    ) -> TransferDecision:
        if not 0 <= target_index < self.sites or len(balances) != self.sites:
            raise ValueError("invalid target or balances")
        if len(path_reliability) != self.sites:
            raise ValueError("path_reliability must match sites")
        if any(value < 0 for value in balances):
            raise ValueError("balances must be nonnegative")
        if any(not 0.0 <= value <= 1.0 for value in path_reliability):
            raise ValueError("path reliability must be in [0, 1]")
        if not 0.0 <= queue_pressure <= 1.0 or not 0.0 <= safe_path_pressure <= 1.0:
            raise ValueError("pressures must be in [0, 1]")

        candidates = [
            index
            for index, balance in enumerate(balances)
            if index != target_index
            and balance > self.reserve_floor
            and balance > math.floor(self.target[index])
        ]
        donor = (
            min(
                candidates,
                key=lambda index: (
                    -(balances[index] - self.target[index]) * path_reliability[index],
                    index,
                ),
            )
            if candidates
            else None
        )
        shortage = max(0.0, self.target[target_index] - balances[target_index])
        reliability = path_reliability[donor] if donor is not None else 0.0
        if self.gate_mode == "confidence":
            uncertainty = math.sqrt(
                math.log(2.0 / self.confidence_delta)
                / (2.0 * max(1, observed_samples))
            )
            mismatch_lcb = max(0.0, 2.0 * min(1.0, shortage) - 2.0 * uncertainty)
            stranding_ucb = min(1.0, 1.0 - reliability + uncertainty)
            congestion_ucb = min(1.0, queue_pressure + uncertainty / 2.0)
            interference_ucb = min(1.0, safe_path_pressure + uncertainty / 2.0)
            margin = mismatch_lcb - (
                2.0 * stranding_ucb + congestion_ucb + interference_ucb
            )
        else:
            # Causal nominal expected-value score.  One delivered right can
            # satisfy the already-arrived shortage; a stranded right costs one
            # unit.  Queue and safe-path pressure are contemporaneous observed
            # costs, each conservatively weighted by one quarter.  Reliability
            # is estimated exclusively from the calibration prefix.
            mismatch_lcb = min(1.0, shortage) * reliability
            stranding_ucb = 1.0 - reliability
            congestion_ucb = queue_pressure
            interference_ucb = safe_path_pressure
            margin = mismatch_lcb - stranding_ucb - 0.25 * (
                congestion_ucb + interference_ucb
            )
        # Retain the best feasible donor even when the gate rejects the move.
        # ``approved`` still requires a strictly positive margin.  Keeping the
        # candidate makes the gate auditable: campaign outputs can distinguish
        # "no movable surplus" from "surplus existed but uncertainty/pressure
        # made relocation unsafe".
        return TransferDecision(
            donor_index=donor,
            mismatch_reduction_lcb=mismatch_lcb,
            stranding_ucb=stranding_ucb,
            congestion_ucb=congestion_ucb,
            safe_path_interference_ucb=interference_ucb,
            margin=margin,
            reserve_floor=self.reserve_floor,
        )
