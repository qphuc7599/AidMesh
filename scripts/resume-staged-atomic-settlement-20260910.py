"""Resume an opened, empty staged batch after a gas-calibration failure."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from web3 import Web3
from aidmesh.experiments.semantic_e2e_campaign import _Harness
from aidmesh.crypto.evm_wire import proof_domain

source = Path(sys.argv[1]).resolve()
run = Path(sys.argv[2]).resolve()
rpc = sys.argv[3]
verifier_source = Path(sys.argv[4]).resolve()
chunk_size = int(sys.argv[5])
if not 1 <= chunk_size <= 25:
    raise ValueError("chunk size must be 1..25")

deployment = json.loads((source / "deployment.json").read_text())
h = _Harness(ROOT, rpc_url=rpc, verifier_source=verifier_source, deployment=deployment)
h.witnesses = (*h.accounts[1:4], h.accounts[9])
g = h.gateway.functions
sites = h.sites[:2]
rows = json.loads((source / "fresh_proofs.json").read_text())
values = [int(row["publicSignals"][4]) for row in rows]
domain = proof_domain(list(map(int, rows[0]["publicSignals"])))
received_path = run / "bpv7/received.json"
received = received_path.read_bytes()
if received != (run / "packets.json").read_bytes():
    raise AssertionError("BPv7 payload mismatch")
items = [packet["item"] for packet in json.loads(received)]
compact_records = [
    (
        "0x" + domain.hex(), item[5][0], item[5][1],
        int(item[3][7]).to_bytes(32, "big"), item[4][0], item[4][1], item[3][4],
        item[4][4], item[5][5], item[5][6], item[5][7],
    )
    for item in items
]
batch_id = Web3.keccak(b"aidmesh/staged-atomic/v1" + received)
batch_before = g.recoveryBatches(batch_id).call()
staged_count = int(batch_before[2])
staged_value = int(batch_before[4])
if (not 0 <= staged_count <= 100 or staged_value != sum(values[:staged_count])
        or bool(batch_before[6])):
    raise AssertionError(f"batch is not a resumable ordered checkpoint: {batch_before}")
if [h.settlement.functions.credits(domain, site).call() for site in sites] != [0, 0]:
    raise AssertionError("credits changed before atomic commit")

cap = 2**24
gas_cap_error = None
try:
    h.web3.eth.send_transaction(
        {"from": h.owner, "to": h.owner, "value": 0, "gas": cap + 1}
    )
except Exception as error:
    gas_cap_error = str(error)
if gas_cap_error is None:
    raise AssertionError("Geth accepted gas above EIP-7825")

direct_full_data = bytes.fromhex(
    g.verifyRecoveryBatchAndSettle(items)._encode_transaction_data()[2:]
)
stage_receipts = []
incomplete_status = None
if staged_count < 100:
    incomplete = h.web3.eth.wait_for_transaction_receipt(
        g.finalizeRecoveryBatch(batch_id, compact_records).transact(
            {"from": h.relayer, "gas": cap}
        )
    )
    incomplete_status = int(incomplete.status)
    if incomplete.status != 0:
        raise AssertionError("incomplete batch finalized")
for offset in range(staged_count, 100, chunk_size):
    subset = items[offset : min(offset + chunk_size, 100)]
    fn = g.stageRecoveryBatch(batch_id, subset)
    calldata_bytes = len(bytes.fromhex(fn._encode_transaction_data()[2:]))
    receipt = h.web3.eth.wait_for_transaction_receipt(
        fn.transact({"from": h.relayer, "gas": cap})
    )
    if receipt.status != 1:
        raise AssertionError(f"stage failed at {offset} with chunk {len(subset)}")
    stage_receipts.append(
        {
            "offset": offset,
            "count": len(subset),
            "calldataBytes": calldata_bytes,
            "gasUsed": int(receipt.gasUsed),
            "status": int(receipt.status),
            "transactionHash": receipt.transactionHash.hex(),
        }
    )
    if [h.settlement.functions.credits(domain, site).call() for site in sites] != [0, 0]:
        raise AssertionError("staging created a settlement credit")

finalize_fn = g.finalizeRecoveryBatch(batch_id, compact_records)
finalize_calldata = len(bytes.fromhex(finalize_fn._encode_transaction_data()[2:]))
finalize = h.web3.eth.wait_for_transaction_receipt(
    finalize_fn.transact({"from": h.relayer, "gas": cap})
)
if finalize.status != 1:
    raise AssertionError("atomic finalization failed")

credits = [h.settlement.functions.credits(domain, site).call() for site in sites]
expected = [
    sum(values[i] for i in range(100) if (i % 2 == 0) == (site_index == 1))
    for site_index in range(2)
]
if credits != expected or sum(credits) != sum(values):
    raise AssertionError((credits, expected))
replay = h.web3.eth.wait_for_transaction_receipt(
    g.finalizeRecoveryBatch(batch_id, compact_records).transact(
        {"from": h.relayer, "gas": 1_000_000}
    )
)
if replay.status != 0 or [h.settlement.functions.credits(domain, site).call() for site in sites] != credits:
    raise AssertionError("finalize replay changed state")

summary = {
    "profile": "EIP-7825-compatible staged verification with atomic 100-credit commit",
    "gethClient": h.web3.client_version,
    "chainId": h.web3.eth.chain_id,
    "blockGasLimit": int(h.web3.eth.get_block("latest").gasLimit),
    "eip7825TransactionGasCap": cap,
    "gasAboveCapRejected": True,
    "gasAboveCapError": gas_cap_error,
    "directFullBatchCalldataBytes": len(direct_full_data),
    "observedGethTransactionSizeLimitBytes": 131072,
    "stageTransactions": stage_receipts,
    "stageTotalGasUsed": sum(row["gasUsed"] for row in stage_receipts),
    "maxStageGasUsed": max(row["gasUsed"] for row in stage_receipts),
    "incompleteFinalizeStatus": incomplete_status,
    "creditsBeforeFinalize": [0, 0],
    "finalize": {
        "calldataBytes": finalize_calldata,
        "gasUsed": int(finalize.gasUsed),
        "status": int(finalize.status),
        "transactionHash": finalize.transactionHash.hex(),
    },
    "creditsAfterFinalize": credits,
    "expectedCredits": expected,
    "escrowAfter": int(h.settlement.functions.campaigns(domain).call()[3]),
    "uniqueVouchers": len({row["publicSignals"][7] for row in rows}),
    "actuatorInvocations": len((run / "actuator.jsonl").read_text().splitlines()),
    "bpv7PayloadSha256": hashlib.sha256(received).hexdigest(),
    "bpv7Unchanged": True,
    "replayFinalizeStatus": int(replay.status),
    "atomicSettlementValueConserved": sum(credits) == sum(values),
    "scope": (
        "single-host Geth/BPv7 execution with synthetic durable actuator; proof and "
        "certificate validation is staged, while all 100 settlement credits commit "
        "or revert in one final transaction"
    ),
}
(run / "summary.json").write_text(json.dumps(summary, indent=2))
print(json.dumps(summary, indent=2))
