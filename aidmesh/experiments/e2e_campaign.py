"""Integrated held-out contact -> protocol -> real Groth16 -> local-EVM campaign."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import platform
import subprocess
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from eth_account import Account
from eth_account.messages import encode_defunct
from web3 import Web3
from web3.providers.eth_tester import EthereumTesterProvider

from aidmesh.common.canonical import stable_hash
from aidmesh.common.errors import DuplicateError
from aidmesh.crypto.groth16 import parse_solidity_calldata, snarkjs_command
from aidmesh.protocol.campaign import CampaignHeader, CommitteeConfig, SafetyDomain
from aidmesh.protocol.inventory import SiteInventory
from aidmesh.protocol.model import Voucher, witness_request_for
from aidmesh.protocol.quorum import assemble_certificate
from aidmesh.protocol.risk_escrow import EscrowRiskLedger
from aidmesh.protocol.witness import SQLiteWitnessStore, Witness
from aidmesh.simulator.dtn import (
    HOUR_US,
    BundleSpec,
    RoutingPolicy,
    TemporalContactSimulator,
    load_contacts_csv,
)

from .solidity import solc_command
from .temporal_frontier import (
    TemporalPathIndex,
    _calibration_roles,
    _initial_balances,
    generate_heldout_requests,
)

EVM_TX_GAS = 12_000_000


def _wait_success(web3: Web3, tx_hash: bytes, *, label: str) -> Any:
    receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
    if receipt.status != 1:
        raise AssertionError(f"local-EVM transaction failed: {label}")
    return receipt


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")


def _unlink_with_retry(path: Path, *, attempts: int = 20) -> None:
    """Remove an orphaned prover file after Windows releases its child-process handle."""

    for attempt in range(attempts):
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            if attempt + 1 == attempts:
                raise
            time.sleep(0.1)


def _command_version(command: list[str], *, cwd: Path) -> str:
    completed = subprocess.run(
        command,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return (completed.stdout or completed.stderr).strip()


def _environment_manifest(root: Path) -> dict[str, Any]:
    packages = {}
    for name in ("web3", "eth-tester", "py-evm", "cryptography", "pandas"):
        packages[name] = importlib.metadata.version(name)
    hashed_paths = {
        "campaign_runner": root / "aidmesh" / "experiments" / "e2e_campaign.py",
        "circuit_wasm": root / "zk" / "build" / "aidmesh_voucher_js" / "aidmesh_voucher.wasm",
        "development_zkey": root / "zk" / "build" / "aidmesh_dev_final.zkey",
        "verification_key": root / "zk" / "build" / "verification_key.json",
        "domain_tags": root / "zk" / "domain_tags.json",
        "solc_binary": Path(solc_command(root)),
        "verifier_solidity": root / "contracts" / "src" / "AidMeshGroth16Verifier.sol",
        "gateway_solidity": root
        / "contracts"
        / "src"
        / "AidMeshVerifiedSettlementGateway.sol",
        "settlement_solidity": root / "contracts" / "src" / "AidMeshSettlement.sol",
    }
    return {
        "os": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "python": sys.version,
        "packages": packages,
        "node": _command_version(["node", "--version"], cwd=root),
        "snarkjs": _command_version(
            [
                "node",
                "-p",
                "require('./node_modules/snarkjs/package.json').version",
            ],
            cwd=root,
        ),
        "solc": _command_version([solc_command(root), "--version"], cwd=root),
        "sha256": {label: _sha256(path) for label, path in hashed_paths.items()},
    }


def _implementation_manifest(root: Path) -> dict[str, Any]:
    paths = {
        "e2e_campaign": root / "aidmesh" / "experiments" / "e2e_campaign.py",
        "temporal_frontier": root
        / "aidmesh"
        / "experiments"
        / "temporal_frontier.py",
        "dtn_simulator": root / "aidmesh" / "simulator" / "dtn.py",
        "campaign_protocol": root / "aidmesh" / "protocol" / "campaign.py",
        "inventory_protocol": root / "aidmesh" / "protocol" / "inventory.py",
        "risk_escrow_protocol": root / "aidmesh" / "protocol" / "risk_escrow.py",
        "witness_protocol": root / "aidmesh" / "protocol" / "witness.py",
        "input_generator": root / "scripts" / "generate-e2e-inputs.mjs",
        "domain_tags": root / "zk" / "domain_tags.json",
        "circuit_source": root / "zk" / "circuits" / "aidmesh_voucher.circom",
        "circuit_wasm": root
        / "zk"
        / "build"
        / "aidmesh_voucher_js"
        / "aidmesh_voucher.wasm",
        "development_zkey": root / "zk" / "build" / "aidmesh_dev_final.zkey",
        "verification_key": root / "zk" / "build" / "verification_key.json",
        "verifier_contract": root
        / "contracts"
        / "src"
        / "AidMeshGroth16Verifier.sol",
        "gateway_contract": root
        / "contracts"
        / "src"
        / "AidMeshVerifiedSettlementGateway.sol",
        "settlement_contract": root
        / "contracts"
        / "src"
        / "AidMeshSettlement.sol",
        "solc_binary": Path(solc_command(root)),
    }
    hashes = {label: _sha256(path) for label, path in paths.items()}
    return {
        "schema_version": 1,
        "sha256": hashes,
        "implementation_hash": stable_hash(
            hashes, domain="aidmesh/e2e/implementation/v1"
        ),
    }


@dataclass(frozen=True)
class E2ERegistry:
    registry_id: str
    profile: str
    domain_tags: str
    compiler_version: str
    evm_version: str
    contexts: tuple[str, ...]
    seeds: tuple[int, ...]
    methods: tuple[str, ...]
    cap_fractions: tuple[float, ...]
    witness_regimes: tuple[str, ...]
    calibration_fraction: float
    buffer_bytes: int
    copy_budget: int
    receipt_bytes: int
    fallback_window: int
    value: int
    proof_workers: int
    voucher_sample_per_context: int
    sampling_strategy: str


def load_e2e_registry(path: Path) -> E2ERegistry:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    registry = E2ERegistry(
        registry_id=str(raw["registry_id"]),
        profile=str(raw["profile"]),
        domain_tags=str(raw["domain_tags"]),
        compiler_version=str(raw["compiler"]["version"]),
        evm_version=str(raw["compiler"]["evm_version"]),
        contexts=tuple(str(value) for value in raw["contexts"]),
        seeds=tuple(int(value) for value in raw["seeds"]),
        methods=tuple(str(value) for value in raw["methods"]),
        cap_fractions=tuple(float(value) for value in raw["cap_fractions"]),
        witness_regimes=tuple(str(value) for value in raw["witness_regimes"]),
        calibration_fraction=float(raw["calibration_fraction"]),
        buffer_bytes=int(raw["buffer_bytes"]),
        copy_budget=int(raw["copy_budget"]),
        receipt_bytes=int(raw["receipt_bytes"]),
        fallback_window=int(raw["fallback_window"]),
        value=int(raw["value"]),
        proof_workers=int(raw.get("proof_workers", 1)),
        voucher_sample_per_context=int(raw["voucher_sample_per_context"]),
        sampling_strategy=str(raw["sampling_strategy"]),
    )
    if not registry.registry_id or not registry.contexts or not registry.seeds:
        raise ValueError("e2e registry identifiers and workload cannot be empty")
    if registry.profile != "aidmesh-voucher-p1-groth16-bn128-domain-v2-tagged":
        raise ValueError("only the canonical P1-v2-tagged profile may be executed")
    if registry.domain_tags != "zk/domain_tags.json":
        raise ValueError("domain tag registry must be zk/domain_tags.json")
    if registry.compiler_version != "0.8.36" or registry.evm_version != "prague":
        raise ValueError("E2E campaign requires Solidity 0.8.36 targeting prague")
    if set(registry.methods) != {"static_rights", "transferable_rights"}:
        raise ValueError("e2e methods must be static_rights and transferable_rights")
    if set(registry.witness_regimes) != {"quorum", "fallback"}:
        raise ValueError("e2e witness regimes must be quorum and fallback")
    if not 0 < registry.calibration_fraction < 1:
        raise ValueError("calibration_fraction must be in (0,1)")
    if any(not 0 < value <= 1 for value in registry.cap_fractions):
        raise ValueError("cap fractions must be in (0,1]")
    if min(
        registry.buffer_bytes,
        registry.copy_budget,
        registry.receipt_bytes,
        registry.fallback_window,
        registry.value,
        registry.proof_workers,
        registry.voucher_sample_per_context,
    ) <= 0:
        raise ValueError("e2e numeric parameters must be positive")
    if registry.sampling_strategy != "stable-hash-v1":
        raise ValueError("unsupported e2e voucher sampling strategy")
    return registry


def _registered_voucher_sample(
    voucher_keys: tuple[str, ...],
    *,
    context: str,
    seed: int,
    limit: int,
) -> tuple[str, ...]:
    """Select logical vouchers without depending on trace or filename ordering."""

    ranked = sorted(
        set(voucher_keys),
        key=lambda key: (
            stable_hash(
                {"context": context, "seed": seed, "voucher_key": key},
                domain="aidmesh/e2e/registered-voucher-sample/v1",
            ),
            key,
        ),
    )
    return tuple(sorted(ranked[:limit]))


@dataclass(frozen=True)
class ProofArtifact:
    key: str
    mode: str
    input_path: Path
    proof_path: Path
    public_path: Path
    calldata_path: Path
    nullifier: str
    fallback_tag: str
    proof_ms: float
    proof_sha256: str
    public_sha256: str

    @property
    def calldata(self) -> tuple[list[int], list[list[int]], list[int], list[int]]:
        return parse_solidity_calldata(self.calldata_path)


def _numeric_campaign_id(registry_id: str, context: str, seed: int) -> int:
    digest = stable_hash(
        {"registry": registry_id, "context": context, "seed": seed},
        domain="aidmesh/e2e/campaign-id/v1",
    )
    return int(digest[:30], 16)


BN254_SCALAR_FIELD = (
    21888242871839275222246405745257275088548364400416034343698204186575808495617
)


def _field_commitment(value: object, *, domain: str) -> int:
    """Map a canonical SHA-256 commitment into the non-zero BN254 scalar field."""

    digest = stable_hash(value, domain=domain)
    return (int(digest, 16) % (BN254_SCALAR_FIELD - 1)) + 1


def prepare_proof_cache(
    root: Path,
    *,
    registry: E2ERegistry,
    context: str,
    seed: int,
    domain_context: dict[str, int],
    voucher_keys: tuple[str, ...],
    output: Path,
) -> dict[tuple[str, str], ProofArtifact]:
    cache = output / "proofs" / context / f"seed-{seed}"
    ordered_keys = tuple(sorted(set(voucher_keys)))
    spec = {
        "registryId": registry.registry_id,
        "context": context,
        "seed": seed,
        "campaignId": str(_numeric_campaign_id(registry.registry_id, context, seed)),
        "epoch": 1,
        "riskCell": 7,
        "category": 3,
        "value": registry.value,
        "fallbackWindow": registry.fallback_window,
        "chainId": str(domain_context["chainId"]),
        "gatewayAddress": str(domain_context["gatewayAddress"]),
        "settlementAddress": str(domain_context["settlementAddress"]),
        "headerHash": str(domain_context["headerHash"]),
        "policyHash": str(domain_context["policyHash"]),
        "committeeHash": str(domain_context["committeeHash"]),
        "modes": ["quorum", "fallback"],
        "vouchers": [{"key": value} for value in ordered_keys],
    }
    expected_spec_hash = stable_hash(spec, domain="aidmesh/e2e/proof-spec/v1")
    version_root = cache / f"spec-{expected_spec_hash[:16]}"
    inputs = version_root / "inputs"
    spec_path = version_root / "proof_spec.json"
    if not (inputs / "input_manifest.json").exists():
        _write_json(spec_path, spec)
        subprocess.run(
            [
                "node",
                str(root / "scripts" / "generate-e2e-inputs.mjs"),
                str(spec_path),
                str(inputs),
            ],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
    manifest = json.loads((inputs / "input_manifest.json").read_text(encoding="utf-8"))
    wasm = root / "zk" / "build" / "aidmesh_voucher_js" / "aidmesh_voucher.wasm"
    zkey = root / "zk" / "build" / "aidmesh_dev_final.zkey"
    verification_key = root / "zk" / "build" / "verification_key.json"
    for required in (wasm, zkey, verification_key):
        if not required.exists():
            raise FileNotFoundError(required)
    artifacts: dict[tuple[str, str], ProofArtifact] = {}
    for vector in manifest["vectors"]:
        key, mode = str(vector["key"]), str(vector["mode"])
        vector_id = f"{stable_hash({'key': key}, domain='aidmesh/e2e/vector/v1')[:16]}-{mode}"
        vector_root = version_root / "artifacts" / vector_id
        input_path = inputs / str(vector["input"])
        proof_path = vector_root / "proof.json"
        public_path = vector_root / "public.json"
        calldata_path = vector_root / "calldata.txt"
        meta_path = vector_root / "meta.json"
        cache_valid = False
        if meta_path.exists():
            try:
                cached_meta = json.loads(meta_path.read_text(encoding="utf-8"))
                json.loads(proof_path.read_text(encoding="utf-8"))
                json.loads(public_path.read_text(encoding="utf-8"))
                cache_valid = all(
                    (
                        path.exists()
                        and _sha256(path) == str(cached_meta[f"{label}_sha256"])
                    )
                    for label, path in (
                        ("proof", proof_path),
                        ("public", public_path),
                        ("calldata", calldata_path),
                    )
                )
            except (KeyError, OSError, ValueError, json.JSONDecodeError):
                cache_valid = False
        if not cache_valid:
            vector_root.mkdir(parents=True, exist_ok=True)
            temporary_proof = vector_root / "proof.json.building"
            temporary_public = vector_root / "public.json.building"
            temporary_calldata = vector_root / "calldata.txt.building"
            for temporary in (temporary_proof, temporary_public, temporary_calldata):
                _unlink_with_retry(temporary)
            started = time.perf_counter()
            completed = subprocess.run(
                [
                    snarkjs_command(),
                    "snarkjs",
                    "groth16",
                    "fullprove",
                    str(input_path),
                    str(wasm),
                    str(zkey),
                    str(temporary_proof),
                    str(temporary_public),
                ],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            )
            proof_ms = (time.perf_counter() - started) * 1000
            # Fail before commit if an interrupted/buggy prover emitted malformed JSON.
            json.loads(temporary_proof.read_text(encoding="utf-8"))
            json.loads(temporary_public.read_text(encoding="utf-8"))
            verify = subprocess.run(
                [
                    snarkjs_command(),
                    "snarkjs",
                    "groth16",
                    "verify",
                    str(verification_key),
                    str(temporary_public),
                    str(temporary_proof),
                ],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            )
            with temporary_calldata.open("w", encoding="ascii", newline="\n") as handle:
                subprocess.run(
                    [
                        snarkjs_command(),
                        "snarkjs",
                        "zkey",
                        "export",
                        "soliditycalldata",
                        str(temporary_public),
                        str(temporary_proof),
                    ],
                    cwd=root,
                    check=True,
                    stdout=handle,
                    text=True,
                )
            # Commit only the fully parsed and independently verified artifact set.
            temporary_proof.replace(proof_path)
            temporary_public.replace(public_path)
            temporary_calldata.replace(calldata_path)
            _write_json(
                meta_path,
                {
                    "schema_version": 1,
                    "key": key,
                    "mode": mode,
                    "proof_ms": proof_ms,
                    "fullprove_stdout": completed.stdout,
                    "snarkjs_verify_stdout": verify.stdout,
                    "input_sha256": _sha256(input_path),
                    "proof_sha256": _sha256(proof_path),
                    "public_sha256": _sha256(public_path),
                    "calldata_sha256": _sha256(calldata_path),
                    "wasm_sha256": _sha256(wasm),
                    "zkey_sha256": _sha256(zkey),
                    "verification_key_sha256": _sha256(verification_key),
                    "srs_profile": "single-operator development zkey; experimental only",
                },
            )
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        public = json.loads(public_path.read_text(encoding="utf-8"))
        if str(public[7]) != str(vector["nullifier"]):
            raise AssertionError("generated proof nullifier differs from input manifest")
        artifacts[(key, mode)] = ProofArtifact(
            key=key,
            mode=mode,
            input_path=input_path,
            proof_path=proof_path,
            public_path=public_path,
            calldata_path=calldata_path,
            nullifier=str(vector["nullifier"]),
            fallback_tag=str(vector["fallbackTag"]),
            proof_ms=float(meta["proof_ms"]),
            proof_sha256=str(meta["proof_sha256"]),
            public_sha256=str(meta["public_sha256"]),
        )
    return artifacts


def _compile_contracts(root: Path) -> dict[str, tuple[list[dict[str, Any]], str]]:
    paths = [
        root / "contracts" / "src" / "AidMeshGroth16Verifier.sol",
        root / "contracts" / "src" / "AidMeshSettlement.sol",
        root / "contracts" / "src" / "AidMeshVerifiedSettlementGateway.sol",
    ]
    completed = subprocess.run(
        [
            solc_command(root),
            "--optimize",
            "--via-ir",
            "--evm-version",
            "prague",
            "--combined-json",
            "abi,bin",
            *(str(path) for path in paths),
        ],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    contracts = json.loads(completed.stdout)["contracts"]
    names = {
        "verifier": "Groth16Verifier",
        "settlement": "AidMeshSettlement",
        "gateway": "AidMeshVerifiedSettlementGateway",
    }
    output: dict[str, tuple[list[dict[str, Any]], str]] = {}
    for label, contract_name in names.items():
        artifact = next(
            value for key, value in contracts.items() if key.endswith(f":{contract_name}")
        )
        abi = artifact["abi"] if isinstance(artifact["abi"], list) else json.loads(artifact["abi"])
        output[label] = (abi, artifact["bin"])
    return output


@dataclass
class RunContracts:
    gateway: Any
    settlement: Any
    deployment_gas: int


class LocalEVMHarness:
    def __init__(self, root: Path) -> None:
        self.web3 = Web3(EthereumTesterProvider())
        self.account_keys = self.web3.provider.ethereum_tester.backend.account_keys
        self.owner = self.web3.eth.accounts[0]
        self.relayer = self.web3.eth.accounts[1]
        self.site_accounts = tuple(self.web3.eth.accounts[2:6])
        self.artifacts = _compile_contracts(root)
        self.verifier, verifier_gas = self._deploy("verifier")
        self.verifier_deployment_gas = verifier_gas
        gateway, gateway_gas = self._deploy("gateway", self.verifier.address)
        settlement, settlement_gas = self._deploy("settlement", gateway.address)
        tx_hash = gateway.functions.setSettlement(settlement.address).transact(
            {"from": self.owner, "gas": EVM_TX_GAS}
        )
        link_receipt = _wait_success(self.web3, tx_hash, label="link-settlement")
        self.contracts = RunContracts(
            gateway,
            settlement,
            gateway_gas + settlement_gas + int(link_receipt.gasUsed),
        )

    def _deploy(self, label: str, *args: object) -> tuple[Any, int]:
        abi, bytecode = self.artifacts[label]
        factory = self.web3.eth.contract(abi=abi, bytecode=bytecode)
        tx_hash = factory.constructor(*args).transact(
            {"from": self.owner, "gas": EVM_TX_GAS}
        )
        receipt = _wait_success(self.web3, tx_hash, label=f"deploy-{label}")
        return (
            self.web3.eth.contract(address=receipt.contractAddress, abi=abi),
            int(receipt.gasUsed),
        )

    def new_run(self) -> RunContracts:
        return self.contracts

    def snapshot(self) -> int:
        return int(self.web3.provider.ethereum_tester.take_snapshot())

    def revert(self, snapshot_id: int) -> None:
        self.web3.provider.ethereum_tester.revert_to_snapshot(snapshot_id)

    def sign_receipt(self, site: str, payload: bytes) -> bytes:
        index = self.web3.eth.accounts.index(site)
        signed = Account.sign_message(
            encode_defunct(primitive=payload),
            private_key=self.account_keys[index].to_hex(),
        )
        return bytes(signed.signature)


def _register_fault_campaign(
    evm: LocalEVMHarness,
    contracts: RunContracts,
    public: list[int],
    *,
    site: str,
    fund: bool,
) -> bytes:
    domain = contracts.gateway.functions.proofDomain(public).call()
    tx = contracts.settlement.functions.registerCampaign(
        domain, int(public[14]), int(public[15]), int(public[16]), [site]
    ).transact({"from": evm.owner, "gas": EVM_TX_GAS})
    _wait_success(evm.web3, tx, label="fault-register-campaign")
    if fund:
        tx = contracts.settlement.functions.fundCampaign(domain, int(public[4])).transact(
            {"from": evm.owner, "gas": EVM_TX_GAS}
        )
        _wait_success(evm.web3, tx, label="fault-fund-campaign")
    return bytes(domain)


def run_e2e_fault_controls(
    evm: LocalEVMHarness, artifact: ProofArtifact, output: Path
) -> dict[str, Any]:
    """Exercise proof/receipt/authorization failures against real local bytecode."""

    a, b, c, public = artifact.calldata
    site = evm.site_accounts[0]
    attacker = evm.web3.eth.accounts[6]
    contracts = evm.new_run()
    initial_snapshot = evm.snapshot()
    domain = _register_fault_campaign(evm, contracts, public, site=site, fund=True)
    default_evidence_hash = Web3.keccak(text="e2e-fault-authorization-evidence")

    def signature(
        receipt_id: bytes,
        signer: str = site,
        bound_site: str = site,
        evidence_hash: bytes = default_evidence_hash,
    ) -> bytes:
        statement_hash = contracts.gateway.functions.proofStatementHash(public).call()
        authorization = contracts.gateway.functions.authorizationCommitment(
            public, receipt_id, bound_site, evidence_hash
        ).call()
        payload = contracts.gateway.functions.receiptPayload(
            domain, statement_hash, receipt_id, bound_site, authorization
        ).call()
        return evm.sign_receipt(signer, bytes(payload))

    rows: list[dict[str, Any]] = []
    first_receipt = Web3.keccak(text="e2e-fault-valid")
    tx = contracts.gateway.functions.verifyAndSettle(
        a,
        b,
        c,
        public,
        (first_receipt, site, default_evidence_hash, signature(first_receipt)),
    ).transact({"from": evm.relayer, "gas": EVM_TX_GAS})
    valid_gas = int(_wait_success(evm.web3, tx, label="fault-valid").gasUsed)
    rows.append(
        {
            "case": "valid",
            "expected": "settled",
            "observed": "settled",
            "passed": True,
            "gas_used": valid_gas,
        }
    )
    credit_before = int(contracts.settlement.functions.credits(domain, site).call())
    replay_receipt = Web3.keccak(text="e2e-fault-replay")
    tx = contracts.gateway.functions.verifyAndSettle(
        a,
        b,
        c,
        public,
        (replay_receipt, site, default_evidence_hash, signature(replay_receipt)),
    ).transact({"from": evm.relayer, "gas": EVM_TX_GAS})
    replay_gas = int(_wait_success(evm.web3, tx, label="fault-replay").gasUsed)
    credit_after = int(contracts.settlement.functions.credits(domain, site).call())
    rows.append(
        {
            "case": "duplicate_nullifier_replay",
            "expected": "conflict_no_credit",
            "observed": "conflict_no_credit" if credit_after == credit_before else "credited",
            "passed": credit_after == credit_before == int(public[4]),
            "gas_used": replay_gas,
        }
    )

    def rejection_case(
        name: str,
        call_args: tuple[object, ...],
        expected_fragment: str,
    ) -> None:
        if len(call_args) == 8:
            call_args = (*call_args[:4], tuple(call_args[4:]))
        reason_matches = False
        call_error = ""
        try:
            contracts.gateway.functions.verifyAndSettle(*call_args).call(
                {"from": evm.relayer, "gas": EVM_TX_GAS}
            )
        except Exception as error:  # backend exception class varies by py-evm release
            call_error = str(error)
            reason_matches = expected_fragment in call_error
        tx_hash = contracts.gateway.functions.verifyAndSettle(*call_args).transact(
            {"from": evm.relayer, "gas": EVM_TX_GAS}
        )
        receipt = evm.web3.eth.wait_for_transaction_receipt(tx_hash)
        rows.append(
            {
                "case": name,
                "expected": "revert",
                "observed": f"status-{receipt.status}",
                "passed": receipt.status == 0 and reason_matches,
                "error": call_error,
                "gas_used": int(receipt.gasUsed),
            }
        )

    mutated_public = list(public)
    mutated_public[4] += 1
    mutation_receipt = Web3.keccak(text="e2e-fault-public")
    rejection_case(
        "mutated_public_value",
        (
            a,
            b,
            c,
            mutated_public,
            mutation_receipt,
            site,
            default_evidence_hash,
            signature(mutation_receipt),
        ),
        "INVALID_PROOF",
    )
    for case_name, public_index, expected_error in (
        ("cross_chain_replay", 11, "WRONG_CHAIN"),
        ("cross_gateway_replay", 12, "WRONG_GATEWAY"),
        ("cross_settlement_replay", 13, "WRONG_SETTLEMENT"),
        ("mutated_header_domain", 14, "INVALID_PROOF"),
        ("mutated_policy_domain", 15, "INVALID_PROOF"),
        ("mutated_committee_domain", 16, "INVALID_PROOF"),
    ):
        rebound_public = list(public)
        rebound_public[public_index] += 1
        rebound_id = Web3.keccak(text=f"e2e-fault-{case_name}")
        rejection_case(
            case_name,
            (
                a,
                b,
                c,
                rebound_public,
                rebound_id,
                site,
                default_evidence_hash,
                signature(rebound_id),
            ),
            expected_error,
        )
    malformed_a = list(a)
    malformed_a[0] += 1
    malformed_receipt = Web3.keccak(text="e2e-fault-proof")
    rejection_case(
        "mutated_proof_point",
        (
            malformed_a,
            b,
            c,
            public,
            malformed_receipt,
            site,
            default_evidence_hash,
            signature(malformed_receipt),
        ),
        "execution reverted",
    )
    wrong_signature_receipt = Web3.keccak(text="e2e-fault-signature")
    rejection_case(
        "wrong_site_signature",
        (
            a,
            b,
            c,
            public,
            wrong_signature_receipt,
            site,
            default_evidence_hash,
            signature(wrong_signature_receipt, signer=attacker),
        ),
        "WRONG_SITE_SIGNATURE",
    )
    malleable_receipt = Web3.keccak(text="e2e-fault-high-s-signature")
    canonical_signature = signature(malleable_receipt)
    curve_order = int(
        "FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141",
        16,
    )
    canonical_s = int.from_bytes(canonical_signature[32:64], "big")
    canonical_v = canonical_signature[64]
    high_s_signature = (
        canonical_signature[:32]
        + (curve_order - canonical_s).to_bytes(32, "big")
        + bytes([canonical_v ^ 1 if canonical_v in (0, 1) else 55 - canonical_v])
    )
    rejection_case(
        "noncanonical_high_s_signature",
        (
            a,
            b,
            c,
            public,
            malleable_receipt,
            site,
            default_evidence_hash,
            high_s_signature,
        ),
        "NON_CANONICAL_SIGNATURE_S",
    )
    unauthorized_receipt = Web3.keccak(text="e2e-fault-unauthorized")
    rejection_case(
        "unauthorized_site",
        (
            a,
            b,
            c,
            public,
            unauthorized_receipt,
            attacker,
            default_evidence_hash,
            signature(unauthorized_receipt, signer=attacker, bound_site=attacker),
        ),
        "UNAUTHORIZED_SITE",
    )
    rebound_receipt = Web3.keccak(text="e2e-fault-rebound")
    old_receipt = Web3.keccak(text="e2e-fault-old-binding")
    rejection_case(
        "receipt_id_rebinding",
        (
            a,
            b,
            c,
            public,
            rebound_receipt,
            site,
            default_evidence_hash,
            signature(old_receipt),
        ),
        "WRONG_SITE_SIGNATURE",
    )
    authorization_rebound_receipt = Web3.keccak(text="e2e-fault-authorization-rebound")
    substituted_evidence = Web3.keccak(text="e2e-fault-substituted-authorization")
    rejection_case(
        "authorization_evidence_rebinding",
        (
            a,
            b,
            c,
            public,
            authorization_rebound_receipt,
            site,
            substituted_evidence,
            signature(authorization_rebound_receipt),
        ),
        "WRONG_SITE_SIGNATURE",
    )
    zero_authorization_receipt = Web3.keccak(text="e2e-fault-zero-authorization")
    rejection_case(
        "zero_authorization_evidence",
        (
            a,
            b,
            c,
            public,
            zero_authorization_receipt,
            site,
            bytes(32),
            signature(zero_authorization_receipt),
        ),
        "ZERO_AUTHORIZATION_EVIDENCE",
    )

    evm.revert(initial_snapshot)
    empty_contracts = evm.new_run()
    empty_domain = _register_fault_campaign(
        evm, empty_contracts, public, site=site, fund=False
    )
    empty_receipt = Web3.keccak(text="e2e-fault-empty-escrow")
    empty_authorization = empty_contracts.gateway.functions.authorizationCommitment(
        public,
        empty_receipt,
        site,
        default_evidence_hash,
    ).call()
    empty_payload = empty_contracts.gateway.functions.receiptPayload(
        empty_domain,
        empty_contracts.gateway.functions.proofStatementHash(public).call(),
        empty_receipt,
        site,
        empty_authorization,
    ).call()
    empty_signature = evm.sign_receipt(site, bytes(empty_payload))
    reason_matches = False
    try:
        empty_contracts.gateway.functions.verifyAndSettle(
            a,
            b,
            c,
            public,
            (empty_receipt, site, default_evidence_hash, empty_signature),
        ).call({"from": evm.relayer, "gas": EVM_TX_GAS})
    except Exception as error:
        reason_matches = "INSUFFICIENT_ESCROW" in str(error)
    empty_tx = empty_contracts.gateway.functions.verifyAndSettle(
        a,
        b,
        c,
        public,
        (empty_receipt, site, default_evidence_hash, empty_signature),
    ).transact({"from": evm.relayer, "gas": EVM_TX_GAS})
    empty_tx_receipt = evm.web3.eth.wait_for_transaction_receipt(empty_tx)
    insufficient_passed = reason_matches and empty_tx_receipt.status == 0
    rows.append(
        {
            "case": "insufficient_escrow",
            "expected": "revert",
            "observed": f"status-{empty_tx_receipt.status}",
            "passed": insufficient_passed,
            "gas_used": int(empty_tx_receipt.gasUsed),
        }
    )
    output.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(rows)
    frame.to_csv(output / "e2e_fault_controls.csv", index=False, lineterminator="\n")
    summary = {
        "schema_version": 1,
        "cases": len(rows),
        "passed": int(frame["passed"].sum()),
        "all_passed": bool(frame["passed"].all()),
        "cases_by_name": {row["case"]: bool(row["passed"]) for row in rows},
    }
    _write_json(output / "e2e_fault_controls_summary.json", summary)
    if not summary["all_passed"]:
        raise AssertionError(f"E2E negative control failure: {rows}")
    return summary


class WitnessKeys:
    def __init__(self, witness_ids: tuple[str, ...]) -> None:
        self.private: dict[str, Ed25519PrivateKey] = {}
        for witness_id in witness_ids:
            seed = hashlib.sha256(f"aidmesh-e2e|{witness_id}".encode()).digest()
            self.private[witness_id] = Ed25519PrivateKey.from_private_bytes(seed)

    def sign(self, signer_id: str, digest: str) -> str:
        return self.private[signer_id].sign(digest.encode("ascii")).hex()

    def verify(self, signer_id: str, digest: str, signature: str) -> bool:
        try:
            self.private[signer_id].public_key().verify(
                bytes.fromhex(signature), digest.encode("ascii")
            )
            return True
        except (ValueError, TypeError):
            return False
        except Exception:
            return False


def _event(
    rows: list[dict[str, Any]],
    *,
    run_id: str,
    event_id: str,
    trace_us: int,
    component: str,
    action: str,
    outcome: str,
    **fields: object,
) -> None:
    rows.append(
        {
            "schema_version": 1,
            "run_id": run_id,
            "event_id": event_id,
            "sequence": len(rows),
            "trace_us": trace_us,
            "wall_ns": time.time_ns(),
            "component": component,
            "action": action,
            "outcome": outcome,
            **fields,
        }
    )


def _build_header(
    *,
    campaign_id: int,
    sites: tuple[str, ...],
    cap: int,
    start_us: int,
    end_us: int,
) -> CampaignHeader:
    committee = CommitteeConfig("e2e-k4-q3", ("w0", "w1", "w2", "w3"), 3, 1)
    return CampaignHeader(
        domain=SafetyDomain(str(campaign_id), 1, 1, committee.committee_id),
        risk_cell="7",
        committee=committee,
        authorized_sites=sites,
        fallback_categories=("3",),
        initial_risk_rights=tuple(sorted(_initial_balances(sites, cap).items())),
        start_tick=start_us,
        end_tick=end_us + 24 * HOUR_US,
        verifier_id="aidmesh-voucher-p1-groth16-bn128-domain-v2-tagged",
        policy_hash=stable_hash(
            {"cap": cap, "quorum": "K4q3f1"}, domain="aidmesh/e2e/policy/v1"
        ),
    )


def _proof_domain_context(
    *,
    evm: LocalEVMHarness,
    header: CampaignHeader,
    registry: E2ERegistry,
    cap: int,
) -> dict[str, int]:
    policy = {
        "schema": "aidmesh/proof-policy/v2",
        "risk_cell": header.risk_cell,
        "fallback_categories": list(header.fallback_categories),
        "fallback_window": registry.fallback_window,
        "value": registry.value,
        "registered_cap": cap,
        "risk_right_semantics": "conserved-transferable-or-static",
        "settlement_semantics": "proof-gated-accounting-conformance",
    }
    committee = {
        "committee_id": header.committee.committee_id,
        "members": list(header.committee.members),
        "threshold": header.committee.threshold,
        "byzantine_bound": header.committee.byzantine_bound,
    }
    return {
        "chainId": int(evm.web3.eth.chain_id),
        "gatewayAddress": int(evm.contracts.gateway.address, 16),
        "settlementAddress": int(evm.contracts.settlement.address, 16),
        "headerHash": _field_commitment(
            header.header_hash, domain="aidmesh/e2e/full-header-field/v2"
        ),
        "policyHash": _field_commitment(
            policy, domain="aidmesh/e2e/full-policy-field/v2"
        ),
        "committeeHash": _field_commitment(
            committee, domain="aidmesh/e2e/committee-field/v2"
        ),
    }


def _escrow_balance(ledger: EscrowRiskLedger, site: str) -> int:
    return dict(ledger.snapshot().balances)[site]


def _validated_cached_run(
    summary_path: Path,
    events_path: Path,
    *,
    run_id: str,
    workload_fingerprint: str,
    implementation_hash: str,
) -> dict[str, Any] | None:
    if not summary_path.exists():
        return None
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if not events_path.exists():
        raise AssertionError(f"cached run has no raw event log: {run_id}")
    events = [
        json.loads(line)
        for line in events_path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    required = {
        "status": "complete",
        "run_id": run_id,
        "workload_fingerprint": workload_fingerprint,
        "implementation_hash": implementation_hash,
        "event_rows": len(events),
        "event_checksum": stable_hash(events, domain="aidmesh/e2e/events/v1"),
    }
    mismatches = {
        key: {"expected": expected, "observed": summary.get(key)}
        for key, expected in required.items()
        if summary.get(key) != expected
    }
    if any(event.get("run_id") != run_id for event in events):
        mismatches["event_run_id"] = {"expected": run_id, "observed": "mixed"}
    if mismatches:
        raise AssertionError(f"cached run integrity failure {run_id}: {mismatches}")
    return summary


def _run_one(
    *,
    registry: E2ERegistry,
    context: str,
    seed: int,
    cap: int,
    method: str,
    regime: str,
    contacts: tuple[Any, ...],
    roles: dict[str, tuple[str, ...]],
    requests: tuple[Any, ...],
    request_deliveries: dict[str, int],
    proofs: dict[tuple[str, str], ProofArtifact],
    evm: LocalEVMHarness,
    output: Path,
    implementation_hash: str,
) -> dict[str, Any]:
    workload_fingerprint = stable_hash(
        {
            "registry": asdict(registry),
            "implementation_hash": implementation_hash,
            "context": context,
            "seed": seed,
            "cap": cap,
            "method": method,
            "regime": regime,
            "requests": [
                {
                    "bundle": asdict(request.bundle),
                    "home_site": request.home_site,
                    "nullifier": request.nullifier,
                }
                for request in requests
            ],
        },
        domain="aidmesh/e2e/run-workload/v1",
    )
    run_id = f"{context}-s{seed}-c{cap}-{method}-{regime}-{workload_fingerprint[:12]}"
    run_root = (
        output
        / "runs"
        / context
        / f"seed-{seed}"
        / f"cap-{cap}"
        / method
        / regime
        / f"workload-{workload_fingerprint[:16]}"
    )
    events_path = run_root / "events.jsonl"
    summary_path = run_root / "summary.json"
    cached = _validated_cached_run(
        summary_path,
        events_path,
        run_id=run_id,
        workload_fingerprint=workload_fingerprint,
        implementation_hash=implementation_hash,
    )
    if cached is not None:
        return cached
    attempt_id = f"attempt-{time.time_ns()}"
    attempt_root = run_root / "attempts" / attempt_id
    events: list[dict[str, Any]] = []
    sites = roles["sites"]
    gateway_node = roles["relays"][0]
    campaign_id = _numeric_campaign_id(registry.registry_id, context, seed)
    header = _build_header(
        campaign_id=campaign_id,
        sites=sites,
        cap=cap,
        start_us=min(contact.start_us for contact in contacts),
        end_us=max(contact.end_us for contact in contacts),
    )
    expected_domain_context = _proof_domain_context(
        evm=evm, header=header, registry=registry, cap=cap
    )
    proof_mode = "quorum" if regime == "quorum" else "fallback"
    witness_keys = WitnessKeys(header.committee.members)
    stores: list[SQLiteWitnessStore] = []
    witnesses: list[Witness] = []
    for witness_id in header.committee.members:
        store = SQLiteWitnessStore(
            attempt_root / "witness" / f"{witness_id}.sqlite", signer_id=witness_id
        )
        stores.append(store)
        witnesses.append(Witness(witness_id, header, store, witness_keys.sign))
    available_witnesses = witnesses if regime == "quorum" else witnesses[:2]
    ledger = EscrowRiskLedger(_initial_balances(sites, cap))
    inventories = {site: SiteInventory({"3": len(requests)}) for site in sites}
    path_index = TemporalPathIndex(contacts)
    request_by_id = {request.bundle.bundle_id: request for request in requests}
    released: list[dict[str, Any]] = []
    local_seen: dict[str, set[str]] = {site: set() for site in sites}
    evm_snapshot = evm.snapshot()
    run_contracts = evm.new_run()
    try:
        for bundle_id, delivered_us in sorted(
            request_deliveries.items(), key=lambda item: (item[1], item[0])
        ):
            request = request_by_id[bundle_id]
            artifact = proofs[(request.nullifier, proof_mode)]
            event_id = stable_hash(
                {"run_id": run_id, "bundle_id": bundle_id}, domain="aidmesh/e2e/event/v1"
            )
            _event(
                events,
                run_id=run_id,
                event_id=event_id,
                trace_us=delivered_us,
                component="dtn-request",
                action="deliver",
                outcome="delivered",
                bundle_id=bundle_id,
                logical_voucher=request.nullifier,
            )
            proof_completed_us = delivered_us + math.ceil(artifact.proof_ms * 1000)
            proof_args = artifact.calldata
            proof_started_ns = time.perf_counter_ns()
            proof_ok = bool(evm.verifier.functions.verifyProof(*proof_args).call())
            verifier_wall_ns = time.perf_counter_ns() - proof_started_ns
            _event(
                events,
                run_id=run_id,
                event_id=event_id,
                trace_us=proof_completed_us,
                component="groth16-local-evm",
                action="verifyProof",
                outcome="verified" if proof_ok else "rejected",
                proof_sha256=artifact.proof_sha256,
                public_sha256=artifact.public_sha256,
                proof_generation_ms=artifact.proof_ms,
                verifier_wall_ns=verifier_wall_ns,
                circuit_nullifier=artifact.nullifier,
            )
            if not proof_ok or proof_completed_us > request.bundle.expires_us:
                continue
            site = request.bundle.destination
            if artifact.nullifier in local_seen[site]:
                _event(
                    events,
                    run_id=run_id,
                    event_id=event_id,
                    trace_us=proof_completed_us,
                    component="site",
                    action="freshness",
                    outcome="local_duplicate_rejected",
                )
                continue
            voucher = Voucher(
                domain=header.domain,
                risk_cell="7",
                category="3",
                value=registry.value,
                inventory_units=1,
                nullifier=artifact.nullifier,
                commitment=stable_hash(
                    {"proof": artifact.proof_sha256}, domain="aidmesh/e2e/commitment/v1"
                ),
                proof=artifact.proof_sha256,
            )
            witness_request = witness_request_for(voucher, site)
            shares = []
            for witness in available_witnesses:
                try:
                    shares.append(witness.sign(witness_request))
                except DuplicateError:
                    _event(
                        events,
                        run_id=run_id,
                        event_id=event_id,
                        trace_us=proof_completed_us,
                        component=f"witness-{witness.witness_id}",
                        action="persist-before-sign",
                        outcome="conflict_rejected",
                    )
            release_us = proof_completed_us
            path = "quorum"
            authorization_evidence_hash: str
            if len(shares) >= header.committee.threshold:
                certificate = assemble_certificate(
                    witness_request, shares, header.committee, witness_keys.verify
                )
                authorization_evidence_hash = stable_hash(
                    {
                        "path": path,
                        "domain": header.domain,
                        "nullifier": artifact.nullifier,
                        "site": site,
                        "certificate_digest": certificate.digest,
                    },
                    domain="aidmesh/path-authorization-evidence/v1",
                )
                _event(
                    events,
                    run_id=run_id,
                    event_id=event_id,
                    trace_us=release_us,
                    component="quorum",
                    action="assemble-certificate",
                    outcome="authorized",
                    signers=[share.signer_id for share in shares],
                )
            else:
                if regime == "quorum":
                    _event(
                        events,
                        run_id=run_id,
                        event_id=event_id,
                        trace_us=release_us,
                        component="quorum",
                        action="assemble-certificate",
                        outcome="conflict_or_insufficient_rejected",
                        shares=len(shares),
                    )
                    continue
                path = "fallback"
                balance = _escrow_balance(ledger, site)
                if balance <= 0 and method == "transferable_rights":
                    balances = dict(ledger.snapshot().balances)
                    donors = [
                        donor
                        for donor, donor_balance in balances.items()
                        if donor != site and donor_balance >= registry.value
                    ]
                    if donors:
                        donor = min(donors, key=lambda value: (-balances[value], value))
                        transfer_id = stable_hash(
                            {"event_id": event_id, "donor": donor, "site": site},
                            domain="aidmesh/e2e/transfer/v1",
                        )
                        ledger.begin_transfer(transfer_id, donor, site, registry.value)
                        arrival = path_index.earliest(
                            donor, site, release_us, request.bundle.expires_us
                        )
                        if arrival is None:
                            ledger.terminate_transfer(transfer_id)
                            _event(
                                events,
                                run_id=run_id,
                                event_id=event_id,
                                trace_us=release_us,
                                component="risk-right",
                                action="transfer",
                                outcome="expired_stranded",
                                source=donor,
                                destination=site,
                            )
                            continue
                        ledger.accept_transfer(transfer_id)
                        release_us = arrival
                        _event(
                            events,
                            run_id=run_id,
                            event_id=event_id,
                            trace_us=release_us,
                            component="risk-right",
                            action="transfer",
                            outcome="accepted",
                            source=donor,
                            destination=site,
                        )
                if _escrow_balance(ledger, site) < registry.value:
                    _event(
                        events,
                        run_id=run_id,
                        event_id=event_id,
                        trace_us=release_us,
                        component="risk-right",
                        action="spend",
                        outcome="insufficient_rights",
                    )
                    continue
                spend_id = stable_hash(event_id, domain="aidmesh/e2e/spend/v1")
                ledger.spend(spend_id, site, registry.value)
                authorization_evidence_hash = stable_hash(
                    {
                        "path": path,
                        "domain": header.domain,
                        "nullifier": artifact.nullifier,
                        "site": site,
                        "spend_id": spend_id,
                        "value": registry.value,
                        "fallback_window": registry.fallback_window,
                        "fallback_tag": artifact.fallback_tag,
                    },
                    domain="aidmesh/path-authorization-evidence/v1",
                )
                _event(
                    events,
                    run_id=run_id,
                    event_id=event_id,
                    trace_us=release_us,
                    component="risk-right",
                    action="spend",
                    outcome="authorized",
                    fallback_tag=artifact.fallback_tag,
                )
            reservation_id = stable_hash(event_id, domain="aidmesh/e2e/inventory/v1")
            if path != proof_mode:
                raise AssertionError(
                    f"authorization path {path} does not match P1 mode {proof_mode}"
                )
            inventories[site].reserve(
                reservation_id=reservation_id, category="3", units=1
            )
            inventories[site].commit(reservation_id)
            local_seen[site].add(artifact.nullifier)
            receipt_bundle_id = f"receipt-{event_id}"
            released.append(
                {
                    "event_id": event_id,
                    "bundle_id": receipt_bundle_id,
                    "site": site,
                    "path": path,
                    "released_us": release_us,
                    "expires_us": request.bundle.expires_us,
                    "proof": artifact,
                    "authorization_evidence_hash": authorization_evidence_hash,
                }
            )
            _event(
                events,
                run_id=run_id,
                event_id=event_id,
                trace_us=release_us,
                component="inventory",
                action="release",
                outcome="committed",
                site=site,
                path=path,
                circuit_nullifier=artifact.nullifier,
            )
        receipt_bundles = tuple(
            BundleSpec(
                bundle_id=item["bundle_id"],
                source=item["site"],
                destination=gateway_node,
                created_us=item["released_us"],
                ttl_us=max(1, item["expires_us"] - item["released_us"]),
                size_bytes=registry.receipt_bytes,
                risk_score=1.0,
            )
            for item in released
        )
        receipt_result = TemporalContactSimulator(
            contacts=contacts,
            bundles=receipt_bundles,
            policy=RoutingPolicy.EPIDEMIC,
            buffer_bytes=registry.buffer_bytes,
            copy_budget=registry.copy_budget,
        ).run()
        receipt_deliveries = {
            delivery.bundle_id: delivery for delivery in receipt_result.deliveries
        }
        sample_artifact = next(iter(proofs.values()))
        sample_public = sample_artifact.calldata[3]
        observed_domain_context = {
            "chainId": int(sample_public[11]),
            "gatewayAddress": int(sample_public[12]),
            "settlementAddress": int(sample_public[13]),
            "headerHash": int(sample_public[14]),
            "policyHash": int(sample_public[15]),
            "committeeHash": int(sample_public[16]),
        }
        if observed_domain_context != expected_domain_context:
            raise AssertionError(
                "proof domain context mismatch: "
                f"observed={observed_domain_context}, expected={expected_domain_context}"
            )
        domain = run_contracts.gateway.functions.proofDomain(sample_public).call()
        site_addresses = {
            site: evm.site_accounts[index] for index, site in enumerate(sites)
        }
        tx = run_contracts.settlement.functions.registerCampaign(
            domain,
            int(sample_public[14]),
            int(sample_public[15]),
            int(sample_public[16]),
            list(site_addresses.values()),
        ).transact({"from": evm.owner, "gas": EVM_TX_GAS})
        register_gas = int(
            _wait_success(evm.web3, tx, label=f"{run_id}-register").gasUsed
        )
        funding = max(registry.value, len(requests) * registry.value)
        tx = run_contracts.settlement.functions.fundCampaign(domain, funding).transact(
            {"from": evm.owner, "gas": EVM_TX_GAS}
        )
        fund_gas = int(_wait_success(evm.web3, tx, label=f"{run_id}-fund").gasUsed)
        released_by_bundle = {item["bundle_id"]: item for item in released}
        oracle_winner: dict[str, str] = {}
        oracle_credit: Counter[str] = Counter()
        settlement_gas: list[int] = []
        settled_count = conflicts = 0
        for receipt_bundle_id, delivery in sorted(
            receipt_deliveries.items(), key=lambda item: (item[1].delivered_us, item[0])
        ):
            item = released_by_bundle[receipt_bundle_id]
            artifact = item["proof"]
            a, b, c, public = artifact.calldata
            if bytes(run_contracts.gateway.functions.proofDomain(public).call()) != bytes(domain):
                raise AssertionError("proof cache contains more than one campaign domain")
            nullifier = Web3.to_bytes(int(artifact.nullifier)).rjust(32, b"\x00")
            receipt_id = Web3.keccak(text=f"{run_id}|{item['event_id']}|receipt")
            site_address = site_addresses[item["site"]]
            authorization_evidence_hash = Web3.to_bytes(
                hexstr=f"0x{item['authorization_evidence_hash']}"
            )
            bound_authorization = run_contracts.gateway.functions.authorizationCommitment(
                public,
                receipt_id,
                site_address,
                authorization_evidence_hash,
            ).call()
            payload = run_contracts.gateway.functions.receiptPayload(
                domain,
                run_contracts.gateway.functions.proofStatementHash(public).call(),
                receipt_id,
                site_address,
                bound_authorization,
            ).call()
            signature = evm.sign_receipt(site_address, bytes(payload))
            before = run_contracts.settlement.functions.winningReceipt(
                domain, nullifier
            ).call()
            started_ns = time.perf_counter_ns()
            tx = run_contracts.gateway.functions.verifyAndSettle(
                a,
                b,
                c,
                public,
                (
                    receipt_id,
                    site_address,
                    authorization_evidence_hash,
                    signature,
                ),
            ).transact({"from": evm.relayer, "gas": EVM_TX_GAS})
            tx_receipt = _wait_success(
                evm.web3, tx, label=f"{run_id}-verify-and-settle"
            )
            evm_wall_ns = time.perf_counter_ns() - started_ns
            after = run_contracts.settlement.functions.winningReceipt(domain, nullifier).call()
            won = before == bytes(32) and after == receipt_id
            if won:
                recorded_authorization = run_contracts.settlement.functions.winningAuthorization(
                    domain, nullifier
                ).call()
                if recorded_authorization != bound_authorization:
                    raise AssertionError("settlement authorization commitment mismatch")
            if won:
                settled_count += 1
                oracle_winner[artifact.nullifier] = Web3.to_hex(receipt_id)
                oracle_credit[item["site"]] += registry.value
                outcome = "settled"
            else:
                conflicts += 1
                outcome = "conflict"
            settlement_gas.append(int(tx_receipt.gasUsed))
            _event(
                events,
                run_id=run_id,
                event_id=item["event_id"],
                trace_us=delivery.delivered_us,
                component="local-evm",
                action="verifyAndSettle",
                outcome=outcome,
                tx_hash=Web3.to_hex(tx),
                gas_used=int(tx_receipt.gasUsed),
                evm_wall_ns=evm_wall_ns,
                receipt_id=Web3.to_hex(receipt_id),
                authorization_evidence_hash=Web3.to_hex(authorization_evidence_hash),
                authorization_commitment=Web3.to_hex(bound_authorization),
                site=item["site"],
                circuit_nullifier=artifact.nullifier,
            )
        credits_match = all(
            run_contracts.settlement.functions.credits(domain, site_addresses[site]).call()
            == oracle_credit[site]
            for site in sites
        )
        escrow_after = int(run_contracts.settlement.functions.campaigns(domain).call()[3])
        expected_escrow = funding - sum(oracle_credit.values())
        risk_snapshot = ledger.snapshot()
        physical_by_nullifier = Counter(item["proof"].nullifier for item in released)
        duplicate_releases = sum(max(0, count - 1) for count in physical_by_nullifier.values())
        summaries = {
            "schema_version": 1,
            "status": "complete",
            "registry_id": registry.registry_id,
            "run_id": run_id,
            "workload_fingerprint": workload_fingerprint,
            "implementation_hash": implementation_hash,
            "attempt_id": attempt_id,
            "context": context,
            "seed": seed,
            "cap": cap,
            "method": method,
            "witness_regime": regime,
            "generated_requests": len(requests),
            "request_network_delivered": len(request_deliveries),
            "physical_releases": len(released),
            "duplicate_physical_releases": duplicate_releases,
            "receipt_network_delivered": len(receipt_deliveries),
            "evm_settled": settled_count,
            "evm_conflicts": conflicts,
            "completion_rate": settled_count / max(1, len(requests)),
            "physical_release_rate": len(released) / max(1, len(requests)),
            "mean_settlement_gas": (
                sum(settlement_gas) / len(settlement_gas) if settlement_gas else None
            ),
            "max_settlement_gas": max(settlement_gas) if settlement_gas else None,
            "deployment_gas": run_contracts.deployment_gas,
            "register_gas": register_gas,
            "fund_gas": fund_gas,
            "risk_issued": risk_snapshot.issued,
            "risk_remaining": risk_snapshot.remaining,
            "risk_in_transit": risk_snapshot.in_transit,
            "risk_spent": risk_snapshot.spent,
            "risk_expired": risk_snapshot.expired,
            "risk_revoked": risk_snapshot.revoked,
            "risk_conserved": risk_snapshot.conserved,
            "cap_violation": max(0, risk_snapshot.spent - cap * registry.value),
            "python_evm_credits_match": credits_match,
            "python_evm_escrow_match": escrow_after == expected_escrow,
            "evm_escrow_after": escrow_after,
            "expected_evm_escrow_after": expected_escrow,
            "event_rows": len(events),
            "event_checksum": stable_hash(events, domain="aidmesh/e2e/events/v1"),
            "receipt_event_checksum": receipt_result.event_checksum,
            "gateway_node": gateway_node,
            "local_evm_profile": "eth-tester/py-evm; gas and state, not public-chain latency",
            "srs_profile": "Groth16 development zkey under stated SRS assumption",
            "domain_context": expected_domain_context,
            "evm_claim_boundary": (
                "proof- and authorization-commitment-gated accounting; exact path "
                "evidence is bound but remains off-chain validated"
            ),
        }
        if not (
            risk_snapshot.conserved
            and summaries["cap_violation"] == 0
            and credits_match
            and summaries["python_evm_escrow_match"]
        ):
            raise AssertionError(f"integrated invariant failed: {summaries}")
        _write_jsonl(events_path, events)
        _write_json(summary_path, summaries)
        return summaries
    finally:
        for store in stores:
            store.close()
        evm.revert(evm_snapshot)


def run_local_e2e_campaign(
    root: Path,
    *,
    registry: E2ERegistry,
    trace_root: Path,
    output: Path,
    smoke_vouchers: int | None = None,
) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    implementation = _implementation_manifest(root)
    implementation_hash = str(implementation["implementation_hash"])
    evm = LocalEVMHarness(root)
    summaries: list[dict[str, Any]] = []
    trace_manifest: dict[str, Any] = {}
    first_artifact: ProofArtifact | None = None
    for context in registry.contexts:
        contacts_path = trace_root / context / "contacts.csv"
        contacts = load_contacts_csv(contacts_path)
        start_us = min(contact.start_us for contact in contacts)
        end_us = max(contact.end_us for contact in contacts)
        cutoff_us = start_us + round(
            (end_us - start_us) * registry.calibration_fraction
        )
        roles = _calibration_roles(contacts, cutoff_us)
        trace_manifest[context] = {
            "contacts": len(contacts),
            "contacts_sha256": _sha256(contacts_path),
            "cutoff_us": cutoff_us,
            "gateway": roles["relays"][0],
            "roles": {key: list(value) for key, value in roles.items()},
            "seed_workloads": {},
        }
        for seed in registry.seeds:
            requests = generate_heldout_requests(
                contacts,
                roles,
                context=context,
                seed=seed,
                cutoff_us=cutoff_us,
            )
            full_voucher_keys = tuple(sorted({request.nullifier for request in requests}))
            voucher_limit = (
                smoke_vouchers
                if smoke_vouchers is not None
                else registry.voucher_sample_per_context
            )
            voucher_keys = _registered_voucher_sample(
                full_voucher_keys,
                context=context,
                seed=seed,
                limit=voucher_limit,
            )
            allowed = set(voucher_keys)
            requests = tuple(request for request in requests if request.nullifier in allowed)
            request_result = TemporalContactSimulator(
                contacts=contacts,
                bundles=tuple(request.bundle for request in requests),
                policy=RoutingPolicy.EPIDEMIC,
                buffer_bytes=registry.buffer_bytes,
                copy_budget=registry.copy_budget,
            ).run()
            request_deliveries = {
                delivery.bundle_id: delivery.delivered_us
                for delivery in request_result.deliveries
            }
            caps = sorted(
                {
                    max(len(roles["sites"]), round(len(requests) * fraction))
                    for fraction in registry.cap_fractions
                }
            )
            trace_manifest[context]["seed_workloads"][str(seed)] = {
                "eligible_logical_vouchers": len(full_voucher_keys),
                "sampled_logical_vouchers": len(voucher_keys),
                "sampled_request_rows_including_replays": len(requests),
                "sample_hash": stable_hash(
                    voucher_keys, domain="aidmesh/e2e/voucher-sample-manifest/v1"
                ),
                "caps_after_rounding_and_deduplication": caps,
                "domain_contexts": {},
            }
            for cap in caps:
                header = _build_header(
                    campaign_id=_numeric_campaign_id(registry.registry_id, context, seed),
                    sites=roles["sites"],
                    cap=cap,
                    start_us=start_us,
                    end_us=end_us,
                )
                domain_context = _proof_domain_context(
                    evm=evm, header=header, registry=registry, cap=cap
                )
                proofs = prepare_proof_cache(
                    root,
                    registry=registry,
                    context=context,
                    seed=seed,
                    domain_context=domain_context,
                    voucher_keys=voucher_keys,
                    output=output,
                )
                trace_manifest[context]["seed_workloads"][str(seed)][
                    "domain_contexts"
                ][str(cap)] = domain_context
                if first_artifact is None:
                    first_artifact = next(iter(proofs.values()))
                for method in registry.methods:
                    for regime in registry.witness_regimes:
                        summaries.append(
                            _run_one(
                                registry=registry,
                                context=context,
                                seed=seed,
                                cap=cap,
                                method=method,
                                regime=regime,
                                contacts=contacts,
                                roles=roles,
                                requests=requests,
                                request_deliveries=request_deliveries,
                                proofs=proofs,
                                evm=evm,
                                output=output,
                                implementation_hash=implementation_hash,
                            )
                        )
    if first_artifact is None:
        raise AssertionError("campaign produced no proof artifacts")
    fault_snapshot = evm.snapshot()
    try:
        fault_summary = run_e2e_fault_controls(
            evm, first_artifact, output / "fault_controls"
        )
    finally:
        evm.revert(fault_snapshot)
    final_implementation = _implementation_manifest(root)
    if final_implementation["implementation_hash"] != implementation_hash:
        raise AssertionError("implementation changed while e2e campaign was running")
    frame = pd.DataFrame(summaries)
    frame.to_csv(output / "e2e_run_summaries.csv", index=False, lineterminator="\n")
    manifest = {
        "schema_version": 1,
        "status": "validated",
        "registry": asdict(registry),
        "registry_hash": stable_hash(
            asdict(registry), domain="aidmesh/e2e/registry/v1"
        ),
        "implementation": implementation,
        "environment": _environment_manifest(root),
        "smoke_vouchers": smoke_vouchers,
        "workload_profile": (
            "cli-smoke-override"
            if smoke_vouchers is not None
            else "registered-laptop-confirmatory"
        ),
        "runs": len(summaries),
        "all_risk_conserved": bool(frame["risk_conserved"].all()),
        "all_cap_safe": bool((frame["cap_violation"] == 0).all()),
        "all_python_evm_credit_match": bool(frame["python_evm_credits_match"].all()),
        "all_python_evm_escrow_match": bool(frame["python_evm_escrow_match"].all()),
        "verifier_deployment_gas": evm.verifier_deployment_gas,
        "fault_controls": fault_summary,
        "traces": trace_manifest,
        "limitations": [
            "Contact traces are public proximity histories with deterministic overlays.",
            "Py-EVM validates bytecode/gas/state, not public-chain confirmation latency.",
            "Risk-right transfer routing uses exact slot-causal paths without queue contention.",
            "Witness availability is a registered fault schedule, not inferred from proximity.",
            "The development Groth16 zkey does not establish production ceremony trust.",
        ],
    }
    _write_json(output / "e2e_campaign_manifest.json", manifest)
    return manifest


def analyze_local_e2e_campaign(input_root: Path, output: Path) -> dict[str, Path]:
    import matplotlib.pyplot as plt
    import numpy as np

    frame = pd.read_csv(input_root / "e2e_run_summaries.csv")
    output.mkdir(parents=True, exist_ok=True)
    frame["request_delivery_rate"] = frame["request_network_delivered"] / frame[
        "generated_requests"
    ].clip(lower=1)
    frame["receipt_delivery_rate"] = frame["receipt_network_delivered"] / frame[
        "generated_requests"
    ].clip(lower=1)
    grouped = (
        frame.groupby(["context", "method", "witness_regime"], as_index=False)
        .agg(
            runs=("run_id", "count"),
            completion_mean=("completion_rate", "mean"),
            physical_release_mean=("physical_release_rate", "mean"),
            duplicate_release_mean=("duplicate_physical_releases", "mean"),
            receipt_delivery_mean=("receipt_delivery_rate", "mean"),
            settlement_gas_mean=("mean_settlement_gas", "mean"),
            settlement_gas_max=("max_settlement_gas", "max"),
            cap_violations=("cap_violation", "sum"),
        )
        .sort_values(["context", "witness_regime", "method"])
    )
    summary_path = output / "e2e_grouped_results.csv"
    grouped.to_csv(summary_path, index=False, lineterminator="\n")

    paired_rows: list[dict[str, Any]] = []
    rng = np.random.default_rng(20260718)
    metrics = (
        "completion_rate",
        "physical_release_rate",
        "receipt_delivery_rate",
        "duplicate_physical_releases",
        "mean_settlement_gas",
    )
    for regime in sorted(frame["witness_regime"].unique()):
        regime_frame = frame[frame["witness_regime"] == regime]
        for metric in metrics:
            pivot = regime_frame.pivot_table(
                index=["context", "seed", "cap"],
                columns="method",
                values=metric,
                aggfunc="mean",
            ).reindex(columns=["static_rights", "transferable_rights"])
            pivot = pivot.dropna(subset=["static_rights", "transferable_rights"])
            pivot["delta"] = pivot["transferable_rights"] - pivot["static_rights"]
            cluster = pivot.reset_index().groupby(
                ["context", "seed"], as_index=False
            )["delta"].mean()
            bootstrap: list[float] = []
            contexts = sorted(cluster["context"].unique())
            for _ in range(5000 if contexts else 0):
                values: list[float] = []
                for sampled_context in rng.choice(contexts, size=len(contexts), replace=True):
                    available = cluster[cluster["context"] == sampled_context]
                    seeds = available["seed"].unique()
                    for sampled_seed in rng.choice(seeds, size=len(seeds), replace=True):
                        values.append(
                            float(
                                available[available["seed"] == sampled_seed]["delta"].iloc[
                                    0
                                ]
                            )
                        )
                bootstrap.append(float(np.mean(values)))
            paired_rows.append(
                {
                    "witness_regime": regime,
                    "metric": metric,
                    "estimand": "transferable_minus_static",
                    "clusters": len(cluster),
                    "estimate": (
                        float(cluster["delta"].mean()) if len(cluster) else math.nan
                    ),
                    "ci95_low": (
                        float(np.quantile(bootstrap, 0.025)) if bootstrap else math.nan
                    ),
                    "ci95_high": (
                        float(np.quantile(bootstrap, 0.975)) if bootstrap else math.nan
                    ),
                    "bootstrap_replicates": len(bootstrap),
                    "bootstrap_seed": 20260718,
                }
            )
    paired_path = output / "e2e_paired_hierarchical_bootstrap.csv"
    pd.DataFrame(paired_rows).to_csv(paired_path, index=False, lineterminator="\n")

    selected_run_ids = set(frame["run_id"].astype(str))
    transaction_rows: list[dict[str, Any]] = []
    for path in sorted((input_root / "runs").rglob("events.jsonl")):
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                event = json.loads(line)
                if (
                    event.get("run_id") in selected_run_ids
                    and event.get("component") == "local-evm"
                    and event.get("action") == "verifyAndSettle"
                ):
                    transaction_rows.append(event)
    transactions = pd.DataFrame(transaction_rows)
    quantile_rows: list[dict[str, Any]] = []
    if not transactions.empty:
        run_dimensions = frame.set_index("run_id")[
            ["context", "method", "witness_regime"]
        ]
        transactions = transactions.join(run_dimensions, on="run_id")
        for dimensions, values in transactions.groupby(
            ["context", "method", "witness_regime"], dropna=False
        ):
            for metric in ("gas_used", "evm_wall_ns"):
                numeric = pd.to_numeric(values[metric], errors="coerce").dropna()
                quantile_rows.append(
                    {
                        "context": dimensions[0],
                        "method": dimensions[1],
                        "witness_regime": dimensions[2],
                        "metric": metric,
                        "n": len(numeric),
                        "p50": float(numeric.quantile(0.50)),
                        "p95": float(numeric.quantile(0.95)),
                        "p99": float(numeric.quantile(0.99)),
                    }
                )
    transaction_path = output / "e2e_transaction_quantiles.csv"
    pd.DataFrame(quantile_rows).to_csv(
        transaction_path, index=False, lineterminator="\n"
    )

    proof_rows: list[dict[str, Any]] = []
    for meta_path in sorted((input_root / "proofs").rglob("meta.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        calldata_path = meta_path.with_name("calldata.txt")
        proof_rows.append(
            {
                "mode": meta["mode"],
                "proof_ms": float(meta["proof_ms"]),
                "calldata_bytes": calldata_path.stat().st_size,
            }
        )
    proofs = pd.DataFrame(proof_rows)
    proof_quantile_rows: list[dict[str, Any]] = []
    for mode, values in proofs.groupby("mode"):
        for metric in ("proof_ms", "calldata_bytes"):
            numeric = values[metric]
            proof_quantile_rows.append(
                {
                    "mode": mode,
                    "metric": metric,
                    "n": len(numeric),
                    "p50": float(numeric.quantile(0.50)),
                    "p95": float(numeric.quantile(0.95)),
                    "p99": float(numeric.quantile(0.99)),
                }
            )
    proof_path = output / "e2e_proof_quantiles.csv"
    pd.DataFrame(proof_quantile_rows).to_csv(
        proof_path, index=False, lineterminator="\n"
    )

    pdf_root = output / "pdf"
    pdf_root.mkdir(parents=True, exist_ok=True)
    colors = {
        ("static_rights", "quorum"): "#4C78A8",
        ("transferable_rights", "quorum"): "#59A14F",
        ("static_rights", "fallback"): "#E45756",
        ("transferable_rights", "fallback"): "#F28E2B",
    }
    labels = {
        ("static_rights", "quorum"): "Static - quorum",
        ("transferable_rights", "quorum"): "Transferable - quorum",
        ("static_rights", "fallback"): "Static - fallback",
        ("transferable_rights", "fallback"): "Transferable - fallback",
    }
    stages = [
        ("request_delivery_rate", "Request"),
        ("physical_release_rate", "Release"),
        ("receipt_delivery_rate", "Receipt"),
        ("completion_rate", "Settlement"),
    ]
    contexts = sorted(frame["context"].unique())
    fig, axes_grid = plt.subplots(
        1, len(contexts), figsize=(3.45 * len(contexts), 3.5), sharey=True, squeeze=False
    )
    axes = axes_grid[0]
    for axis, context in zip(axes, contexts, strict=True):
        context_frame = frame[frame["context"] == context]
        for key, color in colors.items():
            values = context_frame[
                (context_frame["method"] == key[0])
                & (context_frame["witness_regime"] == key[1])
            ]
            axis.plot(
                range(len(stages)),
                [float(values[column].mean()) for column, _ in stages],
                marker="o",
                linewidth=1.8,
                color=color,
                label=labels[key],
            )
        axis.set_title(context)
        axis.set_xticks(range(len(stages)), [label for _, label in stages], rotation=25)
        axis.set_ylim(-0.03, 1.03)
        axis.grid(axis="y", alpha=0.25)
    axes[0].set_ylabel("Fraction of generated requests")
    handles, legend_labels = axes[-1].get_legend_handles_labels()
    fig.legend(
        handles,
        legend_labels,
        loc="upper center",
        bbox_to_anchor=(0.5, 0.995),
        ncol=4,
        frameon=False,
        fontsize=8,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.85))
    pipeline_pdf = pdf_root / "e2e_pipeline.pdf"
    fig.savefig(pipeline_pdf, bbox_inches="tight")
    plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(8.2, 3.2))
    aggregated = frame.groupby(["method", "witness_regime"], as_index=False).agg(
        completion=("completion_rate", "mean"),
        duplicates=("duplicate_physical_releases", "mean"),
    )
    for row in aggregated.itertuples(index=False):
        key = (row.method, row.witness_regime)
        is_static = row.method == "static_rights"
        axes[0].scatter(
            row.duplicates,
            row.completion,
            s=62,
            facecolors="none" if is_static else colors[key],
            edgecolors=colors[key],
            linewidths=2 if is_static else 1,
            marker="o" if is_static else "s",
            zorder=3 if is_static else 2,
            label=labels[key],
        )
    axes[0].set_xlabel("Mean duplicate physical releases per run")
    axes[0].set_ylabel("Settlement completion")
    axes[0].grid(alpha=0.25)
    if not transactions.empty:
        for outcome, values in transactions.groupby("outcome"):
            gas = np.sort(pd.to_numeric(values["gas_used"]).to_numpy())
            axes[1].step(gas, np.arange(1, len(gas) + 1) / len(gas), label=outcome)
    axes[1].set_xlabel("verifyAndSettle gas")
    axes[1].set_ylabel("Empirical CDF")
    axes[1].grid(alpha=0.25)
    if not transactions.empty:
        axes[1].legend(frameon=False)
    handles, legend_labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, legend_labels, loc="upper center", ncol=2, frameon=False)
    fig.tight_layout(rect=(0, 0, 1, 0.84))
    safety_pdf = pdf_root / "e2e_safety_cost.pdf"
    fig.savefig(safety_pdf, bbox_inches="tight")
    plt.close(fig)
    invariant_path = output / "e2e_invariants.json"
    invariants = {
        "runs": len(frame),
        "all_risk_conserved": bool(frame["risk_conserved"].all()),
        "cap_violations": int(frame["cap_violation"].sum()),
        "credit_mismatches": int((~frame["python_evm_credits_match"]).sum()),
        "escrow_mismatches": int((~frame["python_evm_escrow_match"]).sum()),
        "settled": int(frame["evm_settled"].sum()),
        "conflicts": int(frame["evm_conflicts"].sum()),
        "physical_duplicate_releases": int(frame["duplicate_physical_releases"].sum()),
    }
    _write_json(invariant_path, invariants)
    return {
        "grouped": summary_path,
        "paired_bootstrap": paired_path,
        "transaction_quantiles": transaction_path,
        "proof_quantiles": proof_path,
        "pipeline_pdf": pipeline_pdf,
        "safety_pdf": safety_pdf,
        "invariants": invariant_path,
    }


def write_e2e_checksums(root: Path) -> Path:
    excluded_suffixes = (".building", ".log", ".sqlite-shm", ".sqlite-wal", ".tmp")
    paths = sorted(
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.name != "CHECKSUMS.sha256"
        and not path.name.endswith(excluded_suffixes)
    )
    checksum_path = root / "CHECKSUMS.sha256"
    with checksum_path.open("w", encoding="ascii", newline="\n") as handle:
        for path in paths:
            handle.write(f"{_sha256(path)}  {path.relative_to(root).as_posix()}\n")
    return checksum_path


def verify_e2e_checksums(root: Path) -> dict[str, Any]:
    checksum_path = root / "CHECKSUMS.sha256"
    if not checksum_path.exists():
        raise FileNotFoundError(checksum_path)
    checked = 0
    failures: list[dict[str, str]] = []
    resolved_root = root.resolve()
    for line_number, line in enumerate(
        checksum_path.read_text(encoding="ascii").splitlines(), start=1
    ):
        try:
            expected, relative = line.split("  ", 1)
        except ValueError:
            failures.append(
                {"path": f"line-{line_number}", "error": "malformed checksum row"}
            )
            continue
        candidate = (root / relative).resolve()
        if resolved_root not in candidate.parents:
            failures.append({"path": relative, "error": "path escapes artifact root"})
            continue
        if not candidate.is_file():
            failures.append({"path": relative, "error": "missing"})
            continue
        observed = _sha256(candidate)
        checked += 1
        if observed != expected:
            failures.append(
                {
                    "path": relative,
                    "error": "sha256 mismatch",
                    "expected": expected,
                    "observed": observed,
                }
            )
    return {
        "schema_version": 1,
        "status": "valid" if not failures else "invalid",
        "checked": checked,
        "failures": failures,
    }
