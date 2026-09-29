from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from eth_tester.exceptions import TransactionFailed
from web3 import Web3
from web3.providers.eth_tester import EthereumTesterProvider

from aidmesh.experiments.solidity import solc_command

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "contracts" / "src" / "AidMeshSettlement.sol"


def _field(label: str) -> int:
    return int.from_bytes(Web3.keccak(text=label), "big")


def _compile() -> tuple[list[dict], str]:
    completed = subprocess.run(
        [
            solc_command(ROOT),
            "--evm-version",
            "prague",
            "--combined-json",
            "abi,bin",
            str(CONTRACT),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    output = json.loads(completed.stdout)
    artifact = next(iter(output["contracts"].values()))
    abi = artifact["abi"] if isinstance(artifact["abi"], list) else json.loads(artifact["abi"])
    return abi, artifact["bin"]


@pytest.mark.evm
def test_adapter_contract_settles_at_most_once() -> None:
    abi, bytecode = _compile()
    web3 = Web3(EthereumTesterProvider())
    owner, adapter, site = web3.eth.accounts[:3]
    factory = web3.eth.contract(abi=abi, bytecode=bytecode)
    deployment = factory.constructor(adapter).transact({"from": owner})
    address = web3.eth.wait_for_transaction_receipt(deployment).contractAddress
    contract = web3.eth.contract(address=address, abi=abi)

    domain = Web3.keccak(text="domain")
    header = _field("header")
    policy = _field("policy")
    committee = _field("committee")
    nullifier = Web3.keccak(text="nullifier")
    first_receipt = Web3.keccak(text="receipt-1")
    second_receipt = Web3.keccak(text="receipt-2")
    first_authorization = Web3.keccak(text="authorization-1")
    second_authorization = Web3.keccak(text="authorization-2")
    contract.functions.registerCampaign(domain, header, policy, committee, [site]).transact(
        {"from": owner}
    )
    contract.functions.fundCampaign(domain, 10).transact({"from": owner})
    contract.functions.settleVerified(
        domain, nullifier, first_receipt, site, 3, first_authorization
    ).transact({"from": adapter})
    contract.functions.settleVerified(
        domain, nullifier, second_receipt, site, 3, second_authorization
    ).transact({"from": adapter})

    assert contract.functions.winningReceipt(domain, nullifier).call() == first_receipt
    assert (
        contract.functions.winningAuthorization(domain, nullifier).call()
        == first_authorization
    )
    assert contract.functions.credits(domain, site).call() == 3
    assert contract.functions.campaigns(domain).call()[3] == 7


@pytest.mark.evm
def test_only_adapter_can_settle() -> None:
    abi, bytecode = _compile()
    web3 = Web3(EthereumTesterProvider())
    owner, adapter, site, attacker = web3.eth.accounts[:4]
    factory = web3.eth.contract(abi=abi, bytecode=bytecode)
    deployment = factory.constructor(adapter).transact({"from": owner})
    address = web3.eth.wait_for_transaction_receipt(deployment).contractAddress
    contract = web3.eth.contract(address=address, abi=abi)
    domain = Web3.keccak(text="domain")
    contract.functions.registerCampaign(
        domain,
        _field("header"),
        _field("policy"),
        _field("committee"),
        [site],
    ).transact({"from": owner})
    contract.functions.fundCampaign(domain, 10).transact({"from": owner})
    with pytest.raises(TransactionFailed):
        contract.functions.settleVerified(
            domain,
            Web3.keccak(text="n"),
            Web3.keccak(text="r"),
            site,
            1,
            Web3.keccak(text="authorization"),
        ).transact({"from": attacker})


@pytest.mark.evm
def test_zero_authorization_commitment_is_rejected() -> None:
    abi, bytecode = _compile()
    web3 = Web3(EthereumTesterProvider())
    owner, adapter, site = web3.eth.accounts[:3]
    factory = web3.eth.contract(abi=abi, bytecode=bytecode)
    deployment = factory.constructor(adapter).transact({"from": owner})
    address = web3.eth.wait_for_transaction_receipt(deployment).contractAddress
    contract = web3.eth.contract(address=address, abi=abi)
    domain = Web3.keccak(text="domain")
    contract.functions.registerCampaign(
        domain,
        _field("header"),
        _field("policy"),
        _field("committee"),
        [site],
    ).transact({"from": owner})
    contract.functions.fundCampaign(domain, 10).transact({"from": owner})
    with pytest.raises(TransactionFailed):
        contract.functions.settleVerified(
            domain,
            Web3.keccak(text="n"),
            Web3.keccak(text="r"),
            site,
            1,
            bytes(32),
        ).transact({"from": adapter})
