from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
from typing import Any

import pytest

VALIDATOR_PATH = Path("zk/ceremony/validate_transcript.py")
SPEC = importlib.util.spec_from_file_location("ceremony_transcript_validator", VALIDATOR_PATH)
assert SPEC is not None and SPEC.loader is not None
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)


def _write_artifact(root: Path, name: str, content: bytes) -> dict[str, Any]:
    path = root / name
    path.write_bytes(content)
    return {
        "path": name,
        "sha256": hashlib.sha256(content).hexdigest(),
        "bytes": len(content),
    }


def _valid_transcript(root: Path) -> dict[str, Any]:
    initial = _write_artifact(root, "phase2_0000.zkey", b"initial")
    contributions: list[dict[str, Any]] = []
    previous = initial
    for index in range(1, 6):
        name = "aidmesh_final_simulated.zkey" if index == 5 else f"phase2_{index:04d}.zkey"
        output = _write_artifact(root, name, f"zkey-{index}".encode())
        contributions.append(
            {
                "sequence": index,
                "identity": f"sim-node-{index}",
                "container_name": f"airproof-sim-node-{index}-run",
                "input": {"path": previous["path"], "sha256": previous["sha256"]},
                "output": output,
                "contribution_hash": f"{index:02x}" * 64,
                "entropy_source": "container OS CSPRNG",
                "entropy_logged": False,
                "entropy_persisted": False,
                "zkey_verified_against_r1cs_ptau": True,
            }
        )
        previous = output
    verification_key = _write_artifact(root, "verification_key.json", b"vk")
    verifier = _write_artifact(root, "AidMeshGroth16Verifier.sol", b"solidity")
    return {
        "schema_version": 1,
        "status": "completed",
        "single_operator_single_host_simulation": True,
        "production_trusted_setup": False,
        "independent_multiparty_trust": False,
        "toxic_waste_unknowable_claim": False,
        "contributor_container_count": 5,
        "five_contributor_containers_completed": True,
        "inputs": {
            "r1cs": {
                "path": "zk/build/aidmesh_voucher.r1cs",
                "sha256": VALIDATOR.EXPECTED_R1CS_SHA256,
                "bytes": 1,
            },
            "ptau": {
                "path": "zk/public/powersOfTau28_hez_final_15.ptau",
                "sha256": VALIDATOR.EXPECTED_PTAU_SHA256,
                "bytes": 1,
            },
            "package_lock": {
                "path": "package-lock.json",
                "sha256": "11" * 32,
                "bytes": 1,
            },
        },
        "toolchain": {
            "snarkjs_version": "snarkjs@0.7.6",
            "immutable_image_id": f"sha256:{'22' * 32}",
        },
        "initial_zkey": initial,
        "contributions": contributions,
        "commands": [{"argv": ["node", "/app/contribute.mjs"]}],
        "artifacts": {
            "final_zkey": previous,
            "verification_key": verification_key,
            "solidity_verifier": verifier,
        },
        "verification": {
            "initial_zkey_verified": True,
            "every_contribution_verified": True,
            "final_zkey_verified_against_actual_r1cs_ptau": True,
            "chain_hash_continuity_recorded": True,
        },
    }


def test_completed_transcript_and_artifact_hashes_validate(tmp_path: Path) -> None:
    VALIDATOR.validate_transcript(_valid_transcript(tmp_path), output_root=tmp_path)


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("single_operator_single_host_simulation", False),
        ("production_trusted_setup", True),
        ("independent_multiparty_trust", True),
        ("toxic_waste_unknowable_claim", True),
    ],
)
def test_truthfulness_flags_fail_closed(tmp_path: Path, field: str, bad_value: bool) -> None:
    transcript = _valid_transcript(tmp_path)
    transcript[field] = bad_value
    with pytest.raises(ValueError):
        VALIDATOR.validate_transcript(transcript, output_root=tmp_path)


def test_chain_hash_discontinuity_fails_closed(tmp_path: Path) -> None:
    transcript = _valid_transcript(tmp_path)
    transcript["contributions"][3]["input"]["sha256"] = "ff" * 32
    with pytest.raises(ValueError, match="continuity"):
        VALIDATOR.validate_transcript(transcript, output_root=tmp_path)


def test_entropy_argument_in_recorded_command_is_rejected(tmp_path: Path) -> None:
    transcript = _valid_transcript(tmp_path)
    transcript["commands"] = [{"argv": ["snarkjs", "zkey", "contribute", "-e=secret"]}]
    with pytest.raises(ValueError, match="entropy"):
        VALIDATOR.validate_transcript(transcript, output_root=tmp_path)
