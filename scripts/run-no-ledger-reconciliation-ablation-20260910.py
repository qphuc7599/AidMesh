"""Matched no-blockchain reconciliation over the final 100 signed BPv7 packets.

This comparator trusts one SQLite authority to serialize first-valid receipt order.
It verifies the final P1 calldata identity, root endorsement, Ed25519 recovery/receipt
envelopes and independent EVM-profile signatures before atomically journaling credit.
It intentionally supplies no Byzantine consensus or public finality.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from eth_account import Account
from eth_account.messages import encode_defunct
from eth_keys.exceptions import BadSignature
from web3 import Web3

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aidmesh.crypto import evm_wire  # noqa: E402
from aidmesh.protocol.recovery_evm_bridge import (  # noqa: E402
    authorization_hash,
    commit_hash,
    transfer_hash,
)
from aidmesh.protocol.root_endorsement import (  # noqa: E402
    RootStatement,
    verify_root_endorsement,
)
from aidmesh.protocol.terminal_recovery import RecoveryRules, digest  # noqa: E402

SECP256K1_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def raw_hex(value: str) -> bytes:
    return bytes.fromhex(value[2:] if value.startswith("0x") else value)


def address_from_int(value: int) -> str:
    return Web3.to_checksum_address("0x" + value.to_bytes(20, "big").hex())


def percentile(values: list[float], q: float) -> float:
    return float(np.percentile(np.asarray(values, dtype=float), q, method="linear"))


def summary(values: list[float]) -> dict[str, float]:
    return {
        "p50": percentile(values, 50),
        "p95": percentile(values, 95),
        "p99": percentile(values, 99),
        "min": min(values),
        "max": max(values),
    }


def expected_calldata(proof_row: dict) -> list:
    proof = proof_row["proof"]
    return [
        [int(value) for value in proof["pi_a"][:2]],
        [
            [int(proof["pi_b"][0][1]), int(proof["pi_b"][0][0])],
            [int(proof["pi_b"][1][1]), int(proof["pi_b"][1][0])],
        ],
        [int(value) for value in proof["pi_c"][:2]],
        [int(value) for value in proof_row["publicSignals"]],
    ]


def schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=FULL;
        CREATE TABLE settlements (
          domain TEXT NOT NULL,
          nullifier TEXT NOT NULL,
          right_id TEXT NOT NULL,
          site TEXT NOT NULL,
          value INTEGER NOT NULL CHECK(value > 0),
          receipt_id TEXT NOT NULL,
          evidence_sha256 TEXT NOT NULL,
          PRIMARY KEY(domain, nullifier),
          UNIQUE(domain, right_id)
        );
        CREATE TABLE rights (
          domain TEXT NOT NULL,
          right_id TEXT NOT NULL,
          owner TEXT NOT NULL,
          value INTEGER NOT NULL CHECK(value > 0),
          consumed INTEGER NOT NULL DEFAULT 0 CHECK(consumed IN (0, 1)),
          PRIMARY KEY(domain, right_id)
        );
        CREATE TABLE credits (
          domain TEXT NOT NULL,
          site TEXT NOT NULL,
          value INTEGER NOT NULL CHECK(value >= 0),
          PRIMARY KEY(domain, site)
        );
        """
    )


