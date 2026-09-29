from __future__ import annotations

from pathlib import Path

import pytest

from aidmesh.experiments.confirmatory import (
    RegistryError,
    analyze_confirmatory_registry,
    load_confirmatory_registry,
    require_frozen,
    run_confirmatory_registry,
)


def _write_config(path: Path, *, phase: str = "confirmatory") -> None:
    path.write_text(
        f"""schema_version: 2.0.0
scenario_id: CONF_TEST
phase: {phase}
seed: 1
analysis_horizon_us: 7200000000
population:
  beneficiaries: 12
  sites: 3
  risk_cells: 1
  groups: 2
  categories: 2
quorum:
  K: 4
  q: 4
  registered_f: 1
  safe_label: true
  witness_availability: 0.8
  timeout_us: 1000000
network:
  mobility_family: sparse
  partition_us: 1800000000
  bandwidth_bps: 128000
  buffer_bytes: 4096
  loss: 0.05
  loss_model: iid
adversary:
  beneficiary_fraction: 0.0
  byzantine_witnesses: 0
  relay_drop_fraction: 0.0
fallback:
  enabled: true
  total_rights: 6
  placement: equal
  transfer_enabled: true
  window_us: 1800000000
routing:
  method: risk_priority
  copy_allowance: 2
  payload_bytes: 512
  padding_bucket_bytes: 512
  ttl_us: 3600000000
""",
        encoding="utf-8",
    )


def _write_registry(path: Path) -> None:
    path.write_text(
        """schema_version: 1.0.0
registry_id: CONF_TEST
freeze_status: DRAFT
claims:
  primary_questions: [RQ1]
  decision_rules: ["report all registered comparisons"]
approval:
  owner: PENDING_MANUAL_RESEARCH_DECISION
  date_utc: PENDING_MANUAL_RESEARCH_DECISION
  meaningful_effect_statement: PENDING_MANUAL_RESEARCH_DECISION
scenarios:
  - config: scenario.yaml
    seed_start: 100
    seed_count: 3
    methods: [online_only, aidmesh_transferable_rights]
analysis:
  confidence_level: 0.95
  bootstrap_resamples: 1000
  permutation_resamples: 1000
  multiplicity: holm
  comparisons:
    - id: RQ1_COMPLETION
      treatment: aidmesh_transferable_rights
      control: online_only
      metrics: [completion_rate]
      alternative: greater
""",
        encoding="utf-8",
    )


def test_confirmatory_registry_requires_explicit_freeze(tmp_path: Path) -> None:
    _write_config(tmp_path / "scenario.yaml")
    _write_registry(tmp_path / "registry.yaml")
    registry = load_confirmatory_registry(tmp_path / "registry.yaml", root=tmp_path)

    with pytest.raises(RegistryError, match="DRAFT"):
        require_frozen(registry)


def test_confirmatory_smoke_run_and_registered_analysis(tmp_path: Path) -> None:
    _write_config(tmp_path / "scenario.yaml")
    _write_registry(tmp_path / "registry.yaml")
    registry = load_confirmatory_registry(tmp_path / "registry.yaml", root=tmp_path)
    raw = tmp_path / "raw" / "confirmatory"
    outcome = run_confirmatory_registry(
        registry,
        output=raw,
        software_commit="test",
        allow_draft=True,
    )

    assert outcome["completed"] == 6
    assert (raw / "confirmatory_freeze_manifest.json").exists()
    assert (raw / "CHECKSUMS.txt").exists()

    assets = analyze_confirmatory_registry(
        registry,
        input_root=raw,
        output=tmp_path / "results",
        allow_draft=True,
    )

    assert assets["contrasts"].exists()
    assert "mean_difference" in assets["contrasts"].read_text(encoding="utf-8")


def test_confirmatory_registry_rejects_nonconfirmatory_scenario(tmp_path: Path) -> None:
    _write_config(tmp_path / "scenario.yaml", phase="development")
    _write_registry(tmp_path / "registry.yaml")

    with pytest.raises(RegistryError, match="phase=confirmatory"):
        load_confirmatory_registry(tmp_path / "registry.yaml", root=tmp_path)
