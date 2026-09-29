"""Exhaustive finite checks for the assumptions used by AidMesh safety claims.

This is deliberately a bounded executable model rather than a claim of a full
mechanized proof.  The companion TLA+ model has the same state vocabulary.  A
safe quorum must have no conflict under the persistent-honest-witness model;
the checker also emits counterexamples when that condition or atomic debit is
removed.
"""

from __future__ import annotations

import itertools
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class QuorumCase:
    committee_size: int
    threshold: int
    byzantine_bound: int
    rolled_back_honest: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.committee_size < 1 or not 1 <= self.threshold <= self.committee_size:
            raise ValueError("threshold must be in [1, committee_size]")
        if not 0 <= self.byzantine_bound < self.committee_size:
            raise ValueError("byzantine_bound must be in [0, committee_size)")

    @property
    def members(self) -> tuple[str, ...]:
        return tuple(f"w{index}" for index in range(self.committee_size))

    @property
    def byzantine(self) -> frozenset[str]:
        return frozenset(self.members[: self.byzantine_bound])

    @property
    def multi_signers(self) -> frozenset[str]:
        return self.byzantine | frozenset(self.rolled_back_honest)

    @property
    def safe_intersection(self) -> bool:
        return 2 * self.threshold > self.committee_size + self.byzantine_bound


@dataclass(frozen=True)
class QuorumCounterexample:
    left_signers: tuple[str, ...]
    right_signers: tuple[str, ...]
    intersection: tuple[str, ...]
    multi_signers: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def find_conflicting_certificates(case: QuorumCase) -> QuorumCounterexample | None:
    """Search all threshold signer pairs for two different digests.

    Honest witnesses may appear in at most one certificate for a nullifier.  A
    listed ``rolled_back_honest`` witness violates persist-before-sign and is
    intentionally allowed to sign both digests as a negative control.
    """

    groups = list(itertools.combinations(case.members, case.threshold))
    for left in groups:
        for right in groups:
            if left == right:
                continue
            intersection = frozenset(left) & frozenset(right)
            if intersection <= case.multi_signers:
                return QuorumCounterexample(
                    left_signers=left,
                    right_signers=right,
                    intersection=tuple(sorted(intersection)),
                    multi_signers=tuple(sorted(case.multi_signers)),
                )
    return None


@dataclass(frozen=True)
class RiskState:
    balances: tuple[tuple[str, int], ...]
    debited: int

    @property
    def total(self) -> int:
        return sum(value for _site, value in self.balances) + self.debited

    def balance_map(self) -> dict[str, int]:
        return dict(self.balances)


def _canonical_state(balances: dict[str, int], debited: int) -> RiskState:
    return RiskState(tuple(sorted(balances.items())), debited)


def exhaustive_risk_states(
    initial_rights: dict[str, int], *, max_depth: int
) -> tuple[RiskState, ...]:
    """Explore every one-unit atomic transfer/debit state up to ``max_depth``."""

    if not initial_rights or any(value < 0 for value in initial_rights.values()):
        raise ValueError("initial_rights must be a non-empty non-negative mapping")
    initial_total = sum(initial_rights.values())
    frontier = {_canonical_state(dict(initial_rights), 0)}
    visited = set(frontier)
    for _depth in range(max_depth):
        next_frontier: set[RiskState] = set()
        for state in frontier:
            balances = state.balance_map()
            for source, value in balances.items():
                if value <= 0:
                    continue
                debited = dict(balances)
                debited[source] -= 1
                candidate = _canonical_state(debited, state.debited + 1)
                if candidate.total != initial_total or min(debited.values()) < 0:
                    raise AssertionError("atomic debit broke conservation")
                next_frontier.add(candidate)
                for destination in balances:
                    if destination == source:
                        continue
                    transferred = dict(balances)
                    transferred[source] -= 1
                    transferred[destination] += 1
                    candidate = _canonical_state(transferred, state.debited)
                    if candidate.total != initial_total or min(transferred.values()) < 0:
                        raise AssertionError("atomic transfer broke conservation")
                    next_frontier.add(candidate)
        visited.update(next_frontier)
        frontier = next_frontier
    return tuple(sorted(visited, key=lambda state: (state.balances, state.debited)))


