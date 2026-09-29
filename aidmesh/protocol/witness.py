"""Witness state machine with durable decision-before-sign discipline."""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from aidmesh.common.crypto import MessageSigner
from aidmesh.common.errors import DuplicateError, ValidationError

from .campaign import CampaignHeader
from .model import WitnessRequest, WitnessShare

WitnessKey = tuple[str, int, str]


class PersistOutcome(StrEnum):
    CREATED = "created"
    IDEMPOTENT = "idempotent"
    CONFLICT = "conflict"


class WitnessStateStore(Protocol):
    """Durable compare-and-set interface required before a signature is made."""

    def persist_once(self, key: WitnessKey, request_digest: str, /) -> PersistOutcome:
        """Durably commit the digest or report an existing decision."""

    def read(self, key: WitnessKey, /) -> str | None:
        """Read a previously durable decision."""


@dataclass(frozen=True)
class WitnessJournalEntry:
    sequence: int
    key: WitnessKey
    request_digest: str


class InMemoryWitnessStore:
    """Thread-safe experimental store implementing durable-store semantics.

    Production work must replace this with fsync/transactional persistence.  A
    ``before_commit`` hook supports crash-injection tests: an exception from the
    hook leaves no decision committed and therefore no signature can be made.
    """

    def __init__(self, before_commit=None) -> None:
        self._decisions: dict[WitnessKey, str] = {}
        self._journal: list[WitnessJournalEntry] = []
        self._lock = threading.RLock()
        self._before_commit = before_commit

    def persist_once(self, key: WitnessKey, request_digest: str) -> PersistOutcome:
        with self._lock:
            existing = self._decisions.get(key)
            if existing is not None:
                return (
                    PersistOutcome.IDEMPOTENT
                    if existing == request_digest
                    else PersistOutcome.CONFLICT
                )
            if self._before_commit is not None:
                self._before_commit(key, request_digest)
            # This assignment models the transaction commit point.  The caller
            # is allowed to invoke its signing interface only after return.
            self._decisions[key] = request_digest
            self._journal.append(WitnessJournalEntry(len(self._journal), key, request_digest))
            return PersistOutcome.CREATED

    def read(self, key: WitnessKey) -> str | None:
        with self._lock:
            return self._decisions.get(key)

    @property
    def journal(self) -> tuple[WitnessJournalEntry, ...]:
        with self._lock:
            return tuple(self._journal)


