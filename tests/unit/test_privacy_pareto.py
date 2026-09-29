from __future__ import annotations

import json
from pathlib import Path

import pytest

from aidmesh.experiments.privacy_pareto import (
    _collusion_observations,
    _select_core_inputs,
)
from aidmesh.analysis.privacy import MetadataObservation, privacy_report


def _event(path: Path, *, run_id: str, method: str, seed: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "scenario_id": "CONF_CORE_W08_P24",
                "method": method,
                "seed": seed,
            }
        )
        + "\n",
        encoding="utf-8",
    )


def test_select_core_inputs_requires_balanced_method_seed_coverage(tmp_path: Path) -> None:
    for method in ("m1", "m2"):
        for seed in (1, 2):
            _event(
                tmp_path / method / str(seed) / "CONF_CORE_W08_P24" / "events.jsonl",
                run_id=f"{method}-{seed}",
                method=method,
                seed=seed,
            )
    selected = _select_core_inputs(
        tmp_path,
        scenario_id="CONF_CORE_W08_P24",
        expected_methods=("m1", "m2"),
        expected_seeds_per_method=2,
    )
    assert len(selected) == 4

    (tmp_path / "m2" / "2" / "CONF_CORE_W08_P24" / "events.jsonl").unlink()
    with pytest.raises(ValueError, match="coverage mismatch"):
        _select_core_inputs(
            tmp_path,
            scenario_id="CONF_CORE_W08_P24",
            expected_methods=("m1", "m2"),
            expected_seeds_per_method=2,
        )


def test_collusion_uses_actual_gateway_fields() -> None:
    ledger = [
        MetadataObservation(
            epoch=1,
            time_bucket=8,
            risk_cell="hidden",
            category="hidden",
            path="hidden",
            size_class="2048",
            vantage="ledger",
        )
    ]
    gateway = [
        MetadataObservation(
            epoch=1,
            time_bucket=8,
            risk_cell="hidden",
            category="hidden",
            path="fallback",
            size_class="1536",
            vantage="gateway",
        )
    ]
    collusion = _collusion_observations(ledger, gateway)
    assert collusion[0].path == "fallback"
    assert collusion[0].size_class == "1536|ledger:2048"
    assert privacy_report(collusion).observations == 1
