"""Benchmark and mutation checks for the P1 Groth16 voucher circuit."""

from __future__ import annotations

import hashlib
import json
import statistics
import subprocess
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from aidmesh.crypto.groth16 import (
    PUBLIC_INPUT_ORDER,
    default_profile,
    load_public_signals,
    parse_solidity_calldata,
    snarkjs_command,
    verify_with_snarkjs,
    write_public_signals,
)
from aidmesh.experiments.solidity import solc_command


def _sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _compile_verifier(root: Path, verifier: Path | None = None) -> tuple[list[dict[str, Any]], str]:
    verifier = verifier or root / "contracts" / "src" / "AidMeshGroth16Verifier.sol"
    completed = subprocess.run(
        [
            solc_command(root),
            "--optimize",
            "--evm-version",
            "prague",
            "--combined-json",
            "abi,bin",
            str(verifier),
        ],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    output = json.loads(completed.stdout)
    artifact = next(iter(output["contracts"].values()))
    abi = artifact["abi"] if isinstance(artifact["abi"], list) else json.loads(artifact["abi"])
    return abi, artifact["bin"]


def _evm_verifier_gas(
    root: Path,
    calldata_path: Path,
    verifier: Path | None = None,
) -> tuple[int, bool, int, int, int]:
    from web3 import Web3
    from web3.providers.eth_tester import EthereumTesterProvider

    abi, bytecode = _compile_verifier(root, verifier)
    web3 = Web3(EthereumTesterProvider())
    account = web3.eth.accounts[0]
    factory = web3.eth.contract(abi=abi, bytecode=bytecode)
    deployment_hash = factory.constructor().transact({"from": account})
    address = web3.eth.wait_for_transaction_receipt(deployment_hash).contractAddress
    contract = web3.eth.contract(address=address, abi=abi)
    proof_args = parse_solidity_calldata(calldata_path)
    encoded_call = contract.encode_abi("verifyProof", args=list(proof_args))
    valid_call = bool(contract.functions.verifyProof(*proof_args).call())
    rejected = 0
    for index in range(len(PUBLIC_INPUT_ORDER)):
        mutated_public = list(proof_args[3])
        mutated_public[index] += 1
        if _evm_rejects(
            contract,
            proof_args[0],
            proof_args[1],
            proof_args[2],
            mutated_public,
        ):
            rejected += 1
    malformed_a = list(proof_args[0])
    malformed_a[0] += 1
    if _evm_rejects(
        contract,
        malformed_a,
        proof_args[1],
        proof_args[2],
        proof_args[3],
    ):
        rejected += 1
    transaction = contract.functions.verifyProof(*proof_args).transact({"from": account})
    receipt = web3.eth.wait_for_transaction_receipt(transaction)
    return (
        int(receipt.gasUsed),
        valid_call,
        rejected,
        len(PUBLIC_INPUT_ORDER) + 1,
        (len(encoded_call) - 2) // 2,
    )


def _evm_rejects(
    contract: Any,
    proof_a: list[int],
    proof_b: list[list[int]],
    proof_c: list[int],
    public: list[int],
) -> bool:
    try:
        return not bool(contract.functions.verifyProof(proof_a, proof_b, proof_c, public).call())
    except Exception:  # pragma: no cover - backend-specific invalid-point behavior
        return True


def _invalid_witness_cases(valid_input: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Create relation-breaking private mutations without changing public inputs."""

    cases: dict[str, dict[str, Any]] = {}
    secret = dict(valid_input)
    secret["secret"] = str(int(secret["secret"]) + 1)
    cases["secret"] = secret
    quota_boundary = dict(valid_input)
    quota_boundary["voucherIndex"] = str(quota_boundary["quota"])
    cases["voucher_index_equals_quota"] = quota_boundary
    credential_path = dict(valid_input)
    credential_path["credentialPathElements"] = list(credential_path["credentialPathElements"])
    credential_path["credentialPathElements"][0] = "1"
    cases["credential_merkle_element"] = credential_path
    entitlement_path = dict(valid_input)
    entitlement_path["entitlementPathIndices"] = list(entitlement_path["entitlementPathIndices"])
    entitlement_path["entitlementPathIndices"][0] = 1
    cases["entitlement_merkle_direction"] = entitlement_path
    return cases


def _private_witness_mutation_rows(
    *,
    root: Path,
    build: Path,
    input_path: Path,
    wasm_path: Path,
    zkey_path: Path,
    profile: str,
) -> list[dict[str, Any]]:
    valid_input = json.loads(input_path.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    for name, payload in _invalid_witness_cases(valid_input).items():
        mutated_input = build / f"bench_invalid_private_{name}.json"
        proof_path = build / f"bench_invalid_private_{name}_proof.json"
        public_path = build / f"bench_invalid_private_{name}_public.json"
        mutated_input.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        completed = subprocess.run(
            [
                snarkjs_command(),
                "snarkjs",
                "groth16",
                "fullprove",
                str(mutated_input),
                str(wasm_path),
                str(zkey_path),
                str(proof_path),
                str(public_path),
            ],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        rows.append(
            {
                "metric": f"witness_private_mutation_{name}_rejected",
                "value": 1 if completed.returncode != 0 else 0,
                "unit": "mutation_rejected",
                "iterations": 1,
                "min": None,
                "max": None,
                "profile": profile,
            }
        )
    return rows


def _snarkjs_proof_mutation_row(
    *,
    root: Path,
    build: Path,
    verification_key: Path,
    proof_path: Path,
    public_path: Path,
    profile: str,
) -> dict[str, Any]:
    proof = json.loads(proof_path.read_text(encoding="utf-8"))
    proof["pi_a"][0] = str(int(proof["pi_a"][0]) + 1)
    malformed_path = build / "bench_proof_malformed.json"
    malformed_path.write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
    result = verify_with_snarkjs(
        root=root,
        verification_key=verification_key,
        public_signals=public_path,
        proof=malformed_path,
    )
    return {
        "metric": "snarkjs_verify_malformed_proof_rejected",
        "value": 0 if result.ok else 1,
        "unit": "mutation_rejected",
        "iterations": 1,
        "min": None,
        "max": None,
        "profile": profile,
    }


def _snarkjs_public_mutation_rows(
    *,
    root: Path,
    build: Path,
    verification_key: Path,
    proof_path: Path,
    public_values: list[int],
    profile: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, name in enumerate(PUBLIC_INPUT_ORDER):
        mutated_public_path = build / f"bench_public_mutated_{index:02d}_{name}.json"
        mutated = list(public_values)
        mutated[index] += 1
        write_public_signals(mutated_public_path, mutated)
        mutation_result = verify_with_snarkjs(
            root=root,
            verification_key=verification_key,
            public_signals=mutated_public_path,
            proof=proof_path,
        )
        rows.append(
            {
                "metric": f"snarkjs_verify_mutated_public_{name}",
                "value": 0 if mutation_result.ok else 1,
                "unit": "mutation_rejected",
                "iterations": 1,
                "min": None,
                "max": None,
                "profile": profile,
            }
        )
    return rows


def run_zk_benchmark(
    root: Path,
    *,
    iterations: int = 3,
    warmup: int = 1,
    zkey: Path | None = None,
    verification_key: Path | None = None,
    solidity_verifier: Path | None = None,
) -> pd.DataFrame:
    if iterations <= 0:
        raise ValueError("iterations must be positive")
    if warmup < 0:
        raise ValueError("warmup must be non-negative")
    profile = default_profile(root)
    build = root / "zk" / "build"
    input_path = root / "zk" / "inputs" / "valid_quorum.json"
    wasm_path = Path(profile.wasm_path)
    zkey_path = zkey or Path(profile.zkey_path)
    verification_key_path = verification_key or Path(profile.verification_key_path)
    verifier_path = solidity_verifier or root / "contracts" / "src" / "AidMeshGroth16Verifier.sol"
    for path in (
        input_path,
        wasm_path,
        zkey_path,
        verification_key_path,
        verifier_path,
    ):
        if not path.exists():
            raise FileNotFoundError(path)

    proof_times_ms: list[float] = []
    proof_path = build / "bench_proof.json"
    public_path = build / "bench_public.json"
    for index in range(warmup + iterations):
        proof_i = build / f"bench_proof_{index + 1}.json"
        public_i = build / f"bench_public_{index + 1}.json"
        started = time.perf_counter()
        subprocess.run(
            [
                snarkjs_command(),
                "snarkjs",
                "groth16",
                "fullprove",
                str(input_path),
                str(wasm_path),
                str(zkey_path),
                str(proof_i),
                str(public_i),
            ],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
        if index >= warmup:
            proof_times_ms.append(elapsed_ms)
        proof_path = proof_i
        public_path = public_i

    verification = verify_with_snarkjs(
        root=root,
        verification_key=verification_key_path,
        public_signals=public_path,
        proof=proof_path,
        check=True,
    )
    public_values = load_public_signals(public_path)
    mutation_rows = _snarkjs_public_mutation_rows(
        root=root,
        build=build,
        verification_key=verification_key_path,
        proof_path=proof_path,
        public_values=public_values,
        profile=profile.circuit_id,
    )
    witness_mutation_rows = _private_witness_mutation_rows(
        root=root,
        build=build,
        input_path=input_path,
        wasm_path=wasm_path,
        zkey_path=zkey_path,
        profile=profile.circuit_id,
    )
    malformed_proof_row = _snarkjs_proof_mutation_row(
        root=root,
        build=build,
        verification_key=verification_key_path,
        proof_path=proof_path,
        public_path=public_path,
        profile=profile.circuit_id,
    )

    calldata_path = build / "bench_calldata.txt"
    with calldata_path.open("w", encoding="ascii") as handle:
        subprocess.run(
            [
                snarkjs_command(),
                "snarkjs",
                "zkey",
                "export",
                "soliditycalldata",
                str(public_path),
                str(proof_path),
            ],
            cwd=root,
            check=True,
            stdout=handle,
            text=True,
        )

    evm_gas, evm_valid, evm_rejected, evm_mutations, evm_calldata_bytes = _evm_verifier_gas(
        root, calldata_path, verifier_path
    )
    proof_sorted = sorted(proof_times_ms)
    rows = [
        {
            "metric": "groth16_fullprove_ms",
            "value": statistics.median(proof_sorted),
            "unit": "ms",
            "iterations": iterations,
            "min": min(proof_sorted),
            "max": max(proof_sorted),
            "profile": profile.circuit_id,
        },
        {
            "metric": "groth16_fullprove_p95_ms",
            "value": float(np.percentile(proof_times_ms, 95)),
            "unit": "ms",
            "iterations": iterations,
            "min": None,
            "max": None,
            "profile": profile.circuit_id,
        },
        {
            "metric": "groth16_fullprove_p99_ms",
            "value": float(np.percentile(proof_times_ms, 99)),
            "unit": "ms",
            "iterations": iterations,
            "min": None,
            "max": None,
            "profile": profile.circuit_id,
        },
        {
            "metric": "snarkjs_verify_valid",
            "value": 1 if verification.ok else 0,
            "unit": "bool",
            "iterations": 1,
            "min": None,
            "max": None,
            "profile": profile.circuit_id,
        },
        {
            "metric": "snarkjs_verify_public_mutations_rejected",
            "value": sum(int(row["value"]) for row in mutation_rows),
            "unit": f"of_{len(PUBLIC_INPUT_ORDER)}",
            "iterations": 1,
            "min": None,
            "max": None,
            "profile": profile.circuit_id,
        },
        malformed_proof_row,
        {
            "metric": "evm_verifyProof_gas",
            "value": evm_gas,
            "unit": "gas",
            "iterations": 1,
            "min": None,
            "max": None,
            "profile": profile.circuit_id,
        },
        {
            "metric": "evm_verifyProof_valid_call",
            "value": 1 if evm_valid else 0,
            "unit": "bool",
            "iterations": 1,
            "min": None,
            "max": None,
            "profile": profile.circuit_id,
        },
        {
            "metric": "evm_verifyProof_public_mutations_rejected",
            "value": evm_rejected,
            "unit": f"of_{evm_mutations}",
            "iterations": 1,
            "min": None,
            "max": None,
            "profile": profile.circuit_id,
        },
        {
            "metric": "snarkjs_calldata_text_bytes",
            "value": calldata_path.stat().st_size,
            "unit": "bytes",
            "iterations": 1,
            "min": None,
            "max": None,
            "profile": profile.circuit_id,
        },
        {
            "metric": "evm_verifyProof_calldata_bytes",
            "value": evm_calldata_bytes,
            "unit": "bytes",
            "iterations": 1,
            "min": None,
            "max": None,
            "profile": profile.circuit_id,
        },
        {
            "metric": "r1cs_sha256",
            "value": _sha256(Path(profile.r1cs_path)),
            "unit": "hex",
            "iterations": 1,
            "min": None,
            "max": None,
            "profile": profile.circuit_id,
        },
        {
            "metric": "zkey_sha256",
            "value": _sha256(zkey_path),
            "unit": "hex",
            "iterations": 1,
            "min": None,
            "max": None,
            "profile": profile.circuit_id,
        },
        {
            "metric": "verification_key_sha256",
            "value": _sha256(verification_key_path),
            "unit": "hex",
            "iterations": 1,
            "min": None,
            "max": None,
            "profile": profile.circuit_id,
        },
        {
            "metric": "solidity_verifier_sha256",
            "value": _sha256(verifier_path),
            "unit": "hex",
            "iterations": 1,
            "min": None,
            "max": None,
            "profile": profile.circuit_id,
        },
        {
            "metric": "domain_tags_sha256",
            "value": _sha256(root / "zk" / "domain_tags.json"),
            "unit": "hex",
            "iterations": 1,
            "min": None,
            "max": None,
            "profile": profile.circuit_id,
        },
        {
            "metric": "circuit_source_sha256",
            "value": _sha256(root / "zk" / "circuits" / "aidmesh_voucher.circom"),
            "unit": "hex",
            "iterations": 1,
            "min": None,
            "max": None,
            "profile": profile.circuit_id,
        },
        {
            "metric": "constraint_count",
            "value": profile.constraint_count,
            "unit": "constraints",
            "iterations": 1,
            "min": None,
            "max": None,
            "profile": profile.circuit_id,
        },
    ]
    rows.extend(mutation_rows)
    rows.extend(witness_mutation_rows)
    return pd.DataFrame(rows)


def write_zk_benchmark(frame: pd.DataFrame, output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output, index=False)
    return output
