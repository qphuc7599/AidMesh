from __future__ import annotations

from dataclasses import replace

from aidmesh.experiments.catr_power_sweep import expand_requests
from aidmesh.experiments.temporal_frontier import HeldoutRequest
from aidmesh.simulator.dtn import BundleSpec


def test_expand_requests_preserves_timing_and_makes_ids_unique() -> None:
    request = HeldoutRequest(
        BundleSpec("b", "c", "s", 10, 20, 30, 1.0),
        home_site="h",
        nullifier="n",
    )
    expanded = expand_requests((request,), 4)
    assert len(expanded) == 4
    assert len({row.bundle.bundle_id for row in expanded}) == 4
    assert len({row.nullifier for row in expanded}) == 4
    assert {row.bundle.created_us for row in expanded} == {10}
    assert all(replace(row.bundle, bundle_id="b") == request.bundle for row in expanded)
