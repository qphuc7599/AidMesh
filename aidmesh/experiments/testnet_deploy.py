"""Public-testnet deployment helper for final manual validation.

The deployment order is part of the P1-v2 statement domain:

    verifier -> gateway(verifier) -> settlement(gateway) -> gateway.setSettlement

The helper therefore deploys the complete proof-gated accounting stack.  It does
not accept an externally supplied adapter because doing so would bypass the
gateway that binds the chain, contract addresses, policy and receipt signature.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from aidmesh.experiments.solidity import solc_command


@dataclass(frozen=True)
class CompiledContract:
    name: str
    abi: list[dict[str, Any]]
    bytecode: str
    bytecode_bytes: int
    source_sha256: str


@dataclass(frozen=True)
class DeploymentRecord:
    name: str
    address: str | None
    transaction_hash: str | None
    gas_used: int | None
    block_number: int | None
    receipt_status: int | None
    explorer_url: str | None
    bytecode_bytes: int
    block_hash: str | None = None
    block_timestamp: int | None = None
    effective_gas_price_wei: int | None = None
    transaction_cost_wei: int | None = None
    runtime_code_sha256: str | None = None


def _compile_one(root: Path, contract_path: Path, contract_name: str) -> CompiledContract:
    completed = subprocess.run(
        [
            solc_command(root),
            "--optimize",
            "--evm-version",
            "prague",
            "--combined-json",
            "abi,bin",
            str(contract_path),
        ],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    output = json.loads(completed.stdout)
    key, artifact = next(
        (key, value)
        for key, value in output["contracts"].items()
        if key.endswith(f":{contract_name}")
    )
    abi = artifact["abi"] if isinstance(artifact["abi"], list) else json.loads(artifact["abi"])
    bytecode = artifact["bin"]
    return CompiledContract(
        name=key.rsplit(":", 1)[-1],
        abi=abi,
        bytecode=bytecode,
        bytecode_bytes=len(bytecode) // 2,
        source_sha256=hashlib.sha256(contract_path.read_bytes()).hexdigest(),
    )


def compile_testnet_contracts(root: Path) -> dict[str, CompiledContract]:
    contracts = [
        (
            root / "contracts" / "src" / "AidMeshGroth16Verifier.sol",
            "Groth16Verifier",
        ),
        (
            root / "contracts" / "src" / "AidMeshVerifiedSettlementGateway.sol",
            "AidMeshVerifiedSettlementGateway",
        ),
        (
            root / "contracts" / "src" / "AidMeshSettlement.sol",
            "AidMeshSettlement",
        ),
    ]
    compiled = [_compile_one(root, path, name) for path, name in contracts]
    return {contract.name: contract for contract in compiled}


def run_testnet_deploy(
    root: Path,
    *,
    rpc_url: str | None,
    private_key: str | None,
    output: Path,
    dry_run: bool,
    settlement_adapter: str | None = None,
) -> Path:
    if settlement_adapter is not None:
        raise ValueError(
            "custom settlement adapter is unsafe for P1-v2; deploy the canonical gateway"
        )
    compiled = compile_testnet_contracts(root)
    records: list[DeploymentRecord] = []
    manifest: dict[str, Any] = {
        "dry_run": dry_run,
        "target_network": "sepolia",
        "expected_chain_id": 11155111,
        "compiler_settings": {
            "optimizer": True,
            "optimizer_runs": 200,
            "evm_version": "prague",
        },
        "solc_version": subprocess.run(
            [solc_command(root), "--version"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()[-1],
        "contracts": {},
        "deployments": [],
    }
    for name, contract in sorted(compiled.items()):
        manifest["contracts"][name] = {
            "bytecode_bytes": contract.bytecode_bytes,
            "abi_entries": len(contract.abi),
            "source_sha256": contract.source_sha256,
            "bytecode_sha256": hashlib.sha256(bytes.fromhex(contract.bytecode)).hexdigest(),
        }

    if dry_run:
        for name, contract in sorted(compiled.items()):
            records.append(
                DeploymentRecord(
                    name=name,
                    address=None,
                    transaction_hash=None,
                    gas_used=None,
                    block_number=None,
                    receipt_status=None,
                    explorer_url=None,
                    bytecode_bytes=contract.bytecode_bytes,
                )
            )
    else:
        if not rpc_url or not private_key:
            raise ValueError("rpc_url and private_key are required unless dry_run=True")
        from web3 import Web3

        web3 = Web3(Web3.HTTPProvider(rpc_url))
        if not web3.is_connected():
            raise ConnectionError("cannot connect to RPC URL")
        account = web3.eth.account.from_key(private_key)
        chain_id = web3.eth.chain_id
        if chain_id != 11155111:
            raise ValueError(
                f"refusing deployment: expected Sepolia chain ID 11155111, got {chain_id}"
            )
        manifest["chain_id"] = chain_id
        manifest["deployer_address"] = account.address
        manifest["deployer_balance_wei_before"] = int(web3.eth.get_balance(account.address))
        nonce = web3.eth.get_transaction_count(account.address)

        def fee_fields() -> dict[str, int]:
            latest = web3.eth.get_block("latest")
            try:
                priority = int(web3.eth.max_priority_fee)
            except Exception:
                priority = 1_000_000_000
            base_fee = int(latest.get("baseFeePerGas", web3.eth.gas_price))
            return {
                "maxPriorityFeePerGas": priority,
                "maxFeePerGas": 2 * base_fee + priority,
            }

        def receipt_record(
            name: str,
            contract: CompiledContract,
            tx_hash: Any,
            receipt: Any,
            *,
            address: str | None,
        ) -> DeploymentRecord:
            block = web3.eth.get_block(receipt.blockNumber)
            effective_price = int(receipt.effectiveGasPrice)
            runtime_hash = None
            if address is not None:
                runtime_hash = hashlib.sha256(bytes(web3.eth.get_code(address))).hexdigest()
            return DeploymentRecord(
                name=name,
                address=address,
                transaction_hash=tx_hash.hex(),
                gas_used=int(receipt.gasUsed),
                block_number=int(receipt.blockNumber),
                receipt_status=int(receipt.status),
                explorer_url=f"https://sepolia.etherscan.io/tx/{tx_hash.hex()}",
                bytecode_bytes=contract.bytecode_bytes,
                block_hash=receipt.blockHash.hex(),
                block_timestamp=int(block.timestamp),
                effective_gas_price_wei=effective_price,
                transaction_cost_wei=int(receipt.gasUsed) * effective_price,
                runtime_code_sha256=runtime_hash,
            )

        def deploy(name: str, args: tuple[Any, ...] = ()) -> DeploymentRecord:
            nonlocal nonce
            contract = compiled[name]
            factory = web3.eth.contract(abi=contract.abi, bytecode=contract.bytecode)
            transaction = factory.constructor(*args).build_transaction(
                {
                    "from": account.address,
                    "nonce": nonce,
                    "chainId": chain_id,
                    **fee_fields(),
                }
            )
            transaction["gas"] = int(web3.eth.estimate_gas(transaction) * 1.2)
            signed = account.sign_transaction(transaction)
            tx_hash = web3.eth.send_raw_transaction(signed.raw_transaction)
            receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
            if int(receipt.status) != 1:
                raise RuntimeError(f"{name} deployment reverted: {tx_hash.hex()}")
            nonce += 1
            return receipt_record(
                name,
                contract,
                tx_hash,
                receipt,
                address=receipt.contractAddress,
            )

        verifier_record = deploy("Groth16Verifier")
        records.append(verifier_record)
        gateway_record = deploy(
            "AidMeshVerifiedSettlementGateway", (verifier_record.address,)
        )
        records.append(gateway_record)
        settlement_record = deploy("AidMeshSettlement", (gateway_record.address,))
        records.append(settlement_record)

        gateway_contract = compiled["AidMeshVerifiedSettlementGateway"]
        gateway = web3.eth.contract(
            address=gateway_record.address,
            abi=gateway_contract.abi,
        )
        link_transaction = gateway.functions.setSettlement(
            settlement_record.address
        ).build_transaction(
            {
                "from": account.address,
                "nonce": nonce,
                "chainId": chain_id,
                **fee_fields(),
            }
        )
        link_transaction["gas"] = int(web3.eth.estimate_gas(link_transaction) * 1.2)
        signed = account.sign_transaction(link_transaction)
        link_hash = web3.eth.send_raw_transaction(signed.raw_transaction)
        link_receipt = web3.eth.wait_for_transaction_receipt(link_hash)
        if int(link_receipt.status) != 1:
            raise RuntimeError(f"gateway settlement link reverted: {link_hash.hex()}")
        nonce += 1
        records.append(
            receipt_record(
                "GatewaySetSettlement",
                gateway_contract,
                link_hash,
                link_receipt,
                address=None,
            )
        )
        observed_settlement = gateway.functions.settlement().call()
        if Web3.to_checksum_address(observed_settlement) != Web3.to_checksum_address(
            settlement_record.address
        ):
            raise AssertionError("gateway settlement link does not match deployment")
        manifest["linked_context"] = {
            "verifier_address": verifier_record.address,
            "gateway_address": gateway_record.address,
            "settlement_address": settlement_record.address,
            "gateway_reports_settlement": observed_settlement,
        }
        manifest["deployer_balance_wei_after"] = int(web3.eth.get_balance(account.address))
        manifest["total_transaction_cost_wei"] = sum(
            record.transaction_cost_wei or 0 for record in records
        )

    manifest["deployments"] = [asdict(record) for record in records]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output


def run_testnet_deploy_from_env(
    root: Path,
    *,
    output: Path,
    dry_run: bool,
    rpc_env: str = "SEPOLIA_RPC_URL",
    key_env: str = "SEPOLIA_PRIVATE_KEY",
    settlement_adapter: str | None = None,
) -> Path:
    return run_testnet_deploy(
        root,
        rpc_url=os.environ.get(rpc_env),
        private_key=os.environ.get(key_env),
        output=output,
        dry_run=dry_run,
        settlement_adapter=settlement_adapter,
    )