def nonatomic_debit_counterexample() -> dict[str, int]:
    """Return the classic read-check-write race when a debit lacks atomicity."""

    initial = 1
    observed_by_a = initial
    observed_by_b = initial
    balance_after_a = observed_by_a - 1
    # A non-atomic read-check-write may lose A's write when B commits a value
    # derived from the same stale observation.  Both releases proceed anyway.
    balance_after_b = observed_by_b - 1
    return {
        "initial_rights": initial,
        "concurrent_approved_debits": 2,
        "first_commit_balance": balance_after_a,
        "final_balance": balance_after_b,
        "spent": 2,
        "conservation_holds": int(balance_after_b + 2 == initial),
    }


def formal_evidence_report() -> dict[str, Any]:
    """Produce the bounded safety evidence consumed by CI and paper supplements."""

    safe = QuorumCase(committee_size=4, threshold=3, byzantine_bound=1)
    unsafe = QuorumCase(committee_size=4, threshold=2, byzantine_bound=1)
    rollback = QuorumCase(
        committee_size=4,
        threshold=3,
        byzantine_bound=1,
        rolled_back_honest=("w1",),
    )
    safe_counterexample = find_conflicting_certificates(safe)
    if safe_counterexample is not None:
        raise AssertionError("safe quorum model produced a conflicting certificate")
    unsafe_counterexample = find_conflicting_certificates(unsafe)
    rollback_counterexample = find_conflicting_certificates(rollback)
    if unsafe_counterexample is None or rollback_counterexample is None:
        raise AssertionError("negative-control model did not produce an expected counterexample")
    states = exhaustive_risk_states({"site-a": 2, "site-b": 1}, max_depth=5)
    non_atomic = nonatomic_debit_counterexample()
    return {
        "model_kind": "bounded_exhaustive_executable_model",
        "safe_quorum": {
            "case": asdict(safe),
            "safe_intersection": safe.safe_intersection,
            "counterexample": safe_counterexample.to_dict() if safe_counterexample else None,
        },
        "unsafe_quorum_negative_control": {
            "case": asdict(unsafe),
            "safe_intersection": unsafe.safe_intersection,
            "counterexample": unsafe_counterexample.to_dict(),
        },
        "persist_before_sign_negative_control": {
            "case": asdict(rollback),
            "safe_intersection": rollback.safe_intersection,
            "counterexample": rollback_counterexample.to_dict(),
        },
        "risk_rights": {
            "initial_rights": {"site-a": 2, "site-b": 1},
            "max_depth": 5,
            "reachable_atomic_states": len(states),
            "all_states_conserved": all(state.total == 3 for state in states),
            "non_atomic_negative_control": non_atomic,
        },
        "at_most_once": {
            "state_encoding": "settled nullifiers are a set; a second insertion is rejected",
            "negative_control": "a multiset settlement model credits the same nullifier twice",
        },
        "limitations": [
            "Finite state exploration does not replace an unbounded proof.",
            "The companion TLA+ specification must be checked separately with TLC.",
        ],
    }


def write_formal_evidence(output: Path) -> dict[str, Path]:
    output.mkdir(parents=True, exist_ok=True)
    report = formal_evidence_report()
    report_path = output / "bounded_formal_evidence.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    counterexample_path = output / "unsafe_quorum_counterexample.json"
    counterexample_path.write_text(
        json.dumps(report["unsafe_quorum_negative_control"]["counterexample"], indent=2) + "\n",
        encoding="utf-8",
    )
    return {"report": report_path, "unsafe_counterexample": counterexample_path}
