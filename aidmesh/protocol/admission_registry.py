"""Transactional admission and unique entitlement issuance before root publication.

Credential verification is injected (Coconut in the integration runner). SQLite
stores approved statements, never holder secrets/showings. Quota counts indexed
vouchers, while value is fixed by the allocation policy. No root is silently
recomputed from unapproved external leaves.
"""

import json
import sqlite3
from dataclasses import asdict

from aidmesh.crypto.coconut_admission import AdmissionStatement
from aidmesh.protocol.terminal_recovery import canonical, digest


class AdmissionRegistry:
    def __init__(
        self,
        path,
        *,
        campaign,
        epoch,
        deployment_context,
        allocations,
        verify,
        multi_beneficiary=False,
    ):
        # allocations: cell/group/category, number of indexed vouchers, unit value.
        clean = []
        keys = set()
        for allocation in allocations:
            if set(allocation) != {"cell", "group", "category", "quota", "value"}:
                raise ValueError("invalid allocation fields")
            if any(type(v) is not int or v < 0 for v in allocation.values()):
                raise ValueError("allocation must use nonnegative integers")
            if allocation["quota"] <= 0 or allocation["value"] <= 0:
                raise ValueError("quota and value must be positive")
            key = tuple(allocation[k] for k in ("cell", "group", "category"))
            if key in keys:
                raise ValueError("duplicate allocation")
            keys.add(key)
            clean.append(dict(allocation))
        if not clean:
            raise ValueError("allocation required")
        self.policy = {
            "campaign": campaign,
            "epoch": epoch,
            "deployment_context": deployment_context,
            "allocations": sorted(clean, key=canonical),
        }
        self.multi_beneficiary = bool(multi_beneficiary)
        if self.multi_beneficiary:
            self.policy["issuance_profile"] = "per-holder-index/v2"
        self.verify = verify
        self.db = sqlite3.connect(path, timeout=30, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS policy (id INTEGER PRIMARY KEY, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS admissions (
                id TEXT PRIMARY KEY, slot TEXT UNIQUE NOT NULL, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS entitlements (
                cell TEXT, grp TEXT, category TEXT, idx INTEGER,
                value TEXT NOT NULL, admission TEXT NOT NULL,
                PRIMARY KEY(cell,grp,category,idx));
            CREATE TABLE IF NOT EXISTS publication (id INTEGER PRIMARY KEY, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS holder_entitlements (
                cell TEXT, grp TEXT, category TEXT, idx INTEGER,
                value TEXT NOT NULL, admission TEXT NOT NULL,
                PRIMARY KEY(admission,idx));
        """)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute("SELECT body FROM policy WHERE id=1").fetchone()
            if row is None:
                self.db.execute("INSERT INTO policy VALUES(1,?)", (canonical(self.policy),))
            elif row[0] != canonical(self.policy):
                raise ValueError("cannot replace registry policy after restart")
            self.db.commit()
        except BaseException:
            self.db.rollback()
            self.db.close()
            raise

    def close(self):
        self.db.close()

    def admit(self, statement: AdmissionStatement, showing):
        if any(
            getattr(statement, key) != self.policy[key]
            for key in ("campaign", "epoch", "deployment_context")
        ):
            raise ValueError("wrong campaign, epoch or deployment")
        allocation = next(
            (
                a
                for a in self.policy["allocations"]
                if all(a[k] == getattr(statement, k) for k in ("cell", "group", "category"))
            ),
            None,
        )
        if allocation is None:
            raise ValueError("unallocated group/category")
        if not self.verify(statement, showing):
            raise ValueError("invalid credential admission")
        body = asdict(statement)
        admission = digest({"profile": "aidmesh/approved-admission/v1", "statement": body})
        # A commitment cannot request changed quotas or another group in this epoch/category.
        slot = canonical([statement.commitment, statement.category])
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute("SELECT id FROM admissions WHERE slot=?", (slot,)).fetchone()
            if row:
                if row[0] != admission:
                    raise ValueError("commitment already admitted with different policy")
                self.db.commit()
                return admission
            if self.db.execute("SELECT 1 FROM publication").fetchone():
                raise ValueError("registry is sealed")
            coordinates = tuple(
                str(v) for v in (statement.cell, statement.group, statement.category)
            )
            table = "holder_entitlements" if self.multi_beneficiary else "entitlements"
            issued = self.db.execute(
                f"SELECT COUNT(*) FROM {table} WHERE cell=? AND grp=? AND category=?",
                coordinates,
            ).fetchone()[0]
            if issued and not self.multi_beneficiary:
                raise ValueError("this profile permits one credential per allocation slot")
            if issued + statement.quota > allocation["quota"]:
                raise ValueError("admission exceeds allocated quota")
            self.db.execute(
                "INSERT INTO admissions VALUES(?,?,?)", (admission, slot, canonical(body))
            )
            self.db.executemany(
                f"INSERT INTO {table} VALUES(?,?,?,?,?,?)",
                [
                    (*coordinates, index, str(allocation["value"]), admission)
                    for index in range(1, statement.quota + 1)
                ],
            )
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        return admission

    def seal(self):
        """Freeze approved leaves and quota mapping atomically; root worker consumes this.

        This snapshot digest is NOT a P1 Poseidon root nor a threshold endorsement.
        """
        self.db.execute("BEGIN IMMEDIATE")
        try:
            previous = self.db.execute("SELECT body FROM publication WHERE id=1").fetchone()
            if previous:
                self.db.commit()
                return json.loads(previous[0])
            admissions = [
                {"id": row[0], "statement": json.loads(row[1])}
                for row in self.db.execute("SELECT id,body FROM admissions ORDER BY id")
            ]
            if not admissions:
                raise ValueError("cannot seal empty registry")
            table = "holder_entitlements" if self.multi_beneficiary else "entitlements"
            entitlements = [
                dict(
                    zip(
                        ("cell", "group", "category", "index", "value", "admission"),
                        (*(int(v) for v in row[:5]), row[5]),
                        strict=True,
                    )
                )
                for row in self.db.execute(
                    f"SELECT * FROM {table} ORDER BY cell,grp,category,admission,idx"
                )
            ]
            snapshot = {
                "profile": (
                    "aidmesh/approved-registry/v2"
                    if self.multi_beneficiary
                    else "aidmesh/approved-registry/v1"
                ),
                "policy": self.policy,
                "admissions": admissions,
                "entitlements": entitlements,
            }
            publication = {"snapshot": snapshot, "digest": digest(snapshot)}
            self.db.execute("INSERT INTO publication VALUES(1,?)", (canonical(publication),))
            self.db.commit()
            return publication
        except BaseException:
            self.db.rollback()
            raise
