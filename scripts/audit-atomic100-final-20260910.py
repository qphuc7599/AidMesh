"""Independent state/history audit for the compact staged atomic-100 profile."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from eth_abi import encode
from web3 import Web3

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from aidmesh.crypto.evm_wire import proof_domain
from aidmesh.experiments.semantic_e2e_campaign import _Harness

source = Path(sys.argv[1]).resolve()
run = Path(sys.argv[2]).resolve()
rpc = sys.argv[3]
verifier_source = Path(sys.argv[4]).resolve()
deployment = json.loads((source / "deployment.json").read_text())
summary = json.loads((run / "summary.json").read_text())
h = _Harness(ROOT, rpc_url=rpc, verifier_source=verifier_source, deployment=deployment)
h.witnesses = (*h.accounts[1:4], h.accounts[9])

received = (run / "bpv7/received.json").read_bytes()
items = [packet["item"] for packet in json.loads(received)]
rows = json.loads((source / "fresh_proofs.json").read_text())
domain = proof_domain(list(map(int, rows[0]["publicSignals"])))
batch_id = Web3.keccak(b"aidmesh/staged-atomic/v1" + received)
events = h.gateway.events.RecoveryBatchCommitted().get_logs(from_block=0, to_block="latest")
matching = [event for event in events if bytes(event.args.batchId) == bytes(batch_id)]
if len(matching) != 1:
    raise AssertionError(f"expected one commit event, got {len(matching)}")
final_receipt = h.web3.eth.get_transaction_receipt(matching[0].transactionHash)
final_tx = h.web3.eth.get_transaction(matching[0].transactionHash)
pre_block = int(final_receipt.blockNumber) - 1
sites = h.sites[:2]
# The benchmark node uses Geth's normal pruned state, so an eth_call against the
# pre-commit block is not guaranteed to remain available after the run.  Credits
# can change only through ReceiptSettled or BatchAuthorizationCommitted in this
# freshly deployed settlement contract.  Event history therefore provides a
# durable, independent pre-state witness without requiring an archive node.
prior_receipts = h.settlement.events.ReceiptSettled().get_logs(
    from_block=0, to_block=pre_block,
)
prior_batches = h.settlement.events.BatchAuthorizationCommitted().get_logs(
    from_block=0, to_block=pre_block,
)
prior_domain_receipts = [
    event for event in prior_receipts if bytes(event.args.domain) == bytes(domain)
]
prior_domain_batches = [
    event for event in prior_batches if bytes(event.args.domain) == bytes(domain)
]
credits_before = [0, 0] if not prior_domain_receipts and not prior_domain_batches else None
credits_after = [h.settlement.functions.credits(domain, site).call() for site in sites]
batch = h.gateway.functions.recoveryBatches(batch_id).call()

nullifiers = [int(item[3][7]).to_bytes(32, "big") for item in items]
authorizations = [Web3.to_bytes(hexstr=item[4][4]) for item in items]
nullifier_list_hash = Web3.keccak(encode(["bytes32[]"], [nullifiers]))
authorization_list_hash = Web3.keccak(encode(["bytes32[]"], [authorizations]))
stored_authorization_root = h.settlement.functions.batchAuthorizationRoots(
    domain, nullifier_list_hash
).call()
winning = [
    h.settlement.functions.winningReceipt(domain, nullifier).call()
    for nullifier in nullifiers
]
locks = [
    h.gateway.functions.rightStagedBatch(item[5][0]).call()
    for item in items
]
stage_receipts = [
    h.web3.eth.get_transaction_receipt(Web3.to_bytes(hexstr=row["transactionHash"]))
    for row in summary["chunks"]
]
cap = 2**24
checks = {
    "batchCommitted": bool(batch[6]),
    "batchCount100": int(batch[2]) == 100,
    "batchValue150": int(batch[4]) == 150,
    "noPriorSettlementEventsForDomain": (
        not prior_domain_receipts and not prior_domain_batches
    ),
    "creditsZeroBeforeFinalize": credits_before == [0, 0],
    "creditsExactAfterFinalize": credits_after == [100, 50],
    "allWinningReceiptsRecorded": all(value != bytes(32) for value in winning),
    "allRightsPermanentlyBoundToCommittedBatch": all(bytes(value) == bytes(batch_id) for value in locks),
    "authorizationListCommitted": bytes(stored_authorization_root) == bytes(authorization_list_hash),
    "finalizeBelowEip7825": int(final_receipt.gasUsed) < cap,
    "allStagesBelowEip7825": all(int(receipt.gasUsed) < cap for receipt in stage_receipts),
    "replayRejected": summary["replayStatus"] == 0,
    "incompleteFinalizeRejected": summary["incompleteFinalizeStatus"] == 0,
    "bpv7Unchanged": (run / "packets.json").read_bytes() == received,
}
if not all(checks.values()):
    raise AssertionError(checks)

audit = {
    "profile": "compact staged verification, atomic 100-voucher settlement",
    "checks": checks,
    "batchId": batch_id.hex(),
    "commitBlock": int(final_receipt.blockNumber),
    "commitTransactionHash": final_receipt.transactionHash.hex(),
    "commitCalldataBytes": len(final_tx.input),
    "commitGasUsed": int(final_receipt.gasUsed),
    "eip7825TransactionGasCap": cap,
    "creditsBeforeCommit": credits_before,
    "preCommitStateWitness": {
        "method": "absence of all credit-mutating settlement events for the domain",
        "throughBlock": pre_block,
        "priorReceiptSettledEvents": len(prior_domain_receipts),
        "priorBatchAuthorizationCommittedEvents": len(prior_domain_batches),
    },
    "creditsAfterCommit": credits_after,
    "nullifierListHash": nullifier_list_hash.hex(),
    "authorizationListHash": authorization_list_hash.hex(),
    "stageGasUsed": [int(receipt.gasUsed) for receipt in stage_receipts],
    "stageCalldataBytes": [len(h.web3.eth.get_transaction(receipt.transactionHash).input)
                           for receipt in stage_receipts],
    "gethClient": h.web3.client_version,
    "sourceSha256": {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in [
            ROOT / "contracts/src/AidMeshSemanticAuthorizationGateway.sol",
            ROOT / "contracts/src/AidMeshSettlement.sol",
            ROOT / "zk/circuits/aidmesh_voucher_v3.circom",
            ROOT / "scripts/run-integrated-recovery-batch.py",
        ]
    },
    "scope": (
        "all proof/certificate checks precede a single final transaction; the committed "
        "batch lock is the spent marker for staged rights; synthetic actuator and single host"
    ),
}
(run / "atomic_state_audit.json").write_text(json.dumps(audit, indent=2))
print(json.dumps(audit, indent=2))
