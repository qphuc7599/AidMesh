"""Durable release-intent state machine for irreversible physical handoff."""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from aidmesh.common.errors import DuplicateError, ValidationError


class ReleaseStatus(StrEnum):
    AUTHORIZED = "authorized"
    RELEASE_INTENT = "release_intent"
    AMBIGUOUS = "ambiguous"
    CLOSED = "closed"


@dataclass(frozen=True, order=True)
class ReleaseSlotKey:
    """Path-independent durable key for one voucher release at one site."""

    domain_id: str
    epoch: int
    nullifier: str
    site_id: str

    def __post_init__(self) -> None:
        if not self.domain_id or not self.nullifier or not self.site_id:
            raise ValidationError("release slot fields must be non-empty")
        if self.epoch < 1:
            raise ValidationError("release slot epoch must be positive")


@dataclass(frozen=True)
class ReleaseRecord:
    release_intent_id: str
    domain_id: str
    epoch: int
    nullifier: str
    site_id: str
    authorization_hash: str
    receipt_skeleton_hash: str
    reservation_id: str
    status: ReleaseStatus
    receipt_id: str = ""
    updated_at_ns: int = 0

    def __post_init__(self) -> None:
        required = (
            self.release_intent_id,
            self.domain_id,
            self.epoch,
            self.nullifier,
            self.site_id,
            self.authorization_hash,
            self.receipt_skeleton_hash,
            self.reservation_id,
        )
        if not all(required):
            raise ValidationError("release record fields must be non-empty")
        if self.epoch < 1:
            raise ValidationError("release record epoch must be positive")
        if self.status is ReleaseStatus.CLOSED and not self.receipt_id:
            raise ValidationError("closed release needs a receipt id")

    @property
    def slot_key(self) -> ReleaseSlotKey:
        return ReleaseSlotKey(self.domain_id, self.epoch, self.nullifier, self.site_id)

    def same_operation(self, other: ReleaseRecord) -> bool:
        return (
            self.release_intent_id,
            self.domain_id,
            self.epoch,
            self.nullifier,
            self.site_id,
            self.authorization_hash,
            self.receipt_skeleton_hash,
            self.reservation_id,
        ) == (
            other.release_intent_id,
            other.domain_id,
            other.epoch,
            other.nullifier,
            other.site_id,
            other.authorization_hash,
            other.receipt_skeleton_hash,
            other.reservation_id,
        )


class ReleaseStateStore(Protocol):
    """Durable state required before an irreversible handoff is attempted."""

    def authorize(self, record: ReleaseRecord, /) -> ReleaseRecord: ...

    def persist_intent(self, slot_key: ReleaseSlotKey, /) -> ReleaseRecord: ...

    def mark_ambiguous(self, slot_key: ReleaseSlotKey, /) -> ReleaseRecord: ...

    def close(self, slot_key: ReleaseSlotKey, receipt_id: str, /) -> ReleaseRecord: ...

    def read(self, slot_key: ReleaseSlotKey, /) -> ReleaseRecord | None: ...


def _transition(
    record: ReleaseRecord,
    wanted: ReleaseStatus,
    receipt_id: str = "",
) -> ReleaseRecord:
    allowed = {
        ReleaseStatus.AUTHORIZED: {ReleaseStatus.AUTHORIZED, ReleaseStatus.RELEASE_INTENT},
        ReleaseStatus.RELEASE_INTENT: {
            ReleaseStatus.RELEASE_INTENT,
            ReleaseStatus.AMBIGUOUS,
            ReleaseStatus.CLOSED,
        },
        ReleaseStatus.AMBIGUOUS: {ReleaseStatus.AMBIGUOUS},
        ReleaseStatus.CLOSED: {ReleaseStatus.CLOSED},
    }
    if wanted not in allowed[record.status]:
        raise ValidationError(f"invalid release transition {record.status} -> {wanted}")
    if record.status is wanted:
        if wanted is ReleaseStatus.CLOSED and receipt_id != record.receipt_id:
            raise DuplicateError("release intent was closed with another receipt")
        return record
    return replace(
        record,
        status=wanted,
        receipt_id=receipt_id,
        updated_at_ns=time.time_ns(),
    )