def main() -> int:
    if len(sys.argv) != 5:
        raise SystemExit("usage: script SOURCE FINAL_RUN P1_BATCH_REPORT OUTPUT_DIR")
    source, run, p1_report_path, output = (Path(value).resolve() for value in sys.argv[1:])
    output.mkdir(parents=True, exist_ok=True)

    packets_path = run / "packets.json"
    received_path = run / "bpv7/received.json"
    proofs_path = source / "fresh_proofs.json"
    if packets_path.read_bytes() != received_path.read_bytes():
        raise AssertionError("BPv7 payload differs from the sent evidence")
    packets = json.loads(received_path.read_text(encoding="utf-8"))
    proofs = json.loads(proofs_path.read_text(encoding="utf-8"))
    if len(packets) != 100 or len(proofs) != 100:
        raise AssertionError("expected exactly 100 packets and proofs")

    p1_report = json.loads(p1_report_path.read_text(encoding="utf-8"))
    if (
        not p1_report["allValid"]
        or p1_report["proofsPerTrial"] != 100
        or p1_report["proofInputSha256"] != file_sha256(proofs_path)
    ):
        raise AssertionError("P1 batch report is not bound to the final proofs")

    endorsement = json.loads((source / "endorsement.json").read_text(encoding="utf-8"))
    public_keys = json.loads((run / "public_keys.json").read_text(encoding="utf-8"))
    public_keys.update(endorsement["public_keys"])

    def verify_ed(signer: str, digest_value: str, signature: str) -> bool:
        try:
            Ed25519PublicKey.from_public_bytes(raw_hex(public_keys[signer])).verify(
                raw_hex(signature), raw_hex(digest_value)
            )
            return True
        except (InvalidSignature, KeyError, ValueError):
            return False

    root_statement = RootStatement(**endorsement["statement"])
    recovery_members = tuple(
        sorted({vote["signer"] for packet in packets for vote in packet["commits"]})
    )
    if len(recovery_members) != 4:
        raise AssertionError("expected the final K=4 recovery committee")

    def recover_evm(digest_value: bytes, signature_value: str) -> str:
        signature = raw_hex(signature_value)
        if len(signature) != 65:
            raise ValueError("invalid EVM signature length")
        scalar_s = int.from_bytes(signature[32:64], "big")
        if scalar_s == 0 or scalar_s > SECP256K1_N // 2 or signature[64] not in (27, 28):
            raise ValueError("noncanonical EVM signature")
        try:
            recovered = Account.recover_message(
                encode_defunct(primitive=digest_value), signature=signature
            )
        except BadSignature as exc:
            raise ValueError("invalid EVM signature") from exc
        return Web3.to_checksum_address(recovered)

    def validate_packet(index: int, packet: dict) -> dict[str, object]:
        item = packet["item"]
        if item[:4] != expected_calldata(proofs[index]):
            raise ValueError("P1 proof/public calldata identity")
        public = item[3]
        if (
            public[0] != root_statement.campaign
            or public[1] != root_statement.epoch
            or public[5] != root_statement.credential_root
            or public[6] != root_statement.entitlement_root
        ):
            raise ValueError("P1 roots differ from endorsed registry")

        transfer = packet["transfer"]
        transfer_body = transfer["body"]
        if (
            transfer["signer"] != transfer_body["source"]
            or transfer_body["profile"] != "aidmesh/durable-transfer/v1"
            or not verify_ed(transfer["signer"], digest(transfer_body), transfer["signature"])
        ):
            raise ValueError("invalid source transfer envelope")
        rules = RecoveryRules(transfer_body, recovery_members, 1, verify_ed)
        view, decision = rules.certificate(packet["commits"], "commit")

        certificate = item[5]
        receipt = item[4]
        typed_certificate = [raw_hex(certificate[0]), raw_hex(certificate[1]), *certificate[2:]]
        typed_receipt = [
            raw_hex(receipt[0]),
            receipt[1],
            raw_hex(receipt[2]),
            raw_hex(receipt[3]),
            raw_hex(receipt[4]),
            raw_hex(receipt[5]),
        ]
        domain = evm_wire.proof_domain(public)
        context = (public[11], address_from_int(public[12]), address_from_int(public[13]))
        if (
            raw_hex(certificate[0]).hex() != transfer_body["transfer"]
            or raw_hex(certificate[1]).hex() != digest(transfer_body)
            or certificate[2] != transfer_body["source"]
            or certificate[3] != transfer_body["destination"]
            or certificate[4] != transfer_body["epoch"]
            or certificate[5] != 1
            or certificate[6] != view
            or certificate[7] != (decision == "accept")
            or transfer_body["amount"] != public[4]
        ):
            raise ValueError("recovery certificate/transfer mismatch")
        if (
            recover_evm(transfer_hash(domain, typed_certificate, context), certificate[8])
            != certificate[2]
        ):
            raise ValueError("invalid EVM source signature")
        commit_signers = {
            recover_evm(commit_hash(domain, typed_certificate, context), signature)
            for signature in certificate[9]
        }
        if len(commit_signers) < 3 or not commit_signers <= set(recovery_members):
            raise ValueError("invalid EVM recovery quorum")

        recipient = certificate[3] if certificate[7] else certificate[2]
        closed = packet["closed_receipt"]
        body = closed["body"]
        slot = body["statement"]["slot"]
        if (
            closed["signer"] != recipient
            or not verify_ed(recipient, digest(body), closed["signature"])
            or body["profile"] != "aidmesh/recovered-receipt/v1"
            or body["statement"]["profile"] != "aidmesh/recovered-release/v1"
            or body["statement"]["mode"] != "fallback"
            or not body["acknowledgment"]
            or slot["domain_id"] != domain.hex()
            or slot["epoch"] != public[1]
            or int(slot["nullifier"]) != public[7]
            or slot["site_id"] != recipient
            or body["statement"]["value"] != public[4]
            or receipt[0] != "0x" + digest(body)
            or receipt[1] != recipient
            or receipt[2] != "0x" + body["release_id"]
        ):
            raise ValueError("closed receipt/public statement mismatch")
        release_id = raw_hex(receipt[2])
        expected_skeleton = evm_wire.receipt_skeleton(public, release_id, recipient, certificate[4])
        if raw_hex(receipt[3]) != expected_skeleton:
            raise ValueError("receipt skeleton mismatch")
        expected_authorization = authorization_hash(
            public, typed_receipt, typed_certificate, context
        )
        if raw_hex(receipt[4]) != expected_authorization:
            raise ValueError("authorization binding mismatch")
        payload = evm_wire.receipt_payload(
            domain,
            evm_wire.proof_statement_hash(public),
            raw_hex(receipt[0]),
            recipient,
            release_id,
            expected_skeleton,
            expected_authorization,
        )
        if recover_evm(payload, receipt[5]) != recipient:
            raise ValueError("invalid EVM receipt signature")
        return {
            "domain": domain.hex(),
            "nullifier": str(public[7]),
            "rightId": raw_hex(certificate[0]).hex(),
            "source": certificate[2],
            "site": recipient,
            "value": int(public[4]),
            "receiptId": receipt[0],
            "evidenceSha256": hashlib.sha256(
                json.dumps(packet, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
        }

    def seed_rights(connection: sqlite3.Connection) -> None:
        rows = []
        for packet in packets:
            public = packet["item"][3]
            certificate = packet["item"][5]
            rows.append(
                (
                    evm_wire.proof_domain(public).hex(),
                    raw_hex(certificate[0]).hex(),
                    certificate[2],
                    int(public[4]),
                )
            )
        if len(rows) != 100 or len({(row[0], row[1]) for row in rows}) != 100:
            raise AssertionError("expected 100 distinct issued recovery rights")
        connection.executemany(
            "INSERT INTO rights(domain,right_id,owner,value) VALUES(?,?,?,?)", rows
        )
        connection.commit()

    def settle_batch(connection: sqlite3.Connection, candidate_packets: list[dict]) -> None:
        connection.execute("BEGIN IMMEDIATE")
        try:
            for index, packet in enumerate(candidate_packets):
                record = validate_packet(index, packet)
                consumed = connection.execute(
                    """
                    UPDATE rights SET owner=?, consumed=1
                    WHERE domain=? AND right_id=? AND owner=? AND value=? AND consumed=0
                    """,
                    (
                        record["site"],
                        record["domain"],
                        record["rightId"],
                        record["source"],
                        record["value"],
                    ),
                )
                if consumed.rowcount != 1:
                    raise ValueError("right absent, mismatched, or already consumed")
                connection.execute(
                    "INSERT INTO settlements VALUES(?,?,?,?,?,?,?)",
                    (
                        record["domain"],
                        record["nullifier"],
                        record["rightId"],
                        record["site"],
                        record["value"],
                        record["receiptId"],
                        record["evidenceSha256"],
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO credits(domain,site,value) VALUES(?,?,?)
                    ON CONFLICT(domain,site) DO UPDATE SET value=value+excluded.value
                    """,
                    (record["domain"], record["site"], record["value"]),
                )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise

    # The root validation is a campaign setup cost and is executed, but not repeated per voucher.
    if not verify_root_endorsement(
        root_statement,
        endorsement["shares"],
        members=endorsement["members"],
        threshold=endorsement["threshold"],
        faults=endorsement["faults"],
        verify=verify_ed,
    ):
        raise AssertionError("root endorsement failed")

    semantic_times = []
    for _ in p1_report["measurements"]:
        connection = sqlite3.connect(":memory:")
        schema(connection)
        seed_rights(connection)
        start = time.perf_counter_ns()
        settle_batch(connection, packets)
        semantic_times.append((time.perf_counter_ns() - start) / 1_000_000)
        connection.close()

    database = output / "no_ledger_authority.sqlite"
    corrupt_database = output / "corrupt_batch.sqlite"
    for candidate in (database, corrupt_database):
        for suffix in ("", "-wal", "-shm"):
            path = Path(str(candidate) + suffix)
            if path.exists():
                path.unlink()
    connection = sqlite3.connect(database)
    schema(connection)
    seed_rights(connection)
    settle_batch(connection, packets)
    credits = [
        list(row)
        for row in connection.execute("SELECT site,value FROM credits ORDER BY site").fetchall()
    ]
    positive_rows = connection.execute("SELECT COUNT(*) FROM settlements").fetchone()[0]
    positive_rights = connection.execute("SELECT COUNT(*) FROM rights WHERE consumed=1").fetchone()[
        0
    ]
    before_replay = [tuple(row) for row in credits]
    replay_rejected = False
    try:
        settle_batch(connection, packets)
    except (sqlite3.IntegrityError, ValueError):
        replay_rejected = True
    after_replay = [
        tuple(row)
        for row in connection.execute("SELECT site,value FROM credits ORDER BY site").fetchall()
    ]

    corrupt_packets = copy.deepcopy(packets)
    signature = bytearray(raw_hex(corrupt_packets[50]["item"][4][5]))
    signature[5] ^= 1
    corrupt_packets[50]["item"][4][5] = "0x" + bytes(signature).hex()
    corrupt_connection = sqlite3.connect(corrupt_database)
    schema(corrupt_connection)
    seed_rights(corrupt_connection)
    corrupt_rejected = False
    try:
        settle_batch(corrupt_connection, corrupt_packets)
    except ValueError:
        corrupt_rejected = True
    corrupt_rows = corrupt_connection.execute("SELECT COUNT(*) FROM settlements").fetchone()[0]
    corrupt_credits = corrupt_connection.execute("SELECT COUNT(*) FROM credits").fetchone()[0]
    corrupt_rights = corrupt_connection.execute(
        "SELECT COUNT(*) FROM rights WHERE consumed=1"
    ).fetchone()[0]
    corrupt_connection.close()

    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.close()
    restarted = sqlite3.connect(database)
    restart_rows = restarted.execute("SELECT COUNT(*) FROM settlements").fetchone()[0]
    restart_rights = restarted.execute("SELECT COUNT(*) FROM rights WHERE consumed=1").fetchone()[0]
    restart_credits = [
        list(row) for row in restarted.execute("SELECT site,value FROM credits ORDER BY site")
    ]
    restarted.close()

    p1_times = [float(row["p1BatchVerificationMs"]) for row in p1_report["measurements"]]
    combined_times = [left + right for left, right in zip(p1_times, semantic_times, strict=True)]
    geth = json.loads((run / "summary.json").read_text(encoding="utf-8"))
    state = json.loads((run / "atomic_state_audit.json").read_text(encoding="utf-8"))
    result = {
        "profile": "matched no-blockchain signed-evidence reconciliation ablation",
        "trials": len(semantic_times),
        "recordsPerTrial": 100,
        "validation": {
            "sameBpv7Bytes": True,
            "rootEndorsement": True,
            "p1AllValidEveryTrial": True,
            "ed25519TransferRecoveryReceipt": True,
            "evmProfileTransferRecoveryReceipt": True,
            "semanticCrossBinding": True,
        },
        "positive": {
            "settlements": positive_rows,
            "consumedRights": positive_rights,
            "credits": credits,
            "restartSettlements": restart_rows,
            "restartConsumedRights": restart_rights,
            "restartCredits": restart_credits,
        },
        "negativeControls": {
            "replayRejected": replay_rejected,
            "replayCreditsUnchanged": before_replay == after_replay,
            "corruptReceiptRejected": corrupt_rejected,
            "corruptBatchRowsAfterRollback": corrupt_rows,
            "corruptBatchCreditsAfterRollback": corrupt_credits,
            "corruptBatchConsumedRightsAfterRollback": corrupt_rights,
        },
        "latencyMs": {
            "sequentialP1Batch": summary(p1_times),
            "semanticValidationAndSqliteCommit": summary(semantic_times),
            "trialwiseComponentSum": summary(combined_times),
            "note": (
                "trialwise sum pairs two measured components; it is not a "
                "single-process wall-clock trace"
            ),
        },
        "storageAndLedgerCost": {
            "receivedPacketBytes": received_path.stat().st_size,
            "sqliteBytesAfterCheckpoint": database.stat().st_size,
            "noLedgerGas": 0,
            "gethStageCalldataBytes": state["stageCalldataBytes"],
            "gethFinalCalldataBytes": state["commitCalldataBytes"],
            "gethTotalGas": geth["gasUsed"],
            "gethFinalGas": geth["finalizeGasUsed"],
            "feeFormula": "gethTotalGas * feeRate; no current-price or chain-fee claim",
        },
        "trustBoundary": {
            "noLedger": (
                "one honest SQLite authority serializes first-valid order and can "
                "equivocate to different readers if corrupt"
            ),
            "geth": (
                "the configured EVM consensus supplies shared finalized order and "
                "independently auditable state/events"
            ),
            "physicalFact": "neither alternative proves that the modeled handoff happened",
        },
        "inputsSha256": {
            "packets": file_sha256(packets_path),
            "received": file_sha256(received_path),
            "proofs": file_sha256(proofs_path),
            "p1BatchReport": file_sha256(p1_report_path),
            "rootEndorsement": file_sha256(source / "endorsement.json"),
        },
        "allChecksPassed": (
            positive_rows == restart_rows == positive_rights == restart_rights == 100
            and sorted(value for _, value in credits) == [50, 100]
            and credits == restart_credits
            and replay_rejected
            and before_replay == after_replay
            and corrupt_rejected
            and corrupt_rows == corrupt_credits == 0
            and corrupt_rights == 0
        ),
    }
    (output / "summary.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if result["allChecksPassed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
