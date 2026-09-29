from __future__ import annotations

from pathlib import Path

import yaml

from aidmesh.experiments.catr_full_runner import _load_registry, _tasks


def test_catr_runner_registers_expected_shards(tmp_path: Path) -> None:
    trace = tmp_path / "trace"
    trace.mkdir()
    (trace / "contacts.csv").write_text(
        "start_us,end_us,node_a,node_b,bytes_per_second\n0,1,a,b,1\n",
        encoding="utf-8",
    )
    registry_path = tmp_path / "registry.yaml"
    family_manifest = tmp_path / "family_manifest.json"
    family_manifest.write_text(
        '{"eligible_contexts":["trace"],"upstream_commit":"abc"}\n',
        encoding="utf-8",
    )
    registry_path.write_text(
        yaml.safe_dump(
            {
                "primary_seeds": [1, 2],
                "factorial_seeds": [3],
                "cap_fractions": [0.15, 0.60],
                "quorum_availabilities": [0.25, 0.90],
                "calibration_fraction": 0.4,
                "conservative_protection_fraction": 0.1,
                "family_manifest": "family_manifest.json",
                "upstream_commit": "abc",
                "primary_policies": [
                    "quorum_only",
                    "equal_static",
                    "optimized_static",
                    "robust_static",
                    "periodic_sync",
                    "adatransfer",
                    "reclaimable_transfer",
                    "catr",
                ],
                "factorial_policies": [
                    "optimized_static",
                    "robust_static",
                    "periodic_sync",
                    "adatransfer",
                    "reclaimable_transfer",
                    "catr",
                ],
                "factor_order": [
                    "demand_drift",
                    "cap_tightness",
                    "witness_availability",
                    "transfer_reliability",
                    "contact_predictability",
                    "buffer_provisioning",
                    "cross_class_traffic",
                    "recovery_timing",
                ],
                "contexts": [
                    {
                        "family": "f",
                        "context": "trace",
                        "profile": "p",
                        "path": "trace",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    registry = _load_registry(registry_path, tmp_path)
    assert len(_tasks("primary", registry, tmp_path / "out", factorial_chunk_cells=16)) == 2
    assert len(_tasks("factorial", registry, tmp_path / "out", factorial_chunk_cells=16)) == 16