class InMemoryReleaseStore:
    """Thread-safe reference store with crash-injection hooks."""

    def __init__(self, *, before_intent_commit=None, before_close_commit=None) -> None:
        self._records: dict[ReleaseSlotKey, ReleaseRecord] = {}
        self._lock = threading.RLock()
        self._before_intent_commit = before_intent_commit
        self._before_close_commit = before_close_commit

    def authorize(self, record: ReleaseRecord) -> ReleaseRecord:
        if record.status is not ReleaseStatus.AUTHORIZED:
            raise ValidationError("new release record must start as authorized")
        with self._lock:
            existing = self._records.get(record.slot_key)
            if existing is not None:
                if not existing.same_operation(record):
                    raise DuplicateError(
                        "release slot already contains another authorization operation"
                    )
                return existing
            committed = replace(record, updated_at_ns=time.time_ns())
            self._records[record.slot_key] = committed
            return committed

    def persist_intent(self, slot_key: ReleaseSlotKey) -> ReleaseRecord:
        with self._lock:
            record = self._require(slot_key)
            if record.status is not ReleaseStatus.AUTHORIZED:
                raise DuplicateError("actuator claim has already been consumed")
            if record.status is ReleaseStatus.AUTHORIZED and self._before_intent_commit:
                self._before_intent_commit(record)
            updated = _transition(record, ReleaseStatus.RELEASE_INTENT)
            self._records[slot_key] = updated
            return updated

    def mark_ambiguous(self, slot_key: ReleaseSlotKey) -> ReleaseRecord:
        with self._lock:
            record = self._require(slot_key)
            updated = _transition(record, ReleaseStatus.AMBIGUOUS)
            self._records[slot_key] = updated
            return updated

    def close(self, slot_key: ReleaseSlotKey, receipt_id: str) -> ReleaseRecord:
        if not receipt_id:
            raise ValidationError("receipt_id is required")
        with self._lock:
            record = self._require(slot_key)
            if record.status is ReleaseStatus.RELEASE_INTENT and self._before_close_commit:
                self._before_close_commit(record, receipt_id)
            updated = _transition(record, ReleaseStatus.CLOSED, receipt_id)
            self._records[slot_key] = updated
            return updated

    def read(self, slot_key: ReleaseSlotKey) -> ReleaseRecord | None:
        with self._lock:
            return self._records.get(slot_key)

    def _require(self, slot_key: ReleaseSlotKey) -> ReleaseRecord:
        try:
            return self._records[slot_key]
        except KeyError as error:
            raise ValidationError("unknown release slot") from error


