"""Causal regime detector with an explicit optimized-static protection budget."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class ConservativeTransferDecision:
    donor_index: int | None
    drift_score: float
    drift_threshold: float
    target_excess: float
    donor_slack: float
    delivery_probability: float
    lock_cost: float
    pressure_cost: float
    margin: float
    max_open_locks: int

    @property
    def approved(self) -> bool:
        return self.donor_index is not None and self.margin > 0


class ConservativeRegimeController:
    """Switch from optimized static only after a causal drift certificate.

    The detector compares observed site-demand shares with the immutable static
    prior.  It never sees suffix outcomes or policy completion.  At most a small,
    registered number of rights may be locked concurrently, giving an auditable
    sample-path protection budget around the static allocation.  Reclaimability
    discounts only the duration of a failed lock; it never turns a failed transfer
    into a successful one.
    """

    def __init__(
        self,
        *,
        baseline: tuple[int, ...],
        minimum_samples: int = 12,
        false_alarm_scale: float = 1.75,
        protection_fraction: float = 0.10,
    ) -> None:
        if not baseline or any(value < 0 for value in baseline) or sum(baseline) <= 0:
            raise ValueError("baseline must contain a positive conserved allocation")
        if minimum_samples <= 0 or false_alarm_scale <= 0:
            raise ValueError("detector controls must be positive")
        if not 0.0 < protection_fraction <= 1.0:
            raise ValueError("protection_fraction must be in (0, 1]")
        self.baseline = baseline
        self.budget = sum(baseline)
        self.minimum_samples = minimum_samples
        self.false_alarm_scale = false_alarm_scale
        self.protection_fraction = protection_fraction
        self.prior = tuple(value / self.budget for value in baseline)
        self.max_open_locks = max(1, math.floor(protection_fraction * self.budget))

    def decide(
        self,
        *,
        target_index: int,
        balances: tuple[int, ...],
        observed_demand: tuple[int, ...],
        path_reliability: tuple[float, ...],
        queue_pressure: float,
        safe_path_pressure: float,
        open_locks: int,
        reclaimable: bool,
        reclaim_delay_fraction: float,
    ) -> ConservativeTransferDecision:
        sites = len(self.baseline)
        if not 0 <= target_index < sites:
            raise ValueError("invalid target index")
        vectors = (balances, observed_demand, path_reliability)
        if not all(len(values) == sites for values in vectors):
            raise ValueError("controller vectors must match the baseline")
        if any(value < 0 for value in (*balances, *observed_demand)):
            raise ValueError("balances and demand must be nonnegative")
        if any(not 0.0 <= value <= 1.0 for value in path_reliability):
            raise ValueError("path reliability must be in [0, 1]")
        if not 0.0 <= queue_pressure <= 1.0 or not 0.0 <= safe_path_pressure <= 1.0:
            raise ValueError("pressures must be in [0, 1]")
        if open_locks < 0 or not 0.0 <= reclaim_delay_fraction <= 1.0:
            raise ValueError("invalid lock state")

        samples = sum(observed_demand)
        observed = (
            tuple(value / samples for value in observed_demand)
            if samples
            else self.prior
        )
        drift_score = 0.5 * sum(
            abs(left - right) for left, right in zip(observed, self.prior, strict=True)
        )
        drift_threshold = min(
            0.45,
            self.false_alarm_scale / math.sqrt(max(1, samples)),
        )
        target_excess = max(0.0, observed[target_index] - self.prior[target_index])

        candidates = []
        for index, balance in enumerate(balances):
            if index == target_index or balance <= 0:
                continue
            # No site can donate more than the registered protection budget below
            # its optimized-static baseline while another lock is unresolved.
            protected_floor = max(0, self.baseline[index] - self.max_open_locks)
            donor_slack = max(0.0, self.prior[index] - observed[index])
            if balance > protected_floor and donor_slack > 0:
                candidates.append((index, donor_slack))

        donor_index = None
        donor_slack = 0.0
        if candidates and open_locks < self.max_open_locks:
            donor_index, donor_slack = min(
                candidates,
                key=lambda row: (
                    -(row[1] * path_reliability[row[0]]),
                    row[0],
                ),
            )
        delivery_probability = (
            path_reliability[donor_index] if donor_index is not None else 0.0
        )

        drift_certified = samples >= self.minimum_samples and drift_score > drift_threshold
        mismatch_signal = min(1.0, samples * min(target_excess, donor_slack))
        expected_gain = delivery_probability * mismatch_signal if drift_certified else 0.0
        failure_probability = 1.0 - delivery_probability
        lock_cost = failure_probability * (
            reclaim_delay_fraction if reclaimable else 1.0
        )
        pressure_cost = 0.10 * (queue_pressure + safe_path_pressure)
        margin = expected_gain - lock_cost - pressure_cost
        if open_locks >= self.max_open_locks:
            margin = min(margin, 0.0)

        return ConservativeTransferDecision(
            donor_index=donor_index,
            drift_score=drift_score,
            drift_threshold=drift_threshold,
            target_excess=target_excess,
            donor_slack=donor_slack,
            delivery_probability=delivery_probability,
            lock_cost=lock_cost,
            pressure_cost=pressure_cost,
            margin=margin,
            max_open_locks=self.max_open_locks,
        )
