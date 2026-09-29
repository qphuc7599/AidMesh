"""Time one fresh Geth replay of the exact final 100-record settlement workload.

The run starts from a fresh deterministic Geth development chain supplied by the
caller. It recreates the original deployment addresses and campaign/right state,
then submits the unchanged final BPv7 payload as ten validating stages and one
atomic finalizer. Setup transactions are measured separately from settlement.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

import rlp
from eth_utils import keccak
from web3 import Web3

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aidmesh.experiments.semantic_e2e_campaign import (  # noqa: E402
    _Harness,
    _register_domain,
)


def timed_receipt(harness: _Harness, function, gas: int) -> tuple[object, float]:
    start = time.perf_counter_ns()
    transaction = function.transact({"from": harness.owner, "gas": gas})
    receipt = harness.web3.eth.wait_for_transaction_receipt(transaction)
    elapsed_ms = (time.perf_counter_ns() - start) / 1_000_000
    if receipt.status != 1:
        raise AssertionError(f"transaction reverted: {receipt.transactionHash.hex()}")
    return receipt, elapsed_ms


def contract_nonce(owner: str, expected_address: str) -> int:
    owner_bytes = bytes.fromhex(owner[2:])
    expected = Web3.to_checksum_address(expected_address)
    for nonce in range(10_000):
        candidate = Web3.to_checksum_address(keccak(rlp.encode([owner_bytes, nonce]))[-20:])
        if candidate == expected:
            return nonce
    raise AssertionError("expected deployment address is outside the nonce search bound")


def advance_nonce(web3: Web3, owner: str, target: int) -> int:
    current = int(web3.eth.get_transaction_count(owner, "pending"))
    if current > target:
        raise AssertionError(f"fresh-chain nonce {current} already exceeds target {target}")
    sent = 0
    while current < target:
        stop = min(target, current + 50)
        last = None
        for nonce in range(current, stop):
            last = web3.eth.send_transaction(
                {"from": owner, "to": owner, "value": 0, "gas": 21_000, "nonce": nonce}
            )
            sent += 1
        if last is None:
            raise AssertionError("empty nonce-padding batch")
        receipt = web3.eth.wait_for_transaction_receipt(last)
        if receipt.status != 1:
            raise AssertionError("nonce-padding transaction reverted")
        current = stop
    return sent


def main() -> int:
    if len(sys.argv) != 6:
        raise SystemExit("usage: script SOURCE FINAL_RUN RPC VERIFIER_SOURCE OUTPUT")
    source = Path(sys.argv[1]).resolve()
    run = Path(sys.argv[2]).resolve()
    rpc = sys.argv[3]
    verifier_source = Path(sys.argv[4]).resolve()
    output = Path(sys.argv[5]).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    packets_path = run / "packets.json"
    received_path = run / "bpv7/received.json"
    received = received_path.read_bytes()
    if packets_path.read_bytes() != received:
        raise AssertionError("BPv7 payload differs from sent evidence")
    packets = json.loads(received)
    if len(packets) != 100:
        raise AssertionError("expected 100 final packets")
    items = [packet["item"] for packet in packets]
    publics = [item[3] for item in items]
    values = [int(public[4]) for public in publics]
    if sum(values) != 150 or len({int(public[7]) for public in publics}) != 100:
        raise AssertionError("unexpected final value/nullifier workload")

    expected_deployment = json.loads((source / "deployment.json").read_text(encoding="utf-8"))
    bootstrap = Web3(Web3.HTTPProvider(rpc))
    if not bootstrap.is_connected():
        raise AssertionError(f"cannot connect to fresh Geth RPC {rpc}")
    owner = bootstrap.eth.accounts[0]
    target_nonce = contract_nonce(owner, expected_deployment["verifier"])
    padding_transactions = advance_nonce(bootstrap, owner, target_nonce)

    setup_start = time.perf_counter_ns()
    harness = _Harness(ROOT, rpc_url=rpc, verifier_source=verifier_source)
    harness.witnesses = (*harness.accounts[1:4], harness.accounts[9])
    actual_deployment = {
        "verifier": harness.verifier.address,
        "gateway": harness.gateway.address,
        "settlement": harness.settlement.address,
    }
    if any(
        Web3.to_checksum_address(actual_deployment[key])
        != Web3.to_checksum_address(expected_deployment[key])
        for key in actual_deployment
    ):
        raise AssertionError((actual_deployment, expected_deployment))

    gateway = harness.gateway.functions
    sites = harness.sites[:2]
    domain = _register_domain(
        harness,
        public=list(map(int, publics[0])),
        threshold=3,
        cap=sum(values),
        initial_funding=0,
    )
    cap = 2**24
    timed_receipt(harness, harness.settlement.functions.fundCampaign(domain, 150), cap)
    timed_receipt(harness, gateway.enableRecovery(domain, 1), cap)
    for item in items:
        certificate = item[5]
        timed_receipt(
            harness,
            gateway.issueRight(domain, certificate[0], certificate[2], int(item[3][4])),
            cap,
        )
    batch_id = Web3.keccak(b"aidmesh/staged-atomic/v1" + received)
    timed_receipt(
        harness,
        gateway.openRecoveryBatch(
            batch_id,
            domain,
            100,
            sum(values),
            int(harness.web3.eth.block_number) + 1_000,
        ),
        cap,
    )
    setup_ms = (time.perf_counter_ns() - setup_start) / 1_000_000

    stage_rows = []
    for offset in range(0, 100, 10):
        function = gateway.stageRecoveryBatch(batch_id, items[offset : offset + 10])
        calldata_bytes = len(bytes.fromhex(function._encode_transaction_data()[2:]))
        receipt, elapsed_ms = timed_receipt(harness, function, cap)
        stage_rows.append(
            {
                "offset": offset,
                "count": 10,
                "wallMs": elapsed_ms,
                "gasUsed": int(receipt.gasUsed),
                "calldataBytes": calldata_bytes,
                "blockNumber": int(receipt.blockNumber),
            }
        )
        if [harness.settlement.functions.credits(domain, site).call() for site in sites] != [0, 0]:
            raise AssertionError("stage changed settlement credits")

    compact_records = [
        (
            "0x" + domain.hex(),
            item[5][0],
            item[5][1],
            int(item[3][7]).to_bytes(32, "big"),
            item[4][0],
            item[4][1],
            item[3][4],
            item[4][4],
            item[5][5],
            item[5][6],
            item[5][7],
        )
        for item in items
    ]
    final_function = gateway.finalizeRecoveryBatch(batch_id, compact_records)
    final_calldata = len(bytes.fromhex(final_function._encode_transaction_data()[2:]))
    final_receipt, final_ms = timed_receipt(harness, final_function, cap)
    credits = [harness.settlement.functions.credits(domain, site).call() for site in sites]
    expected_credits = [
        sum(values[index] for index in range(100) if (index % 2 == 0) == (site == 1))
        for site in range(2)
    ]
    if credits != expected_credits or credits != [100, 50]:
        raise AssertionError((credits, expected_credits))

    stage_wall = [row["wallMs"] for row in stage_rows]
    result = {
        "profile": "fresh Geth replay for matched settlement-layer ablation",
        "sameBpv7Bytes": True,
        "records": 100,
        "deploymentAddressesMatchFinalEvidence": True,
        "deploymentNonce": target_nonce,
        "noncePaddingTransactions": padding_transactions,
        "client": harness.web3.client_version,
        "chainId": int(harness.web3.eth.chain_id),
        "blockGasLimit": int(harness.web3.eth.get_block("latest").gasLimit),
        "eip7825TransactionGasCap": cap,
        "setupWallMs": setup_ms,
        "stageTransactions": stage_rows,
        "stageWallMs": sum(stage_wall),
        "finalize": {
            "wallMs": final_ms,
            "gasUsed": int(final_receipt.gasUsed),
            "calldataBytes": final_calldata,
            "blockNumber": int(final_receipt.blockNumber),
        },
        "settlementWallMs": sum(stage_wall) + final_ms,
        "totalGas": sum(row["gasUsed"] for row in stage_rows) + int(final_receipt.gasUsed),
        "totalCalldataBytes": sum(row["calldataBytes"] for row in stage_rows) + final_calldata,
        "credits": credits,
        "inputSha256": hashlib.sha256(received).hexdigest(),
        "scope": (
            "single fresh Geth development chain with one-second block period; setup "
            "excludes campaign/right issuance; settlement wall time is one execution"
        ),
        "allChecksPassed": True,
    }
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
