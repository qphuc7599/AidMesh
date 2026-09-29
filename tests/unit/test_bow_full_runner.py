from __future__ import annotations

from pathlib import Path

import pytest

from aidmesh.experiments.bow_full_runner import _load_registry, _tasks


def test_full_registry_has_exact_primary_and_factorial_row_design() -> None:
    workspace = Path(__file__).resolve().parents[2]
    required = workspace / "external" / "sociopatterns" / "normalized_slots" / "invs13" / "contacts.csv"
    if not required.is_file():
        pytest.skip("external normalized traces are distributed separately")
    registry = _load_registry(
        workspace / "configs" / "trace_families" / "bow_full_20260822.yaml",
        workspace,
    )
    primary = _tasks("primary", registry, workspace / "tmp" / "p", factorial_chunk_cells=8)
    factorial = _tasks(
        "factorial", registry, workspace / "tmp" / "f", factorial_chunk_cells=8
    )
    assert len(primary) == 7 * 20
    assert len(factorial) == 5 * 4 * 16
    assert 7 * 20 * 4 * 3 * 8 == 13_440
    assert 5 * 4 * 128 * 6 == 15_360
