from aidmesh.experiments.fault_campaign import run_fault_campaign


def test_native_fault_campaign_is_fail_closed_and_conserved() -> None:
    results = run_fault_campaign()
    assert {item.fault_id for item in results} == {
        "F1_CRASH_BEFORE_WITNESS_COMMIT",
        "F2_RESTART_EQUIVALENT_CONFLICT",
        "F3_STALE_RISK_STATE",
        "F4_EPOCH_HANDOFF",
        "F5_TRANSFER_ACCEPT_BEFORE_DEBIT",
        "F6_TRANSFER_REPLAY",
        "F7_EXPIRED_RIGHT_RESURRECTION",
    }
    assert all(item.safety_preserved for item in results)
