from pathlib import Path

from aidmesh.formal.refinement import run_escrow_refinement, run_witness_refinement


def test_generated_escrow_traces_refine_independent_oracle() -> None:
    summary, rows = run_escrow_refinement(seed=7, traces=8, steps=24)
    assert summary["all_states_refine_oracle"]
    assert summary["transitions"] == 192
    assert summary["accepted"] > 0
    assert summary["rejected"] > 0
    assert len(rows) == 192


def test_sqlite_witness_trace_survives_restarts_and_crash(tmp_path: Path) -> None:
    summary, rows = run_witness_refinement(tmp_path, seed=11, traces=2, steps=16)
    assert summary["all_states_refine_oracle"]
    assert summary["crash_before_commit_fail_closed"]
    assert len(rows) == 32
