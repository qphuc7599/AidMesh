from __future__ import annotations

import json
import subprocess
from functools import lru_cache
from pathlib import Path

import pytest
from eth_tester.exceptions import TransactionFailed
from hypothesis import given, settings
from hypothesis import strategies as st
from web3 import Web3
from web3.providers.eth_tester import EthereumTesterProvider

from aidmesh.experiments.solidity import solc_command

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "contracts" / "src" / "AidMeshSettlement.sol"


@lru_cache(maxsize=1)
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
    artifact = next(iter(json.loads(completed.stdout)["contracts"].values()))
    abi = artifact["abi"] if isinstance(artifact["abi"], list) else json.loads(artifact["abi"])
    return abi, artifact["bin"]


operation = st.tuples(
    st.integers(min_value=0, max_value=7),
    st.integers(min_value=0, max_value=31),
    st.integers(min_value=0, max_value=2),
    st.integers(min_value=1, max_value=3),
    st.integers(min_value=0, max_value=31),
    st.booleans(),
    st.booleans(),
)


@pytest.mark.evm
@settings(max_examples=30, stateful_step_count=40, deadline=None)
@given(operations=st.lists(operation, min_size=1, max_size=40))
def test_random_settlement_sequences_refine_first_valid_wins(
    operations: list[tuple[int, int, int, int, int, bool, bool]],
) -> None:
    abi, bytecode = _compile()
    web3 = Web3(EthereumTesterProvider())
    owner, adapter, attacker, *sites = web3.eth.accounts[:6]
    factory = web3.eth.contract(abi=abi, bytecode=bytecode)
    deployment = factory.constructor(adapter).transact({"from": owner})
    address = web3.eth.wait_for_transaction_receipt(deployment).contractAddress
    contract = web3.eth.contract(address=address, abi=abi)

    domain = Web3.keccak(text="stateful-domain")
    contract.functions.registerCampaign(
        domain,
        int.from_bytes(Web3.keccak(text="header"), "big"),
        int.from_bytes(Web3.keccak(text="policy"), "big"),
        int.from_bytes(Web3.keccak(text="committee"), "big"),
        sites,
    ).transact({"from": owner})
    initial_escrow = 100
    contract.functions.fundCampaign(domain, initial_escrow).transact({"from": owner})

    winners: dict[bytes, tuple[bytes, bytes, str, int]] = {}
    credits = {site: 0 for site in sites}
    escrow = initial_escrow
    for nullifier_id, receipt_id, site_id, value, auth_id, from_attacker, zero_auth in operations:
        nullifier = Web3.keccak(text=f"n-{nullifier_id}")
        receipt = Web3.keccak(text=f"r-{receipt_id}")
        site = sites[site_id % len(sites)]
        authorization = bytes(32) if zero_auth else Web3.keccak(text=f"a-{auth_id}")
        sender = attacker if from_attacker else adapter
        should_revert = from_attacker or zero_auth
        if should_revert:
            with pytest.raises(TransactionFailed):
                contract.functions.settleVerified(
                    domain,
                    nullifier,
                    receipt,
                    site,
                    value,
                    authorization,
                ).transact({"from": sender})
        else:
            contract.functions.settleVerified(
                domain,
                nullifier,
                receipt,
                site,
                value,
                authorization,
            ).transact({"from": sender})
            if nullifier not in winners:
                winners[nullifier] = (receipt, authorization, site, value)
                credits[site] += value
                escrow -= value

        assert contract.functions.campaigns(domain).call()[3] == escrow
        assert sum(contract.functions.credits(domain, item).call() for item in sites) == (
            initial_escrow - escrow
        )
        for key, (winner, bound_authorization, _, _) in winners.items():
            assert contract.functions.winningReceipt(domain, key).call() == winner
            assert (
                contract.functions.winningAuthorization(domain, key).call()
                == bound_authorization
            )
        for account, expected in credits.items():
            assert contract.functions.credits(domain, account).call() == expected
