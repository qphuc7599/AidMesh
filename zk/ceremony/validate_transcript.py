"""Validate a completed single-host Docker ceremony-simulation transcript."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
CONTRIBUTION_HASH_RE = re.compile(r"^[0-9a-f]{128}$")
EXPECTED_IDENTITIES = [f"sim-node-{index}" for index in range(1, 6)]
EXPECTED_R1CS_SHA256 = "a3417107f3e40f2ba9e62a4948e5f399569b4316d76fa99c8bcfc0e0cbbc6215"
EXPECTED_PTAU_SHA256 = "3ef2ecc5b75d687048cf2d59195119b42fb07c5af639c5f283d84bfa69829e7f"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _validate_relative_path(value: Any, label: str) -> str:
    _require(isinstance(value, str) and value, f"{label} must be a non-empty string")
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    _require(not path.is_absolute(), f"{label} must not be absolute")
    _require(
        ":" not in normalized and ".." not in path.parts, f"{label} is not repository-relative"
    )
    return normalized


def _validate_artifact(
    artifact: Any,
    label: str,
    *,
    output_root: Path | None = None,
) -> None:
    _require(isinstance(artifact, dict), f"{label} must be an object")
    relative = _validate_relative_path(artifact.get("path"), f"{label}.path")
    digest = artifact.get("sha256")
    _require(
        isinstance(digest, str) and SHA256_RE.fullmatch(digest) is not None,
        f"{label}.sha256 is invalid",
    )
    _require(
        isinstance(artifact.get("bytes"), int) and artifact["bytes"] > 0,
        f"{label}.bytes is invalid",
    )
    if output_root is not None:
        path = output_root / relative
        _require(path.is_file(), f"{label} file is missing: {relative}")
        _require(path.stat().st_size == artifact["bytes"], f"{label} byte count mismatch")
        _require(sha256_file(path) == digest, f"{label} SHA-256 mismatch")


def validate_transcript(data: Any, *, output_root: Path | None = None) -> None:
    _require(isinstance(data, dict), "transcript must be a JSON object")
    _require(data.get("schema_version") == 1, "unsupported schema_version")
    _require(data.get("status") == "completed", "transcript is not completed")
    _require(
        data.get("single_operator_single_host_simulation") is True, "simulation flag must be true"
    )
    _require(data.get("production_trusted_setup") is False, "production flag must be false")
    _require(
        data.get("independent_multiparty_trust") is False, "independent-trust flag must be false"
    )
    _require(
        data.get("toxic_waste_unknowable_claim") is False, "toxic-waste claim flag must be false"
    )
    _require(data.get("contributor_container_count") == 5, "container count must be five")
    _require(
        data.get("five_contributor_containers_completed") is True,
        "five containers must be complete",
    )

    inputs = data.get("inputs")
    _require(isinstance(inputs, dict), "inputs must be an object")
    _validate_artifact(inputs.get("r1cs"), "inputs.r1cs")
    _validate_artifact(inputs.get("ptau"), "inputs.ptau")
    _validate_artifact(inputs.get("package_lock"), "inputs.package_lock")
    _require(inputs["r1cs"]["sha256"] == EXPECTED_R1CS_SHA256, "unexpected frozen R1CS hash")
    _require(inputs["ptau"]["sha256"] == EXPECTED_PTAU_SHA256, "unexpected frozen PTAU hash")

    toolchain = data.get("toolchain")
    _require(isinstance(toolchain, dict), "toolchain must be an object")
    _require("0.7.6" in str(toolchain.get("snarkjs_version", "")), "snarkjs 0.7.6 was not recorded")
    _require(
        re.fullmatch(r"sha256:[0-9a-f]{64}", str(toolchain.get("immutable_image_id", "")))
        is not None,
        "immutable image ID is invalid",
    )

    initial = data.get("initial_zkey")
    _validate_artifact(initial, "initial_zkey", output_root=output_root)
    contributions = data.get("contributions")
    _require(
        isinstance(contributions, list) and len(contributions) == 5,
        "exactly five contributions required",
    )
    _require(
        [item.get("identity") for item in contributions] == EXPECTED_IDENTITIES,
        "contributor identities/order invalid",
    )

    expected_input_hash = initial["sha256"]
    seen_output_hashes: set[str] = set()
    for index, contribution in enumerate(contributions, start=1):
        label = f"contributions[{index - 1}]"
        _require(contribution.get("sequence") == index, f"{label}.sequence is invalid")
        _require(
            contribution.get("zkey_verified_against_r1cs_ptau") is True, f"{label} was not verified"
        )
        _require(contribution.get("entropy_logged") is False, f"{label} says entropy was logged")
        _require(
            contribution.get("entropy_persisted") is False, f"{label} says entropy was persisted"
        )
        input_artifact = contribution.get("input")
        _require(isinstance(input_artifact, dict), f"{label}.input must be an object")
        _validate_relative_path(input_artifact.get("path"), f"{label}.input.path")
        _require(
            input_artifact.get("sha256") == expected_input_hash, f"{label} breaks hash continuity"
        )
        output_artifact = contribution.get("output")
        _validate_artifact(output_artifact, f"{label}.output", output_root=output_root)
        output_hash = output_artifact["sha256"]
        _require(
            output_hash not in seen_output_hashes, f"{label} duplicates an earlier output hash"
        )
        seen_output_hashes.add(output_hash)
        contribution_hash = contribution.get("contribution_hash")
        _require(
            isinstance(contribution_hash, str)
            and CONTRIBUTION_HASH_RE.fullmatch(contribution_hash) is not None,
            f"{label}.contribution_hash is invalid",
        )
        expected_input_hash = output_hash

    artifacts = data.get("artifacts")
    _require(isinstance(artifacts, dict), "artifacts must be an object")
    for name in ("final_zkey", "verification_key", "solidity_verifier"):
        _validate_artifact(artifacts.get(name), f"artifacts.{name}", output_root=output_root)
    _require(
        artifacts["final_zkey"]["sha256"] == expected_input_hash, "final zkey is not the chain tip"
    )

    verification = data.get("verification")
    _require(isinstance(verification, dict), "verification must be an object")
    for flag in (
        "initial_zkey_verified",
        "every_contribution_verified",
        "final_zkey_verified_against_actual_r1cs_ptau",
        "chain_hash_continuity_recorded",
    ):
        _require(verification.get(flag) is True, f"verification.{flag} must be true")

    serialized_commands = json.dumps(data.get("commands", []), sort_keys=True).lower()
    _require("-e=" not in serialized_commands, "commands must not contain entropy arguments")
    _require(
        "aidmesh_ceremony_entropy" not in serialized_commands,
        "commands expose a secret environment name",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--transcript", required=True, type=Path)
    parser.add_argument("--output-root", type=Path)
    args = parser.parse_args()
    data = json.loads(args.transcript.read_text(encoding="utf-8-sig"))
    validate_transcript(data, output_root=args.output_root)
    print("ceremony transcript validation: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
