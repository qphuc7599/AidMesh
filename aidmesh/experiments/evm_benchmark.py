"""Controlled local-EVM benchmark for the P2 verifier-adapter contract."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pandas as pd
from web3 import Web3
from web3.providers.eth_tester import EthereumTesterProvider

from aidmesh.experiments.solidity import solc_command


def compile_contract(root: Path) -> tuple[list[dict], str]:
    contract = root / "contracts" / "src" / "AidMeshSettlement.sol"
    completed = subprocess.run(
        [
            solc_command(root),
            "--optimize",
            "--via-ir",
            "--evm-version",
            "prague",
            "--combined-json",
            "abi,bin",
            str(contract),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    output = json.loads(completed.stdout)
    artifact = next(iter(output["contracts"].values()))
    abi = artifact["abi"] if isinstance(artifact["abi"], list) else json.loads(artifact["abi"])
    return abi, artifact["bin"]


def run_local_evm_benchmark(
    root: Path,
    *,
    batch_sizes: tuple[int, ...] = (1, 10, 25, 50, 100),
) -> pd.DataFrame:
    abi, bytecode = compile_contract(root)
    web3 = Web3(EthereumTesterProvider())
    owner, adapter, site = web3.eth.accounts[:3]
    factory = web3.eth.contract(abi=abi, bytecode=bytecode)
    deployment_hash = factory.constructor(adapter).transact({"from": owner})
    deployment_receipt = web3.eth.wait_for_transaction_receipt(deployment_hash)
    contract = web3.eth.contract(address=deployment_receipt.contractAddress, abi=abi)
    rows = [
        {
            "case": "deployment",
            "batch_size": 0,
            "gas_total": deployment_receipt.gasUsed,
            "gas_per_receipt": None,
            "profile": "P2-verifier-adapter",
        }
    ]
    for batch_size in batch_sizes:
        domain = Web3.keccak(text=f"domain-{batch_size}")
        header_hash = int.from_bytes(Web3.keccak(text=f"header-{batch_size}"), "big")
        policy_hash = int.from_bytes(Web3.keccak(text=f"policy-{batch_size}"), "big")
        committee_hash = int.from_bytes(Web3.keccak(text=f"committee-{batch_size}"), "big")
        transaction = contract.functions.registerCampaign(
            domain, header_hash, policy_hash, committee_hash, [site]
        ).transact({"from": owner})
        web3.eth.wait_for_transaction_receipt(transaction)
        transaction = contract.functions.fundCampaign(domain, batch_size * 2).transact(
            {"from": owner}
        )
        web3.eth.wait_for_transaction_receipt(transaction)
        nullifiers = [Web3.keccak(text=f"n-{batch_size}-{index}") for index in range(batch_size)]
        receipts = [Web3.keccak(text=f"r-{batch_size}-{index}") for index in range(batch_size)]
        items = [
            (
                domain,
                nullifiers[index],
                receipts[index],
                site,
                1,
                Web3.keccak(text=f"authorization-{batch_size}-{index}"),
            )
            for index in range(batch_size)
        ]
        transaction = contract.functions.settleVerifiedBatch(items).transact({"from": adapter})
        receipt = web3.eth.wait_for_transaction_receipt(transaction)
        rows.append(
            {
                "case": "fresh_batch",
                "batch_size": batch_size,
                "gas_total": receipt.gasUsed,
                "gas_per_receipt": receipt.gasUsed / batch_size,
                "profile": "P2-verifier-adapter",
            }
        )
    return pd.DataFrame(rows)


def write_evm_benchmark(frame: pd.DataFrame, output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output, index=False)
    return output
