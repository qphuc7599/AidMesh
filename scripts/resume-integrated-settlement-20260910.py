"""Finish a clean 100-voucher run after a pre-execution full-calldata rejection."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aidmesh.experiments.semantic_e2e_campaign import _Harness
from aidmesh.crypto.evm_wire import proof_domain

source = Path(sys.argv[1]).resolve()
run = Path(sys.argv[2]).resolve()
rpc = sys.argv[3]
verifier_source = Path(sys.argv[4]).resolve()
chunk_size = int(sys.argv[5])
if chunk_size < 1 or 100 % chunk_size:
    raise ValueError("chunk size must divide 100")

deployment = json.loads((source / "deployment.json").read_text())
h = _Harness(
    ROOT,
    rpc_url=rpc,
    verifier_source=verifier_source,
    deployment=deployment,
)
h.witnesses = (*h.accounts[1:4], h.accounts[9])
g = h.gateway.functions
sites = h.sites[:2]
rows = json.loads((source / "fresh_proofs.json").read_text())
values = [int(row["publicSignals"][4]) for row in rows]
domain = proof_domain(list(map(int, rows[0]["publicSignals"])))
received_path = run / "bpv7/received.json"
sent_path = run / "packets.json"
if received_path.read_bytes() != sent_path.read_bytes():
    raise AssertionError("BPv7 changed the signed payload")
items = [packet["item"] for packet in json.loads(received_path.read_text())]
if len(items) != 100:
    raise AssertionError("expected 100 recovery settlement items")

initial_credits = [h.settlement.functions.credits(domain, site).call() for site in sites]
campaign = h.settlement.functions.campaigns(domain).call()
if initial_credits != [0, 0] or int(campaign[3]) != sum(values):
    raise AssertionError("resume state is not the clean pre-settlement checkpoint")

eip7825_cap = 2**24
gas_cap_error = None
try:
    h.web3.eth.send_transaction(
        {"from": h.owner, "to": h.owner, "value": 0, "gas": eip7825_cap + 1}
    )
except Exception as error:  # exact RPC reason is preserved in the artifact
    gas_cap_error = str(error)
if gas_cap_error is None:
    raise AssertionError("client accepted a transaction above the EIP-7825 cap")

full_batch_error = None
try:
    g.verifyRecoveryBatchAndSettle(items).transact(
        {"from": h.relayer, "gas": eip7825_cap}
    )
except Exception as error:
    full_batch_error = str(error)
if full_batch_error is None:
    raise AssertionError("100-item full-calldata transaction unexpectedly entered the pool")
if [h.settlement.functions.credits(domain, site).call() for site in sites] != [0, 0]:
    raise AssertionError("pre-execution rejection changed credits")

receipts = []
for offset in range(0, 100, chunk_size):
    receipt = h.web3.eth.wait_for_transaction_receipt(
        g.verifyRecoveryBatchAndSettle(items[offset : offset + chunk_size]).transact(
            {"from": h.relayer, "gas": eip7825_cap}
        )
    )
    if receipt.status != 1:
        raise AssertionError(f"chunk at offset {offset} failed")
    receipts.append(
        {
            "offset": offset,
            "count": chunk_size,
            "gasUsed": int(receipt.gasUsed),
            "status": int(receipt.status),
            "transactionHash": receipt.transactionHash.hex(),
        }
    )

credits = [h.settlement.functions.credits(domain, site).call() for site in sites]
expected = [
    sum(values[i] for i in range(100) if (i % 2 == 0) == (site_index == 1))
    for site_index in range(2)
]
if credits != expected:
    raise AssertionError((credits, expected))

replay = h.web3.eth.wait_for_transaction_receipt(
    g.verifyRecoveryBatchAndSettle(items[:chunk_size]).transact(
        {"from": h.relayer, "gas": eip7825_cap}
    )
)
if replay.status != 0:
    raise AssertionError("replayed recovery chunk did not revert")
if [h.settlement.functions.credits(domain, site).call() for site in sites] != credits:
    raise AssertionError("replay changed credits")

summary = {
    "profile": "EIP-7825-compatible chunked recovery settlement",
    "gethClient": h.web3.client_version,
    "chainId": h.web3.eth.chain_id,
    "blockGasLimit": int(h.web3.eth.get_block("latest").gasLimit),
    "eip7825TransactionGasCap": eip7825_cap,
    "gasAboveCapRejected": True,
    "gasAboveCapError": gas_cap_error,
    "fullBatchSubmission": "rejected_pre_execution",
    "fullBatchError": full_batch_error,
    "chunks": receipts,
    "totalGasUsed": sum(row["gasUsed"] for row in receipts),
    "maxTransactionGasUsed": max(row["gasUsed"] for row in receipts),
    "credits": credits,
    "expectedCredits": expected,
    "escrowAfter": int(h.settlement.functions.campaigns(domain).call()[3]),
    "uniqueVouchers": len({row["publicSignals"][7] for row in rows}),
    "actuatorInvocations": len((run / "actuator.jsonl").read_text().splitlines()),
    "bpv7PayloadSha256": hashlib.sha256(received_path.read_bytes()).hexdigest(),
    "bpv7Unchanged": True,
    "replayStatus": int(replay.status),
    "settlementValueConserved": sum(credits) == sum(values),
    "scope": (
        "single-host real Geth and BPv7 processes; fresh Coconut/P1-v3 proofs; "
        "durable synthetic actuator; four independent settlement transactions"
    ),
}
(run / "summary.json").write_text(json.dumps(summary, indent=2))
print(json.dumps(summary, indent=2))
