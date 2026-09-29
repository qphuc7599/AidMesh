"""Distributed risk-right transfer campaign with no shared ledger or global lock.

Every site and witness owns a separate SQLite database.  Processes exchange only
canonical transfer proposals/certificates.  SQLite transactions protect one
participant's durable state; no database, Python lock, or in-memory ledger is
shared across sites.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing as mp
import os
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from aidmesh.common.canonical import stable_hash

PROTOCOL = "aidmesh/distributed-risk-right/v1"
WITNESSES = ("w0", "w1", "w2", "w3")
THRESHOLD = 3


@dataclass(frozen=True)
class TransferProposal:
    domain: str
    token_id: str
    source: str
    destination: str
    transfer_id: str
    parent_certificate: str

    def payload(self) -> dict[str, str]:
        return {"protocol": PROTOCOL, **asdict(self)}

    @property
    def digest(self) -> str:
        return stable_hash(self.payload(), domain=PROTOCOL)


def _private_key(identity: str) -> Ed25519PrivateKey:
    seed = hashlib.sha256(f"{PROTOCOL}|{identity}".encode("utf-8")).digest()
    return Ed25519PrivateKey.from_private_bytes(seed)


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=20, isolation_level=None)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    return connection


def _init_site(path: Path, site: str, tokens: tuple[str, ...] = ()) -> None:
    connection = _connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS site_meta(
                site TEXT PRIMARY KEY
            );
            CREATE TABLE IF NOT EXISTS token_state(
                token_id TEXT PRIMARY KEY,
                state TEXT NOT NULL CHECK(state IN ('owned','exported','spent')),
                provenance TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS outbox(
                transfer_id TEXT PRIMARY KEY,
                token_id TEXT NOT NULL UNIQUE,
                proposal_json TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending','certified'))
            );
            CREATE TABLE IF NOT EXISTS inbox(
                transfer_id TEXT PRIMARY KEY,
                token_id TEXT NOT NULL UNIQUE,
                certificate_hash TEXT NOT NULL
            );
            """
        )
        connection.execute("INSERT OR IGNORE INTO site_meta(site) VALUES (?)", (site,))
        for token_id in tokens:
            connection.execute(
                "INSERT OR IGNORE INTO token_state(token_id,state,provenance)"
                " VALUES (?,'owned','GENESIS')",
                (token_id,),
            )
    finally:
        connection.close()


def _site_id(connection: sqlite3.Connection) -> str:
    row = connection.execute("SELECT site FROM site_meta").fetchone()
    if row is None:
        raise RuntimeError("site database is not initialized")
    return str(row[0])


