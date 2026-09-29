"""Run a process-death matrix around the irreversible release boundary.

The campaign exercises the real SQLite/WAL implementation with synchronous=FULL.
It deliberately separates the storage assumption from cryptographic validation:
Ed25519 authenticates the release statement and receipt, while abrupt ``os._exit``
terminates the child at four named boundaries.  A snapshot-rollback negative control
demonstrates why non-rollback storage is an explicit theorem assumption.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aidmesh.protocol.recovered_release import RecoveredReleaseSite  # noqa: E402
from aidmesh.protocol.release import ReleaseSlotKey  # noqa: E402
from aidmesh.protocol.terminal_recovery import digest  # noqa: E402


BOUNDARIES = ("before_actuator", "after_actuator", "before_receipt_sign", "after_close")
CHILD = r"""
import os,sys
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from aidmesh.protocol.recovered_release import RecoveredReleaseSite
from aidmesh.protocol.release import ReleaseSlotKey
from aidmesh.protocol.terminal_recovery import digest

database,counter,boundary,site_key_hex,auth_key_hex,auth_signature=sys.argv[1:]
site_key=Ed25519PrivateKey.from_private_bytes(bytes.fromhex(site_key_hex))
auth_key=Ed25519PrivateKey.from_private_bytes(bytes.fromhex(auth_key_hex))
site_public=site_key.public_key();auth_public=auth_key.public_key()
def sign(_member, message_digest):
    if boundary=='before_receipt_sign': os._exit(73)
    return site_key.sign(bytes.fromhex(message_digest)).hex()
def verify(_member, message_digest, signature):
    try:
        site_public.verify(bytes.fromhex(signature),bytes.fromhex(message_digest));return True
    except Exception:return False
def verify_auth(statement,signature):
    try:
        auth_public.verify(bytes.fromhex(signature),bytes.fromhex(digest(statement)));return True
    except Exception:return False
def actuator(_release_id,_value):
    if boundary=='before_actuator':os._exit(73)
    with open(counter,'ab',buffering=0) as stream:
        stream.write(b'CALL\n');os.fsync(stream.fileno())
    if boundary=='after_actuator':os._exit(73)
    return 'durable-device-ack'
site=RecoveredReleaseSite(database,site='a',domain='D',epoch=1,initial=1,
    sites=('a','b'),witnesses=('w0','w1','w2','w3'),faults=1,
    sign=sign,verify=verify)
site.release(ReleaseSlotKey('D',1,'n','a'),value=1,mode='fallback',
    authorization=auth_signature,verify_authorization=verify_auth,actuator=actuator)