class SQLiteWitnessStore:
    """SQLite WAL witness store with durable compare-and-set semantics.

    The store uses one local SQLite database per witness.  Every first decision
    is inserted under ``BEGIN IMMEDIATE`` and committed before ``Witness.sign``
    can invoke its signer.  The audit table is intentionally simple so crash
    harnesses can check for sign-before-commit and post-restart decision loss.
    """

    def __init__(self, db_path: Path | str, *, signer_id: str, before_commit=None) -> None:
        self.db_path = Path(db_path)
        self.signer_id = signer_id
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._before_commit = before_commit
        self._conn = sqlite3.connect(self.db_path, isolation_level=None, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._initialize()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _initialize(self) -> None:
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=FULL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS witness_decision (
                    campaign        TEXT    NOT NULL,
                    epoch           INTEGER NOT NULL,
                    nullifier       TEXT    NOT NULL,
                    request_digest  TEXT    NOT NULL,
                    signer_id       TEXT    NOT NULL,
                    decided_at_ns   INTEGER NOT NULL,
                    PRIMARY KEY (campaign, epoch, nullifier)
                );

                CREATE TABLE IF NOT EXISTS witness_audit (
                    event_id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign        TEXT    NOT NULL,
                    epoch           INTEGER NOT NULL,
                    nullifier       TEXT    NOT NULL,
                    request_digest  TEXT    NOT NULL,
                    phase           TEXT    NOT NULL,
                    ts_unix_ns      INTEGER NOT NULL,
                    note            TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_witness_audit_lookup
                ON witness_audit(campaign, epoch, nullifier, ts_unix_ns);
                """
            )

    def _audit(
        self,
        key: WitnessKey,
        request_digest: str,
        phase: str,
        *,
        note: str | None = None,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO witness_audit(
                campaign, epoch, nullifier, request_digest, phase, ts_unix_ns, note
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (key[0], key[1], key[2], request_digest, phase, time.time_ns(), note),
        )

    def persist_once(self, key: WitnessKey, request_digest: str) -> PersistOutcome:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._audit(key, request_digest, "BEGIN_TX")
                row = self._conn.execute(
                    """
                    SELECT request_digest FROM witness_decision
                    WHERE campaign=? AND epoch=? AND nullifier=?
                    """,
                    key,
                ).fetchone()
                if row is not None:
                    if row["request_digest"] == request_digest:
                        self._audit(key, request_digest, "IDEMPOTENT")
                        self._conn.commit()
                        return PersistOutcome.IDEMPOTENT
                    self._audit(key, request_digest, "CONFLICT")
                    self._conn.rollback()
                    return PersistOutcome.CONFLICT

                self._conn.execute(
                    """
                    INSERT INTO witness_decision(
                        campaign, epoch, nullifier, request_digest, signer_id, decided_at_ns
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (key[0], key[1], key[2], request_digest, self.signer_id, time.time_ns()),
                )
                self._audit(key, request_digest, "INSERTED")
                if self._before_commit is not None:
                    self._before_commit(key, request_digest)
                self._audit(key, request_digest, "COMMIT_OK")
                self._conn.commit()
                return PersistOutcome.CREATED
            except BaseException:
                self._conn.rollback()
                self._audit(key, request_digest, "FAIL_CLOSED", note="rolled back before commit")
                raise

    def read(self, key: WitnessKey) -> str | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT request_digest FROM witness_decision
                WHERE campaign=? AND epoch=? AND nullifier=?
                """,
                key,
            ).fetchone()
            return None if row is None else str(row["request_digest"])

    def record_sign_ok(self, key: WitnessKey, request_digest: str) -> None:
        with self._lock:
            self._audit(key, request_digest, "SIGN_OK")

    def audit_counts(self) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT phase, COUNT(*) AS count FROM witness_audit GROUP BY phase"
            ).fetchall()
            return {str(row["phase"]): int(row["count"]) for row in rows}


class Witness:
    """One committee member bound to an immutable campaign header."""

    def __init__(
        self,
        witness_id: str,
        header: CampaignHeader,
        store: WitnessStateStore,
        signer: MessageSigner,
        *,
        request_validator=None,
    ) -> None:
        if witness_id not in header.committee.members:
            raise ValidationError("witness is not a member of this committee")
        self.witness_id = witness_id
        self.header = header
        self.store = store
        self._signer = signer
        self._request_validator = request_validator

    @staticmethod
    def state_key(request: WitnessRequest) -> WitnessKey:
        campaign, epoch = request.domain.nullifier_scope
        return campaign, epoch, request.nullifier

    def _validate(self, request: WitnessRequest) -> None:
        if request.domain != self.header.domain:
            raise ValidationError("witness request uses a stale or foreign safety domain")
        if request.risk_cell != self.header.risk_cell:
            raise ValidationError("witness request is bound to another risk cell")
        if request.site_id not in self.header.authorized_sites:
            raise ValidationError("request names an unauthorized site")
        if self._request_validator is not None and not self._request_validator(request):
            raise ValidationError("external witness request verifier rejected the request")

    def sign(self, request: WitnessRequest) -> WitnessShare:
        self._validate(request)
        key = self.state_key(request)
        outcome = self.store.persist_once(key, request.digest)
        if outcome is PersistOutcome.CONFLICT:
            raise DuplicateError(
                "witness already persisted a conflicting request for this nullifier"
            )

        # Persist-before-sign: no signature interface call occurs above this line.
        signature = self._signer(self.witness_id, request.digest)
        if not signature:
            raise ValidationError("signature interface returned an empty signature")
        if hasattr(self.store, "record_sign_ok"):
            self.store.record_sign_ok(key, request.digest)
        return WitnessShare(
            signer_id=self.witness_id,
            domain=request.domain,
            nullifier=request.nullifier,
            request_digest=request.digest,
            signature=signature,
        )
