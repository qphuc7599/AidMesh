from __future__ import annotations

from aidmesh.formal.safety import (
    QuorumCase,
    exhaustive_risk_states,
    find_conflicting_certificates,
    formal_evidence_report,
    nonatomic_debit_counterexample,
)


def test_safe_quorum_has_no_counterexample_under_persistent_honest_witnesses() -> None:
    case = QuorumCase(committee_size=4, threshold=3, byzantine_bound=1)

    assert case.safe_intersection
    assert find_conflicting_certificates(case) is None


def test_unsafe_and_rollback_negative_controls_produce_counterexamples() -> None:
    unsafe = QuorumCase(committee_size=4, threshold=2, byzantine_bound=1)
    rollback = QuorumCase(
        committee_size=4,
        threshold=3,
        byzantine_bound=1,
        rolled_back_honest=("w1",),
    )

    assert find_conflicting_certificates(unsafe) is not None
    assert find_conflicting_certificates(rollback) is not None
    assert nonatomic_debit_counterexample()["conservation_holds"] == 0


def test_atomic_risk_state_space_preserves_rights() -> None:
    states = exhaustive_risk_states({"site-a": 2, "site-b": 1}, max_depth=5)

    assert states
    assert all(state.total == 3 for state in states)
    report = formal_evidence_report()
    assert report["safe_quorum"]["counterexample"] is None
    assert report["risk_rights"]["all_states_conserved"]