if boundary=='after_close':os._exit(73)
"""


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def raw_private(key: Ed25519PrivateKey) -> bytes:
    from cryptography.hazmat.primitives import serialization

    return key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )


def open_site(path: Path, site_key: Ed25519PrivateKey) -> RecoveredReleaseSite:
    public = site_key.public_key()

    def sign(_member: str, message_digest: str) -> str:
        return site_key.sign(bytes.fromhex(message_digest)).hex()

    def verify(_member: str, message_digest: str, signature: str) -> bool:
        try:
            public.verify(bytes.fromhex(signature), bytes.fromhex(message_digest))
            return True
        except Exception:
            return False

    return RecoveredReleaseSite(
        path,
        site="a",
        domain="D",
        epoch=1,
        initial=1,
        sites=("a", "b"),
        witnesses=("w0", "w1", "w2", "w3"),
        faults=1,
        sign=sign,
        verify=verify,
    )


def authorization(auth_key: Ed25519PrivateKey) -> str:
    slot = ReleaseSlotKey("D", 1, "n", "a")
    statement = {
        "profile": "aidmesh/recovered-release/v1",
        "slot": asdict(slot),
        "value": 1,
        "mode": "fallback",
    }
    return auth_key.sign(bytes.fromhex(digest(statement))).hex()


def verify_authorization(auth_key: Ed25519PrivateKey):
    public = auth_key.public_key()

    def verify(statement: dict, signature: str) -> bool:
        try:
            public.verify(bytes.fromhex(signature), bytes.fromhex(digest(statement)))
            return True
        except Exception:
            return False

    return verify


def row_state(path: Path) -> dict:
    connection = sqlite3.connect(path)
    try:
        row = connection.execute(
            "SELECT status,receipt FROM release_claims ORDER BY slot"
        ).fetchone()
        balance = int(connection.execute("SELECT balance FROM state WHERE id=1").fetchone()[0])
        pragmas = {
            "journal_mode": connection.execute("PRAGMA journal_mode").fetchone()[0],
            "synchronous": int(connection.execute("PRAGMA synchronous").fetchone()[0]),
        }
        return {
            "status": None if row is None else row[0],
            "receipt_present": bool(row and row[1]),
            "balance": balance,
            **pragmas,
        }
    finally:
        connection.close()


def run_boundary(root: Path, boundary: str, repetition: int) -> dict:
    directory = root / f"{boundary}-{repetition:03d}"
    directory.mkdir(parents=True)
    database = directory / "site.sqlite"
    counter = directory / "actuator.log"
    site_key = Ed25519PrivateKey.generate()
    auth_key = Ed25519PrivateKey.generate()
    auth_signature = authorization(auth_key)
    started = time.perf_counter()
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            CHILD,
            str(database),
            str(counter),
            boundary,
            raw_private(site_key).hex(),
            raw_private(auth_key).hex(),
            auth_signature,
        ],
        cwd=ROOT,
        timeout=30,
        check=False,
    )
    calls_before = counter.read_bytes().count(b"CALL\n") if counter.exists() else 0
    before = row_state(database)
    site = open_site(database, site_key)
    site.recover_ambiguities()
    replay_calls = 0

    def forbidden_actuator(*_args):
        nonlocal replay_calls
        replay_calls += 1
        return "unsafe-repeat"

    replay = site.release(
        ReleaseSlotKey("D", 1, "n", "a"),
        value=1,
        mode="fallback",
        authorization=auth_signature,
        verify_authorization=verify_authorization(auth_key),
        actuator=forbidden_actuator,
    )
    after = row_state(database)
    receipt_valid = False
    if site.receipt_outbox():
        receipt_valid = bool(site.verify_receipt(site.receipt_outbox()[0]))
    site.close()
    expected_calls = 0 if boundary == "before_actuator" else 1
    expected_status = "closed" if boundary == "after_close" else "ambiguous"
    safe = (
        child.returncode == 73
        and calls_before == expected_calls
        and replay_calls == 0
        and replay["status"] == expected_status
        and after["balance"] == 0
        and (receipt_valid == (boundary == "after_close"))
        and before["journal_mode"].lower() == "wal"
        and before["synchronous"] == 2
    )
    return {
        "boundary": boundary,
        "repetition": repetition,
        "child_exit": child.returncode,
        "actuator_calls": calls_before,
        "replay_actuator_calls": replay_calls,
        "state_before_recovery": before,
        "state_after_recovery": after,
        "receipt_valid": receipt_valid,
        "safety_preserved": safe,
        "elapsed_seconds": time.perf_counter() - started,
    }


def rollback_negative_control(root: Path) -> dict:
    directory = root / "rollback-negative-control"
    directory.mkdir(parents=True)
    database = directory / "site.sqlite"
    snapshot = directory / "pre-intent.sqlite"
    counter = directory / "actuator.log"
    site_key = Ed25519PrivateKey.generate()
    auth_key = Ed25519PrivateKey.generate()
    signature = authorization(auth_key)
    site = open_site(database, site_key)
    site.close()
    shutil.copy2(database, snapshot)

    def actuate(*_args):
        with counter.open("ab", buffering=0) as stream:
            stream.write(b"CALL\n")
            os.fsync(stream.fileno())
        return "ack"

    site = open_site(database, site_key)
    site.release(
        ReleaseSlotKey("D", 1, "n", "a"),
        value=1,
        mode="fallback",
        authorization=signature,
        verify_authorization=verify_authorization(auth_key),
        actuator=actuate,
    )
    site.close()
    shutil.copy2(snapshot, database)
    site = open_site(database, site_key)
    site.release(
        ReleaseSlotKey("D", 1, "n", "a"),
        value=1,
        mode="fallback",
        authorization=signature,
        verify_authorization=verify_authorization(auth_key),
        actuator=actuate,
    )
    site.close()
    calls = counter.read_bytes().count(b"CALL\n")
    return {
        "control": "restore_pre_intent_snapshot",
        "actuator_calls": calls,
        "reinvocation_observed": calls == 2,
        "interpretation": "non-rollback storage is a necessary recovery-agreement assumption",
    }


def corrupt_header_negative_control(root: Path) -> dict:
    directory = root / "corrupt-header-negative-control"
    directory.mkdir(parents=True)
    database = directory / "site.sqlite"
    site_key = Ed25519PrivateKey.generate()
    site = open_site(database, site_key)
    site.close()
    with database.open("r+b") as stream:
        stream.seek(0)
        stream.write(b"\0" * 128)
        stream.flush()
        os.fsync(stream.fileno())
    rejected = False
    error = ""
    try:
        site = open_site(database, site_key)
        site.close()
    except sqlite3.DatabaseError as exc:
        rejected = True
        error = type(exc).__name__
    return {
        "control": "corrupt_database_header",
        "startup_rejected": rejected,
        "error_type": error,
        "interpretation": "detectable corruption fails closed; arbitrary media recovery is not claimed",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=30)
    args = parser.parse_args()
    if args.repetitions < 1:
        raise ValueError("repetitions must be positive")
    args.output.mkdir(parents=True, exist_ok=False)
    raw_root = args.output / "raw"
    raw_root.mkdir()
    records = [
        run_boundary(raw_root, boundary, repetition)
        for boundary in BOUNDARIES
        for repetition in range(args.repetitions)
    ]
    raw_path = args.output / "release_crash_matrix.jsonl"
    raw_path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in records))
    rollback = rollback_negative_control(raw_root)
    corruption = corrupt_header_negative_control(raw_root)
    source_path = Path(__file__).resolve()
    implementation = ROOT / "aidmesh/protocol/recovered_release.py"
    summary = {
        "profile": "release-crash-matrix",
        "repetitions_per_boundary": args.repetitions,
        "boundary_trials": len(records),
        "all_boundary_trials_safe": all(row["safety_preserved"] for row in records),
        "by_boundary": {
            boundary: {
                "trials": sum(row["boundary"] == boundary for row in records),
                "safe": sum(
                    row["boundary"] == boundary and row["safety_preserved"] for row in records
                ),
                "actuator_calls": sum(
                    row["actuator_calls"] for row in records if row["boundary"] == boundary
                ),
                "replay_actuator_calls": sum(
                    row["replay_actuator_calls"]
                    for row in records
                    if row["boundary"] == boundary
                ),
            }
            for boundary in BOUNDARIES
        },
        "rollback_negative_control": rollback,
        "corruption_negative_control": corruption,
        "storage_profile": {"sqlite": sqlite3.sqlite_version, "journal_mode": "WAL", "synchronous": "FULL"},
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
            "processor": platform.processor(),
        },
        "source_sha256": {
            str(source_path.relative_to(ROOT)): sha256(source_path),
            str(implementation.relative_to(ROOT)): sha256(implementation),
        },
        "raw_sha256": sha256(raw_path),
    }
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
