from aidmesh.policy.conservative_reclaimable import ConservativeRegimeController


def test_stable_observations_keep_static_baseline_closed() -> None:
    controller = ConservativeRegimeController(baseline=(11, 5, 3, 1))
    decision = controller.decide(
        target_index=1,
        balances=(11, 5, 3, 1),
        observed_demand=(55, 25, 15, 5),
        path_reliability=(0.9, 1.0, 0.8, 0.7),
        queue_pressure=0.0,
        safe_path_pressure=0.0,
        open_locks=0,
        reclaimable=True,
        reclaim_delay_fraction=0.1,
    )
    assert decision.drift_score == 0
    assert not decision.approved


def test_certified_shift_opens_reclaimable_surplus() -> None:
    controller = ConservativeRegimeController(baseline=(11, 5, 3, 1))
    decision = controller.decide(
        target_index=2,
        balances=(11, 5, 3, 1),
        observed_demand=(10, 10, 70, 10),
        path_reliability=(0.95, 0.8, 1.0, 0.7),
        queue_pressure=0.0,
        safe_path_pressure=0.0,
        open_locks=0,
        reclaimable=True,
        reclaim_delay_fraction=0.1,
    )
    assert decision.drift_score > decision.drift_threshold
    assert decision.donor_index == 0
    assert decision.approved


def test_open_lock_budget_blocks_additional_exposure() -> None:
    controller = ConservativeRegimeController(
        baseline=(11, 5, 3, 1), protection_fraction=0.05
    )
    decision = controller.decide(
        target_index=2,
        balances=(10, 5, 3, 1),
        observed_demand=(10, 10, 70, 10),
        path_reliability=(1.0, 1.0, 1.0, 1.0),
        queue_pressure=0.0,
        safe_path_pressure=0.0,
        open_locks=1,
        reclaimable=True,
        reclaim_delay_fraction=0.1,
    )
    assert decision.max_open_locks == 1
    assert not decision.approved
