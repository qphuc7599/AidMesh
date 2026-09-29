"""Executable attack matrix over the final P1-v3/recovery/atomic-batch run."""
from __future__ import annotations

import copy
import hashlib
import json
import sys
from dataclasses import replace
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from web3 import Web3

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from aidmesh.crypto.evm_wire import proof_domain
from aidmesh.experiments.semantic_e2e_campaign import _Harness
from aidmesh.protocol.root_endorsement import RootStatement, verify_root_endorsement

source = Path(sys.argv[1]).resolve()
run = Path(sys.argv[2]).resolve()
out = Path(sys.argv[3]).resolve()
rpc = sys.argv[4]
verifier_source = Path(sys.argv[5]).resolve()
live = len(sys.argv) > 6 and sys.argv[6] == "live"
deployment = json.loads((source / "deployment.json").read_text())
h = _Harness(ROOT, rpc_url=rpc, verifier_source=verifier_source, deployment=deployment)
h.witnesses = (*h.accounts[1:4], h.accounts[9])
g = h.gateway.functions
packets = json.loads((run / "bpv7/received.json").read_text())
items = [packet["item"] for packet in packets]
rows = json.loads((source / "fresh_proofs.json").read_text())
domain = proof_domain(list(map(int, rows[0]["publicSignals"])))
batch_id = Web3.keccak(b"aidmesh/staged-atomic/v1" + (run / "bpv7/received.json").read_bytes())
if live:
    pre_stage_block = "latest"
    pre_commit_block = None
else:
    summary = json.loads((run / "summary.json").read_text())
    first_stage = h.web3.eth.get_transaction_receipt(summary["chunks"][0]["transactionHash"])
    events = h.gateway.events.RecoveryBatchCommitted().get_logs(from_block=0, to_block="latest")
    commit_event = next(event for event in events if bytes(event.args.batchId) == bytes(batch_id))
    commit_receipt = h.web3.eth.get_transaction_receipt(commit_event.transactionHash)
    pre_stage_block = int(first_stage.blockNumber) - 1
    pre_commit_block = int(commit_receipt.blockNumber) - 1
cap = 2**24

matrix = []


def try_call(name, layer, function, block, *, sender=None, expected="reject"):
    error = None
    accepted = False
    try:
        result = function.call(
            {"from": sender or h.owner, "gas": cap}, block_identifier=block
        )
        accepted = result is not False
    except Exception as exc:
        error = str(exc)[:240]
    passed = (accepted if expected == "accept" else not accepted)
    matrix.append({
        "attack": name, "layer": layer, "expected": expected,
        "accepted": accepted, "passed": passed, "error": error,
    })
    if not passed:
        raise AssertionError(matrix[-1])


valid = items[0]
try_call("valid_recovery_control", "EVM end-to-end", g.verifyRecoveryAndSettle(*valid),
         pre_stage_block, expected="accept")

bad = copy.deepcopy(valid)
bad[0][0] = int(bad[0][0]) + 1
try_call("groth16_coordinate_mutation", "P1 proof", g.verifyRecoveryAndSettle(*bad), pre_stage_block)

public_names = [
    "campaign", "epoch", "cell", "group", "value", "credential_root",
    "entitlement_root", "nullifier", "mode", "fallback_window", "fallback_tag",
    "chain", "gateway", "settlement", "header", "policy", "committee",
]
for index, name in enumerate(public_names):
    bad = copy.deepcopy(valid)
    bad[3][index] = int(bad[3][index]) + 1
    try_call(f"public_{name}_mutation", "P1 public/context wire",
             g.verifyRecoveryAndSettle(*bad), pre_stage_block)


def flip_hex(value):
    raw = bytearray(Web3.to_bytes(hexstr=value))
    raw[-1] ^= 1
    return "0x" + bytes(raw).hex()


receipt_names = ["receipt_id", "site", "release_intent", "skeleton", "authorization", "site_signature"]
for index, name in enumerate(receipt_names):
    bad = copy.deepcopy(valid)
    if index == 1:
        bad[4][index] = h.accounts[9]
    else:
        bad[4][index] = flip_hex(bad[4][index])
    try_call(f"receipt_{name}_mutation", "closed receipt wire",
             g.verifyRecoveryAndSettle(*bad), pre_stage_block)

certificate_names = [
    "right_id", "transfer_id", "source", "destination", "epoch",
    "nonce", "view", "decision", "source_signature",
]
for index, name in enumerate(certificate_names):
    bad = copy.deepcopy(valid)
    if index in (0, 1, 8):
        bad[5][index] = flip_hex(bad[5][index])
    elif index in (2, 3):
        bad[5][index] = h.accounts[9]
    elif index == 7:
        bad[5][index] = not bool(bad[5][index])
    else:
        bad[5][index] = int(bad[5][index]) + 1
    try_call(f"recovery_{name}_mutation", "terminal recovery wire",
             g.verifyRecoveryAndSettle(*bad), pre_stage_block)

for name, signatures in [
    ("insufficient_commit_quorum", valid[5][9][:2]),
    ("duplicate_commit_signer", [valid[5][9][0], valid[5][9][0], valid[5][9][2]]),
    ("corrupt_commit_signature", [flip_hex(valid[5][9][0]), *valid[5][9][1:]]),
]:
    bad = copy.deepcopy(valid)
    bad[5][9] = signatures
    try_call(name, "recovery quorum", g.verifyRecoveryAndSettle(*bad), pre_stage_block)

