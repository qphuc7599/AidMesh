"""Audit the exact logical storage footprint of the final staged recovery batch."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from eth_abi import encode
from eth_utils import keccak
from web3 import Web3

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aidmesh.crypto.evm_wire import proof_domain  # noqa: E402


def mapping_slot(key: bytes, slot: int) -> int:
    return int.from_bytes(keccak(encode(["bytes32", "uint256"], [key, slot])), "big")


def storage(web3: Web3, address: str, slot: int) -> bytes:
    return bytes(web3.eth.get_storage_at(address, slot))


def raw_hex(value: str) -> bytes:
    return bytes.fromhex(value[2:] if value.startswith("0x") else value)


def main() -> int:
    if len(sys.argv) != 5:
        raise SystemExit("usage: script SOURCE FINAL_RUN RPC OUTPUT")
    source = Path(sys.argv[1]).resolve()
    run = Path(sys.argv[2]).resolve()
    rpc = sys.argv[3]
    output = Path(sys.argv[4]).resolve()

    web3 = Web3(Web3.HTTPProvider(rpc))
    if not web3.is_connected():
        raise AssertionError(f"cannot connect to {rpc}")
    deployment = json.loads((source / "deployment.json").read_text(encoding="utf-8"))
    gateway = deployment["gateway"]
    state = json.loads((run / "atomic_state_audit.json").read_text(encoding="utf-8"))
    packets_bytes = (run / "bpv7/received.json").read_bytes()
    if packets_bytes != (run / "packets.json").read_bytes():
        raise AssertionError("BPv7 bytes differ")
    packets = json.loads(packets_bytes)
    if len(packets) != 100:
        raise AssertionError("expected 100 records")
    batch_id = raw_hex("0x" + state["batchId"])
    domain = proof_domain(packets[0]["item"][3])

    # solc 0.8.36 storage layout: mappings recoveryBatches/rightStagedBatch/
    # nullifierStagedBatch/stagedRecoveryDigests occupy declaration slots 6..9.
    batch_base = mapping_slot(batch_id, 6)
    header = [storage(web3, gateway, batch_base + index) for index in range(5)]
    expected_header = [
        domain,
        (100 + (100 << 16)).to_bytes(32, "big"),
        (150).to_bytes(32, "big"),
        (150).to_bytes(32, "big"),
    ]
    header_prefix_matches = header[:4] == expected_header
    deadline_and_commit = int.from_bytes(header[4], "big")
    header_commit_bit = ((deadline_and_commit >> 64) & 0xFF) == 1
    header_deadline = deadline_and_commit & ((1 << 64) - 1)

    right_slots = []
    nullifier_slots = []
    expected_digests = []
    for packet in packets:
        item = packet["item"]
        public, receipt, certificate = item[3], item[4], item[5]
        right = raw_hex(certificate[0])
        nullifier = int(public[7]).to_bytes(32, "big")
        right_slots.append(storage(web3, gateway, mapping_slot(right, 7)))
        nullifier_slots.append(storage(web3, gateway, mapping_slot(nullifier, 8)))
        recipient = certificate[3] if certificate[7] else certificate[2]
        expected_digests.append(
            keccak(
                encode(
                    [
                        "bytes32",
                        "bytes32",
                        "bytes32",
                        "bytes32",
                        "bytes32",
                        "address",
                        "uint256",
                        "bytes32",
                        "uint64",
                        "uint64",
                        "bool",
                    ],
                    [
                        domain,
                        right,
                        raw_hex(certificate[1]),
                        nullifier,
                        raw_hex(receipt[0]),
                        recipient,
                        int(public[4]),
                        raw_hex(receipt[4]),
                        int(certificate[5]),
                        int(certificate[6]),
                        bool(certificate[7]),
                    ],
                )
            )
        )

    array_head = mapping_slot(batch_id, 9)
    array_length = int.from_bytes(storage(web3, gateway, array_head), "big")
    array_data_base = int.from_bytes(keccak(array_head.to_bytes(32, "big")), "big")
    actual_digests = [storage(web3, gateway, array_data_base + index) for index in range(100)]

    total_words = 5 + 100 + 100 + 1 + 100
    checks = {
        "bpv7Unchanged": True,
        "headerPrefixMatches": header_prefix_matches,
        "headerCommitted": header_commit_bit,
        "headerDeadlineNonzero": header_deadline > 0,
        "allRightLocksEqualBatch": all(value == batch_id for value in right_slots),
        "allNullifierLocksEqualBatch": all(value == batch_id for value in nullifier_slots),
        "digestArrayLength100": array_length == 100,
        "allDigestWordsMatchRecords": actual_digests == expected_digests,
        "logicalWordCount306": total_words == 306,
    }
    result = {
        "profile": "direct Geth storage audit of final staged-atomic batch",
        "client": web3.client_version,
        "gateway": gateway,
        "batchId": state["batchId"],
        "storageLayoutSource": "solc 0.8.36 --storage-layout; declaration slots 6..9",
        "logicalStagingStorage": {
            "batchHeaderWords": 5,
            "rightLockWords": 100,
            "nullifierLockWords": 100,
            "digestArrayLengthWords": 1,
            "digestDataWords": 100,
            "totalWords": total_words,
            "logicalBytes": total_words * 32,
            "scope": (
                "nonzero EVM storage words retained by the gateway staging manifest; "
                "excludes trie/database overhead and final settlement-contract state"
            ),
        },
        "checks": checks,
        "inputSha256": hashlib.sha256(packets_bytes).hexdigest(),
        "allChecksPassed": all(checks.values()),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if result["allChecksPassed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