def _export(path: Path, proposal: TransferProposal) -> str:
    connection = _connect(path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        if _site_id(connection) != proposal.source:
            raise ValueError("WRONG_SOURCE")
        existing = connection.execute(
            "SELECT transfer_id,proposal_json FROM outbox WHERE token_id=?",
            (proposal.token_id,),
        ).fetchone()
        canonical = json.dumps(proposal.payload(), sort_keys=True, separators=(",", ":"))
        if existing is not None:
            if str(existing[0]) == proposal.transfer_id and str(existing[1]) == canonical:
                connection.commit()
                return "idempotent"
            raise ValueError("SOURCE_EQUIVOCATION_REJECTED")
        row = connection.execute(
            "SELECT state FROM token_state WHERE token_id=?", (proposal.token_id,)
        ).fetchone()
        if row is None or str(row[0]) != "owned":
            raise ValueError("TOKEN_NOT_OWNED")
        connection.execute(
            "UPDATE token_state SET state='exported' WHERE token_id=?",
            (proposal.token_id,),
        )
        connection.execute(
            "INSERT INTO outbox(transfer_id,token_id,proposal_json,status)"
            " VALUES (?,?,?,'pending')",
            (proposal.transfer_id, proposal.token_id, canonical),
        )
        connection.commit()
        return "exported"
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _init_witness(path: Path) -> None:
    connection = _connect(path)
    try:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS transfer_decision(
                domain_token TEXT PRIMARY KEY,
                digest TEXT NOT NULL,
                signature TEXT NOT NULL
            )
            """
        )
    finally:
        connection.close()


def _notarize(path: Path, witness_id: str, proposal: TransferProposal) -> dict[str, str]:
    _init_witness(path)
    connection = _connect(path)
    key = f"{proposal.domain}|{proposal.token_id}"
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT digest,signature FROM transfer_decision WHERE domain_token=?", (key,)
        ).fetchone()
        if row is not None:
            if str(row[0]) != proposal.digest:
                raise ValueError("WITNESS_CONFLICT")
            connection.commit()
            return {"witness": witness_id, "signature": str(row[1])}
        signature = _private_key(witness_id).sign(
            proposal.digest.encode("ascii")
        ).hex()
        connection.execute(
            "INSERT INTO transfer_decision(domain_token,digest,signature) VALUES (?,?,?)",
            (key, proposal.digest, signature),
        )
        connection.commit()
        return {"witness": witness_id, "signature": signature}
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _notary_worker(
    path: str, witness_id: str, payload: dict[str, str], queue: Any
) -> None:
    proposal = TransferProposal(**payload)
    try:
        share = _notarize(Path(path), witness_id, proposal)
        queue.put({"status": "signed", "pid": os.getpid(), **share})
    except Exception as error:  # noqa: BLE001
        queue.put(
            {
                "status": "rejected",
                "pid": os.getpid(),
                "witness": witness_id,
                "error": str(error),
            }
        )


def _certificate(
    proposal: TransferProposal, witness_root: Path
) -> tuple[dict[str, Any], list[int]]:
    context = mp.get_context("spawn")
    queue = context.Queue()
    processes = [
        context.Process(
            target=_notary_worker,
            args=(
                str(witness_root / f"{witness}.sqlite"),
                witness,
                asdict(proposal),
                queue,
            ),
        )
        for witness in WITNESSES
    ]
    for process in processes:
        process.start()
    results = [queue.get(timeout=30) for _ in processes]
    for process in processes:
        process.join(timeout=30)
        if process.exitcode not in (0,):
            raise RuntimeError(f"notary process failed: pid={process.pid}, code={process.exitcode}")
    shares = [
        {"witness": row["witness"], "signature": row["signature"]}
        for row in results
        if row["status"] == "signed"
    ]
    certificate = {"proposal": proposal.payload(), "shares": shares}
    return certificate, [int(row["pid"]) for row in results]


def _verify_certificate(certificate: dict[str, Any], destination: str) -> TransferProposal:
    raw = dict(certificate["proposal"])
    if raw.pop("protocol", None) != PROTOCOL:
        raise ValueError("WRONG_PROTOCOL")
    proposal = TransferProposal(**raw)
    if proposal.destination != destination:
        raise ValueError("WRONG_DESTINATION")
    valid: set[str] = set()
    for share in certificate.get("shares", []):
        witness = str(share["witness"])
        if witness not in WITNESSES or witness in valid:
            continue
        try:
            _private_key(witness).public_key().verify(
                bytes.fromhex(str(share["signature"])),
                proposal.digest.encode("ascii"),
            )
            valid.add(witness)
        except (ValueError, TypeError):
            continue
    if len(valid) < THRESHOLD:
        raise ValueError("INSUFFICIENT_NOTARY_QUORUM")
    return proposal


def _accept(path: Path, certificate: dict[str, Any]) -> str:
    connection = _connect(path)
    try:
        destination = _site_id(connection)
        proposal = _verify_certificate(certificate, destination)
        certificate_hash = stable_hash(certificate, domain=f"{PROTOCOL}/certificate")
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT certificate_hash FROM inbox WHERE transfer_id=?",
            (proposal.transfer_id,),
        ).fetchone()
        if row is not None:
            if str(row[0]) != certificate_hash:
                raise ValueError("TRANSFER_ID_REBINDING")
            connection.commit()
            return "idempotent"
        token = connection.execute(
            "SELECT provenance FROM token_state WHERE token_id=?", (proposal.token_id,)
        ).fetchone()
        if token is not None:
            raise ValueError("TOKEN_ALREADY_PRESENT")
        connection.execute(
            "INSERT INTO inbox(transfer_id,token_id,certificate_hash) VALUES (?,?,?)",
            (proposal.transfer_id, proposal.token_id, certificate_hash),
        )
        connection.execute(
            "INSERT INTO token_state(token_id,state,provenance) VALUES (?,'owned',?)",
            (proposal.token_id, certificate_hash),
        )
        connection.commit()
        return "accepted"
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _accept_worker(path: str, certificate: dict[str, Any], queue: Any) -> None:
    try:
        outcome = _accept(Path(path), certificate)
        queue.put({"status": outcome, "pid": os.getpid()})
    except Exception as error:  # noqa: BLE001
        queue.put({"status": "rejected", "pid": os.getpid(), "error": str(error)})


def _crash_after_export(path: str, payload: dict[str, str]) -> None:
    _export(Path(path), TransferProposal(**payload))
    os._exit(23)


def _owned_tokens(path: Path) -> set[str]:
    connection = _connect(path)
    try:
        return {
            str(row[0])
            for row in connection.execute(
                "SELECT token_id FROM token_state WHERE state='owned'"
            )
        }
    finally:
        connection.close()


def run_distributed_rights_campaign(output: Path, *, trials: int = 8) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    all_pids: set[int] = set()
    for trial in range(trials):
        root = output / "state" / f"trial-{trial:03d}"
        source = root / "sites" / "site-a.sqlite"
        destination = root / "sites" / "site-b.sqlite"
        wrong_destination = root / "sites" / "site-c.sqlite"
        token = f"right-{trial:03d}"
        _init_site(source, "site-a", (token,))
        _init_site(destination, "site-b")
        _init_site(wrong_destination, "site-c")
        proposal = TransferProposal(
            domain="campaign-1|epoch-1|cell-a",
            token_id=token,
            source="site-a",
            destination="site-b",
            transfer_id=f"transfer-{trial:03d}",
            parent_certificate="GENESIS",
        )

        context = mp.get_context("spawn")
        crash = context.Process(
            target=_crash_after_export, args=(str(source), asdict(proposal))
        )
        crash.start()
        crash.join(timeout=30)
        all_pids.add(int(crash.pid))
        recovered = crash.exitcode == 23 and not _owned_tokens(source)
        certificate, notary_pids = _certificate(proposal, root / "witnesses")
        all_pids.update(notary_pids)

        queue = context.Queue()
        receivers = [
            context.Process(
                target=_accept_worker, args=(str(destination), certificate, queue)
            )
            for _ in range(2)
        ]
        for process in receivers:
            process.start()
        outcomes = [queue.get(timeout=30) for _ in receivers]
        for process in receivers:
            process.join(timeout=30)
            all_pids.add(int(process.pid))
        duplicate_safe = sorted(row["status"] for row in outcomes) == [
            "accepted",
            "idempotent",
        ]

        wrong_rejected = False
        try:
            _accept(wrong_destination, certificate)
        except ValueError as error:
            wrong_rejected = "WRONG_DESTINATION" in str(error)

        conflicting = TransferProposal(
            domain=proposal.domain,
            token_id=proposal.token_id,
            source=proposal.source,
            destination="site-c",
            transfer_id=f"conflict-{trial:03d}",
            parent_certificate="GENESIS",
        )
        conflicting_certificate, conflict_pids = _certificate(
            conflicting, root / "witnesses"
        )
        all_pids.update(conflict_pids)
        conflict_quorum_blocked = len(conflicting_certificate["shares"]) < THRESHOLD

        source_rejects_second = False
        try:
            _export(source, conflicting)
        except ValueError:
            source_rejects_second = True

        owned = {
            "site-a": sorted(_owned_tokens(source)),
            "site-b": sorted(_owned_tokens(destination)),
            "site-c": sorted(_owned_tokens(wrong_destination)),
        }
        unique_owner = sum(token in values for values in owned.values()) == 1
        passed = all(
            (
                recovered,
                duplicate_safe,
                wrong_rejected,
                conflict_quorum_blocked,
                source_rejects_second,
                unique_owner,
            )
        )
        rows.append(
            {
                "trial": trial,
                "passed": passed,
                "source_crash_recovered": recovered,
                "duplicate_delivery_idempotent": duplicate_safe,
                "wrong_destination_rejected": wrong_rejected,
                "conflicting_quorum_blocked": conflict_quorum_blocked,
                "source_equivocation_rejected": source_rejects_second,
                "unique_active_owner": unique_owner,
                "receiver_outcomes": outcomes,
                "owned_tokens": owned,
                "notary_shares": len(certificate["shares"]),
                "conflict_shares": len(conflicting_certificate["shares"]),
            }
        )
    payload = {
        "schema_version": 1,
        "campaign": "distributed_risk_right_no_shared_ledger_v1",
        "trials": trials,
        "processes_observed": len(all_pids),
        "participant_databases": {
            "per_site": True,
            "per_witness": True,
            "shared_ledger": False,
            "global_lock": False,
            "local_transaction": "SQLite BEGIN IMMEDIATE + WAL + synchronous=FULL",
        },
        "quorum": {"K": len(WITNESSES), "q": THRESHOLD, "f": 1},
        "all_passed": all(row["passed"] for row in rows),
        "failures": sum(not row["passed"] for row in rows),
        "zero_failure_exact_95pct_upper_bound": (
            1.0 - 0.05 ** (1.0 / trials)
            if all(row["passed"] for row in rows)
            else None
        ),
        "results": rows,
        "claim_boundary": (
            "Safety is conditional on at most f Byzantine notaries, durable local "
            "witness state, signature unforgeability, and canonical domain binding."
        ),
    }
    result_path = output / "distributed_rights_results.json"
    result_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    checksum = hashlib.sha256(result_path.read_bytes()).hexdigest()
    (output / "CHECKSUMS.sha256").write_text(
        f"{checksum}  distributed_rights_results.json\n", encoding="ascii"
    )
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--trials", type=int, default=100)
    args = parser.parse_args()
    if args.trials <= 0:
        parser.error("--trials must be positive")
    payload = run_distributed_rights_campaign(args.output, trials=args.trials)
    print(
        json.dumps(
            {
                "trials": payload["trials"],
                "processes_observed": payload["processes_observed"],
                "all_passed": payload["all_passed"],
                "upper_95": payload["zero_failure_exact_95pct_upper_bound"],
            }
        )
    )


if __name__ == "__main__":
    main()