class SQLiteReleaseStore:
    """SQLite WAL implementation keyed by the path-independent release slot."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, isolation_level=None, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=FULL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS release_slot_v2 (
                    domain_id             TEXT NOT NULL,
                    epoch                 INTEGER NOT NULL,
                    nullifier             TEXT NOT NULL,
                    site_id               TEXT NOT NULL,
                    release_intent_id     TEXT NOT NULL UNIQUE,
                    authorization_hash    TEXT NOT NULL,
                    receipt_skeleton_hash TEXT NOT NULL,
                    reservation_id        TEXT NOT NULL,
                    status                TEXT NOT NULL CHECK (
                        status IN ('authorized', 'release_intent', 'ambiguous', 'closed')
                    ),
                    receipt_id             TEXT NOT NULL,
                    updated_at_ns          INTEGER NOT NULL,
                    PRIMARY KEY (domain_id, epoch, nullifier, site_id)
                )
                """
            )
            legacy = self._conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='release_state'"
            ).fetchone()
            if legacy is not None:
                legacy_rows = int(
                    self._conn.execute("SELECT COUNT(*) FROM release_state").fetchone()[0]
                )
                if legacy_rows:
                    raise ValidationError(
                        "legacy release_state rows require an explicit epoch-aware migration"
                    )

    def close_store(self) -> None:
        with self._lock:
            self._conn.close()

    @staticmethod
    def _from_row(row: sqlite3.Row) -> ReleaseRecord:
        return ReleaseRecord(
            release_intent_id=str(row["release_intent_id"]),
            domain_id=str(row["domain_id"]),
            epoch=int(row["epoch"]),
            nullifier=str(row["nullifier"]),
            site_id=str(row["site_id"]),
            authorization_hash=str(row["authorization_hash"]),
            receipt_skeleton_hash=str(row["receipt_skeleton_hash"]),
            reservation_id=str(row["reservation_id"]),
            status=ReleaseStatus(str(row["status"])),
            receipt_id=str(row["receipt_id"]),
            updated_at_ns=int(row["updated_at_ns"]),
        )

    def authorize(self, record: ReleaseRecord) -> ReleaseRecord:
        if record.status is not ReleaseStatus.AUTHORIZED:
            raise ValidationError("new release record must start as authorized")
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                existing = self._read_unlocked(record.slot_key)
                if existing is not None:
                    if not existing.same_operation(record):
                        raise DuplicateError(
                            "release slot already contains another authorization operation"
                        )
                    self._conn.commit()
                    return existing
                committed = replace(record, updated_at_ns=time.time_ns())
                self._conn.execute(
                    """
                    INSERT INTO release_slot_v2 (
                        domain_id, epoch, nullifier, site_id, release_intent_id,
                        authorization_hash, receipt_skeleton_hash, reservation_id,
                        status, receipt_id, updated_at_ns
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        committed.domain_id,
                        committed.epoch,
                        committed.nullifier,
                        committed.site_id,
                        committed.release_intent_id,
                        committed.authorization_hash,
                        committed.receipt_skeleton_hash,
                        committed.reservation_id,
                        committed.status.value,
                        committed.receipt_id,
                        committed.updated_at_ns,
                    ),
                )
                self._conn.commit()
                return committed
            except sqlite3.IntegrityError as error:
                self._conn.rollback()
                raise DuplicateError("release slot or intent already exists") from error
            except BaseException:
                self._conn.rollback()
                raise

    def _move(
        self,
        slot_key: ReleaseSlotKey,
        status: ReleaseStatus,
        receipt_id: str = "",
    ) -> ReleaseRecord:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                record = self._read_unlocked(slot_key)
                if record is None:
                    raise ValidationError("unknown release slot")
                if (
                    status is ReleaseStatus.RELEASE_INTENT
                    and record.status is not ReleaseStatus.AUTHORIZED
                ):
                    raise DuplicateError("actuator claim has already been consumed")
                updated = _transition(record, status, receipt_id)
                if updated is record:
                    self._conn.commit()
                    return record
                cursor = self._conn.execute(
                    """
                    UPDATE release_slot_v2
                    SET status=?, receipt_id=?, updated_at_ns=?
                    WHERE domain_id=? AND epoch=? AND nullifier=? AND site_id=? AND status=?
                    """,
                    (
                        updated.status.value,
                        updated.receipt_id,
                        updated.updated_at_ns,
                        slot_key.domain_id,
                        slot_key.epoch,
                        slot_key.nullifier,
                        slot_key.site_id,
                        record.status.value,
                    ),
                )
                if cursor.rowcount != 1:
                    raise DuplicateError("concurrent release transition")
                self._conn.commit()
            except BaseException:
                self._conn.rollback()
                raise
            return updated

    def persist_intent(self, slot_key: ReleaseSlotKey) -> ReleaseRecord:
        return self._move(slot_key, ReleaseStatus.RELEASE_INTENT)

    def mark_ambiguous(self, slot_key: ReleaseSlotKey) -> ReleaseRecord:
        return self._move(slot_key, ReleaseStatus.AMBIGUOUS)

    def close(self, slot_key: ReleaseSlotKey, receipt_id: str) -> ReleaseRecord:
        if not receipt_id:
            raise ValidationError("receipt_id is required")
        return self._move(slot_key, ReleaseStatus.CLOSED, receipt_id)

    def _read_unlocked(self, slot_key: ReleaseSlotKey) -> ReleaseRecord | None:
        row = self._conn.execute(
            """
            SELECT * FROM release_slot_v2
            WHERE domain_id=? AND epoch=? AND nullifier=? AND site_id=?
            """,
            (
                slot_key.domain_id,
                slot_key.epoch,
                slot_key.nullifier,
                slot_key.site_id,
            ),
        ).fetchone()
        return None if row is None else self._from_row(row)

    def read(self, slot_key: ReleaseSlotKey) -> ReleaseRecord | None:
        with self._lock:
            return self._read_unlocked(slot_key)
