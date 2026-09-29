from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from aidmesh.common.canonical import stable_hash
from aidmesh.experiments.e2e_campaign import (
    LocalEVMHarness,
    ProofArtifact,
    _validated_cached_run,
    load_e2e_registry,
    run_e2e_fault_controls,
    verify_e2e_checksums,
    write_e2e_checksums,
)

ROOT = Path(__file__).resolve().parents[2]


def test_registered_local_e2e_matrix_is_frozen() -> None:
    registry = load_e2e_registry(ROOT / "configs" / "e2e" / "registry.yaml")
    assert registry.contexts == ("invs13", "lh10", "sfhh")
    assert registry.seeds == tuple(range(7301, 7309))
    assert registry.methods == ("static_rights", "transferable_rights")
    assert registry.witness_regimes == ("quorum", "fallback")
    assert registry.cap_fractions == (0.10, 0.20, 0.40, 0.80)
    assert registry.voucher_sample_per_context == 12
    assert registry.sampling_strategy == "stable-hash-v1"


def test_input_generator_builds_shared_roots_and_distinct_nullifiers(tmp_path: Path) -> None:
    spec = {
        "registryId": "test-e2e",
        "context": "toy",
        "seed": 1,
        "campaignId": "1001",
        "epoch": 1,
        "riskCell": 7,
        "category": 3,
        "value": 1,
        "fallbackWindow": 6,
        "chainId": "131277322940537",
        "gatewayAddress": "235632950691130629397570742864357571059148313476",
        "settlementAddress": "1267616807065886026890900485244985828061334074567",
        "headerHash": "5001",
        "policyHash": "5002",
        "committeeHash": "5003",
        "modes": ["quorum", "fallback"],
        "vouchers": [{"key": "a"}, {"key": "b"}],
    }
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    output = tmp_path / "inputs"
    subprocess.run(
        ["node", str(ROOT / "scripts" / "generate-e2e-inputs.mjs"), spec_path, output],
        cwd=ROOT,
        check=True,
    )
    manifest = json.loads((output / "input_manifest.json").read_text(encoding="utf-8"))
    assert manifest["vouchers"] == 2
    assert len(manifest["vectors"]) == 4
    assert len({row["credentialRoot"] for row in manifest["vectors"]}) == 1
    assert len({row["entitlementRoot"] for row in manifest["vectors"]}) == 1
    by_key = {}
    for row in manifest["vectors"]:
        by_key.setdefault(row["key"], row["nullifier"])
        assert by_key[row["key"]] == row["nullifier"]
    assert by_key["a"] != by_key["b"]


def test_checksum_manifest_detects_mutation_and_excludes_transient_logs(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "artifact.json"
    artifact.write_text('{"ok":true}\n', encoding="utf-8")
    (tmp_path / "campaign.stdout.log").write_text("mutable", encoding="utf-8")
    write_e2e_checksums(tmp_path)
    assert verify_e2e_checksums(tmp_path)["status"] == "valid"
    artifact.write_text('{"ok":false}\n', encoding="utf-8")
    result = verify_e2e_checksums(tmp_path)
    assert result["status"] == "invalid"
    assert result["failures"][0]["path"] == "artifact.json"


def test_cached_run_requires_matching_raw_event_log(tmp_path: Path) -> None:
    events = [{"run_id": "run-1", "sequence": 0, "outcome": "settled"}]
    events_path = tmp_path / "events.jsonl"
    events_path.write_text(json.dumps(events[0]) + "\n", encoding="utf-8")
    summary_path = tmp_path / "summary.json"
    summary = {
        "status": "complete",
        "run_id": "run-1",
        "workload_fingerprint": "workload",
        "implementation_hash": "implementation",
        "event_rows": 1,
        "event_checksum": stable_hash(events, domain="aidmesh/e2e/events/v1"),
    }
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    assert _validated_cached_run(
        summary_path,
        events_path,
        run_id="run-1",
        workload_fingerprint="workload",
        implementation_hash="implementation",
    ) == summary
    events_path.write_text(json.dumps({**events[0], "outcome": "conflict"}), encoding="utf-8")
    with pytest.raises(AssertionError, match="cached run integrity failure"):
        _validated_cached_run(
            summary_path,
            events_path,
            run_id="run-1",
            workload_fingerprint="workload",
            implementation_hash="implementation",
        )


@pytest.mark.evm
def test_gateway_negative_controls_against_local_bytecode(tmp_path: Path) -> None:
    calldata = ROOT / "zk" / "build" / "calldata_p1_v2_tagged.txt"
    public = ROOT / "zk" / "build" / "public_p1_v2_tagged.json"
    proof = ROOT / "zk" / "build" / "proof_p1_v2_tagged.json"
    if not all(path.exists() for path in (calldata, public, proof)):
        pytest.skip("local Groth16 benchmark artifacts are unavailable")
    public_values = json.loads(public.read_text(encoding="utf-8"))
    artifact = ProofArtifact(
        key="fixture",
        mode="quorum",
        input_path=ROOT / "zk" / "inputs" / "valid_quorum.json",
        proof_path=proof,
        public_path=public,
        calldata_path=calldata,
        nullifier=str(public_values[7]),
        fallback_tag=str(public_values[10]),
        proof_ms=0.0,
        proof_sha256="fixture",
        public_sha256="fixture",
    )
    summary = run_e2e_fault_controls(LocalEVMHarness(ROOT), artifact, tmp_path)
    assert summary["all_passed"]
    assert summary["passed"] == summary["cases"] == 17
    assert summary["cases_by_name"]["authorization_evidence_rebinding"]
    assert summary["cases_by_name"]["zero_authorization_evidence"]