bad = copy.deepcopy(valid)
bad[4] = copy.deepcopy(items[1][4])
bad[5] = copy.deepcopy(items[1][5])
try_call("proof_receipt_certificate_cross_binding", "composition wire",
         g.verifyRecoveryAndSettle(*bad), pre_stage_block)

try_call("unauthorized_batch_stager", "atomic batch",
         g.stageRecoveryBatch(batch_id, [valid]), pre_stage_block,
         sender=h.accounts[1])
try_call("duplicate_item_same_stage", "atomic batch",
         g.stageRecoveryBatch(batch_id, [valid, valid]), pre_stage_block)


def compact(item):
    return (
        "0x" + domain.hex(), item[5][0], item[5][1],
        int(item[3][7]).to_bytes(32, "big"), item[4][0], item[4][1], item[3][4],
        item[4][4], item[5][5], item[5][6], item[5][7],
    )


if not live:
    records = [compact(item) for item in items]
    try_call("valid_atomic_finalize_control", "atomic batch",
             g.finalizeRecoveryBatch(batch_id, records), pre_commit_block, expected="accept")
    try_call("incomplete_record_list", "atomic batch",
             g.finalizeRecoveryBatch(batch_id, records[:-1]), pre_commit_block)
    swapped = list(records)
    swapped[0], swapped[1] = swapped[1], swapped[0]
    try_call("staged_record_reordering", "atomic batch",
             g.finalizeRecoveryBatch(batch_id, swapped), pre_commit_block)
    mutated = list(records)
    entry = list(mutated[0])
    entry[7] = flip_hex(entry[7])
    mutated[0] = tuple(entry)
    try_call("staged_authorization_digest_mutation", "atomic batch",
             g.finalizeRecoveryBatch(batch_id, mutated), pre_commit_block)
    try_call("post_commit_finalize_replay", "atomic batch",
             g.finalizeRecoveryBatch(batch_id, records), "latest")
    try_call("post_commit_direct_replay", "settlement first-valid-wins",
             g.verifyRecoveryAndSettle(*valid), "latest")

# Root-endorsement controls use the exact final root statement and retained public keys.
endorsement = json.loads((source / "endorsement.json").read_text())
statement = RootStatement(**endorsement["statement"])


def verify_member(member, digest_value, signature):
    try:
        Ed25519PublicKey.from_public_bytes(
            bytes.fromhex(endorsement["public_keys"][member])
        ).verify(bytes.fromhex(signature), bytes.fromhex(digest_value))
        return True
    except (InvalidSignature, ValueError, KeyError):
        return False


def root_case(name, candidate, shares, expected):
    accepted = False
    error = None
    try:
        accepted = bool(verify_root_endorsement(
            candidate, shares, members=endorsement["members"],
            threshold=endorsement["threshold"], faults=endorsement["faults"],
            verify=verify_member,
        ))
    except Exception as exc:
        error = str(exc)
    passed = accepted if expected == "accept" else not accepted
    matrix.append({"attack": name, "layer": "root endorsement", "expected": expected,
                   "accepted": accepted, "passed": passed, "error": error})
    if not passed:
        raise AssertionError(matrix[-1])


root_case("valid_root_endorsement_control", statement, endorsement["shares"], "accept")
root_case("insufficient_root_quorum", statement, endorsement["shares"][:2], "reject")
root_case("duplicate_root_signer", statement,
          [endorsement["shares"][0], endorsement["shares"][0], endorsement["shares"][2]], "reject")
root_case("malicious_entitlement_root", replace(
    statement, entitlement_root=(statement.entitlement_root + 1)), endorsement["shares"], "reject")
bad_share = copy.deepcopy(endorsement["shares"])
bad_share[0]["signature"] = flip_hex("0x" + bad_share[0]["signature"])[2:]
root_case("corrupt_root_signature", statement, bad_share, "reject")

result = {
    "profile": "final P1-v3, recovery and atomic-settlement executable attack matrix",
    "positiveControls": sum(row["expected"] == "accept" for row in matrix),
    "negativeControls": sum(row["expected"] == "reject" for row in matrix),
    "allPassed": all(row["passed"] for row in matrix),
    "preStageBlock": pre_stage_block,
    "preCommitBlock": pre_commit_block,
    "livePreSettlement": live,
    "rows": matrix,
    "stateMutation": "eth_call at retained historical states; actual incomplete/replay transactions are in summary.json",
    "inputsSha256": {
        "packets": hashlib.sha256((run / "bpv7/received.json").read_bytes()).hexdigest(),
        "proofs": hashlib.sha256((source / "fresh_proofs.json").read_bytes()).hexdigest(),
        "endorsement": hashlib.sha256((source / "endorsement.json").read_bytes()).hexdigest(),
    },
    "scope": "cryptographic/context/recovery/receipt/batch fields; denial-of-service and physical compromise remain separate",
}
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(result, indent=2))
print(json.dumps({k: result[k] for k in ["positiveControls", "negativeControls", "allPassed"]}))
