from __future__ import annotations

from pathlib import Path

import pytest

from aidmesh.experiments.semantic_adversarial_campaign import (
    run_semantic_adversarial_campaign,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.evm
def test_semantic_adversarial_campaign_closes_every_registered_case() -> None:
    frame, manifest = run_semantic_adversarial_campaign(ROOT, trials=1)
    assert len(frame) == len(manifest["cases"])
    assert frame["security_holds"].all()
    controls = frame.loc[frame["negative_control_applicable"]]
    assert controls["negative_control_accepts_attack"].all()
