"""Crash-durable reference rights accounting using a transactional command journal.

One SQLite database is one ledger authority, not a distributed transaction across
sites. Replay is intentionally O(history); it is a correctness reference rather
than a throughput-optimized production store. Existing genesis cannot be reset.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from aidmesh.common.canonical import canonical_json
from aidmesh.common.errors import ValidationError
from aidmesh.protocol.risk import RiskRightsLedger, SiteStateToken


class SQLiteRiskRightsLedger:
    def __init__(self, domain, initial_rights: dict[str, int], db_path: Path | str):
        RiskRightsLedger(domain, initial_rights)  # Validate before persisting genesis.
        self.domain = domain
        self._initial = dict(initial_rights)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(db_path), isolation_level=None, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS rights_genesis("
            "id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS rights_commands("
            "seq INTEGER PRIMARY KEY, method TEXT NOT NULL, payload TEXT NOT NULL)"
        )
        genesis = canonical_json({"domain": domain, "initial": self._initial})
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            row = self._conn.execute("SELECT payload FROM rights_genesis WHERE id=1").fetchone()
            if row is None:
                if self._conn.execute("SELECT COUNT(*) FROM rights_commands").fetchone()[0]:
                    raise ValidationError("rights journal without genesis")
                self._conn.execute("INSERT INTO rights_genesis VALUES (1,?)", (genesis,))
            elif row[0] != genesis:
                raise ValidationError("durable rights genesis cannot be replaced")
            self._conn.commit()
        except BaseException:
            self._conn.rollback()
            self._conn.close()
            raise

    @staticmethod
    def _arguments(payload):
        kwargs = json.loads(payload)
        for key in ("expected_state", "expected_source", "expected_destination"):
            if kwargs.get(key) is not None:
                kwargs[key] = SiteStateToken(**kwargs[key])
        return kwargs

    def _access(self, method, kwargs=None, *, write=False):
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                model = RiskRightsLedger(self.domain, self._initial)
                rows = self._conn.execute(
                    "SELECT seq,method,payload FROM rights_commands ORDER BY seq"
                ).fetchall()
                for expected, (seq, operation, payload) in enumerate(rows):
                    if seq != expected or operation not in {"debit", "transfer"}:
                        raise ValidationError("invalid durable rights journal")
                    getattr(model, operation)(**self._arguments(payload))
                if method == "journal":
                    result = model.journal
                else:
                    result = getattr(model, method)(**(kwargs or {}))
                if write and len(model.journal) > len(rows):
                    self._conn.execute(
                        "INSERT INTO rights_commands VALUES (?,?,?)",
                        (len(rows), method, canonical_json(kwargs)),
                    )
                self._conn.commit()
                return result
            except BaseException:
                self._conn.rollback()
                raise

    def debit(self, **kwargs):
        return self._access("debit", kwargs, write=True)

    def transfer(self, **kwargs):
        return self._access("transfer", kwargs, write=True)

    def balance(self, site_id):
        return self._access("balance", {"site_id": site_id})

    def state_token(self, site_id):
        return self._access("state_token", {"site_id": site_id})

    def snapshot(self):
        return self._access("snapshot")

    def assert_conserved(self):
        return self._access("assert_conserved")

    @property
    def journal(self):
        return self._access("journal")

    def close(self):
        with self._lock:
            self._conn.close()
