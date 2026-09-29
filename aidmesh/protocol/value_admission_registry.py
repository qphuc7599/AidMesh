"""Value-conserving, disjoint-serial admission (new fixed-domain v3 profile).

The credential authenticates the holder commitment and quota. The sealed snapshot
assigns each approved serial to one holder/local index; the v3 proof relation must
bind the same commitment into the entitlement leaf. This is not a v2 root input.
"""

import json
import sqlite3
from dataclasses import asdict

from aidmesh.crypto.coconut_admission import P1_FIELD
from aidmesh.protocol.terminal_recovery import canonical, digest


def _integer(v, name, positive=False, limit=2**64):
    if type(v) is not int or not (int(positive) <= v < limit):
        raise ValueError(f"invalid {name}")
    return v


class ValueAdmissionRegistry:
    PROFILE = "aidmesh/approved-registry/v3-holder-value"

    def __init__(self, path, *, campaign, epoch, deployment_context, allocations, verify):
        for name, value in (
            ("campaign", campaign),
            ("epoch", epoch),
            ("deployment_context", deployment_context),
        ):
            if type(value) is not int or not 0 <= value < P1_FIELD:
                raise ValueError(f"invalid {name}")
        clean, seen = [], set()
        for a in allocations:
            if set(a) != {"cell", "group", "category", "entitlements"}:
                raise ValueError("invalid allocation schema")
            coordinates = tuple(
                _integer(a[k], k, limit=P1_FIELD) for k in ("cell", "group", "category")
            )
            if coordinates in seen:
                raise ValueError("duplicate allocation")
            seen.add(coordinates)
            serials, entries = set(), []
            for e in a["entitlements"]:
                if set(e) != {"serial", "value"}:
                    raise ValueError("invalid entitlement schema")
                serial = _integer(e["serial"], "serial", True)
                value = _integer(e["value"], "value", True)
                if serial in serials:
                    raise ValueError("duplicate approved serial")
                serials.add(serial)
                entries.append({"serial": serial, "value": value})
            if not entries:
                raise ValueError("empty allocation")
            clean.append(
                {
                    **dict(zip(("cell", "group", "category"), coordinates, strict=True)),
                    "entitlements": sorted(entries, key=lambda e: e["serial"]),
                }
            )
        if not clean:
            raise ValueError("allocation required")
        self.policy = dict(
            profile=self.PROFILE,
            campaign=campaign,
            epoch=epoch,
            deployment_context=deployment_context,
            allocations=sorted(clean, key=canonical),
        )
        self.verify = verify
        self.db = sqlite3.connect(path, timeout=30, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS value_policy(id INTEGER PRIMARY KEY, body TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS value_admissions(
            id TEXT PRIMARY KEY, slot TEXT UNIQUE, body TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS value_assignments(cell TEXT, grp TEXT, category TEXT,
            serial TEXT, value TEXT NOT NULL, admission TEXT NOT NULL, idx INTEGER NOT NULL,
            PRIMARY KEY(cell,grp,category,serial), UNIQUE(admission,idx));
          CREATE TABLE IF NOT EXISTS value_publication(id INTEGER PRIMARY KEY, body TEXT NOT NULL);
        """)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute("SELECT body FROM value_policy WHERE id=1").fetchone()
            if row is None:
                self.db.execute("INSERT INTO value_policy VALUES(1,?)", (canonical(self.policy),))
            elif row[0] != canonical(self.policy):
                raise ValueError("cannot replace registry policy")
            self.db.commit()
        except BaseException:
            self.db.rollback()
            self.db.close()
            raise

    def close(self):
        self.db.close()

    def admit(self, statement, showing, *, serials):
        if any(
            getattr(statement, k) != self.policy[k]
            for k in ("campaign", "epoch", "deployment_context")
        ):
            raise ValueError("wrong domain")
        requested = tuple(_integer(s, "serial", True) for s in serials)
        if len(requested) != statement.quota or len(set(requested)) != len(requested):
            raise ValueError("quota/serial mismatch")
        coordinates = tuple(getattr(statement, k) for k in ("cell", "group", "category"))
        a = next(
            (
                a
                for a in self.policy["allocations"]
                if tuple(a[k] for k in ("cell", "group", "category")) == coordinates
            ),
            None,
        )
        if a is None:
            raise ValueError("unallocated group/category")
        approved = {e["serial"]: e["value"] for e in a["entitlements"]}
        if not set(requested) <= approved.keys():
            raise ValueError("unapproved serial")
        if not self.verify(statement, showing):
            raise ValueError("invalid credential admission")
        # Sorting fixes the local-index mapping independently of request ordering.
        body = dict(statement=asdict(statement), serials=sorted(requested))
        identifier = digest({"profile": self.PROFILE, **body})
        slot = canonical([statement.commitment, statement.category])
        self.db.execute("BEGIN IMMEDIATE")
        try:
            previous = self.db.execute(
                "SELECT id FROM value_admissions WHERE slot=?", (slot,)
            ).fetchone()
            if previous:
                if previous[0] != identifier:
                    raise ValueError("holder already admitted with another assignment")
                self.db.commit()
                return identifier
            if self.db.execute("SELECT 1 FROM value_publication").fetchone():
                raise ValueError("registry is sealed")
            c = tuple(map(str, coordinates))
            if any(
                self.db.execute(
                    "SELECT 1 FROM value_assignments "
                    "WHERE cell=? AND grp=? AND category=? AND serial=?",
                    (*c, str(s)),
                ).fetchone()
                for s in requested
            ):
                raise ValueError("serial already assigned")
            self.db.execute(
                "INSERT INTO value_admissions VALUES(?,?,?)", (identifier, slot, canonical(body))
            )
            self.db.executemany(
                "INSERT INTO value_assignments VALUES(?,?,?,?,?,?,?)",
                [
                    (*c, str(s), str(approved[s]), identifier, i)
                    for i, s in enumerate(sorted(requested), 1)
                ],
            )
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        return identifier

    def seal(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            previous = self.db.execute("SELECT body FROM value_publication WHERE id=1").fetchone()
            if previous:
                self.db.commit()
                return json.loads(previous[0])
            admissions = [
                {"id": r[0], **json.loads(r[1])}
                for r in self.db.execute("SELECT id,body FROM value_admissions ORDER BY id")
            ]
            if not admissions:
                raise ValueError("empty registry")
            holders = {a["id"]: a for a in admissions}
            entitlements = []
            for r in self.db.execute("SELECT * FROM value_assignments ORDER BY admission,idx"):
                cell, group, category, serial, value = map(int, r[:5])
                a, index = holders[r[5]], r[6]
                s = a["statement"]
                approved = next(
                    p
                    for p in self.policy["allocations"]
                    if (p["cell"], p["group"], p["category"]) == (cell, group, category)
                )
                if (s["cell"], s["group"], s["category"]) != (cell, group, category) or not (
                    1 <= index <= s["quota"]
                    and a["serials"][index - 1] == serial
                    and {"serial": serial, "value": value} in approved["entitlements"]
                ):
                    raise ValueError("corrupt assignment")
                entitlements.append(
                    dict(
                        cell=cell,
                        group=group,
                        category=category,
                        serial=serial,
                        value=value,
                        admission=a["id"],
                        index=index,
                        commitment=s["commitment"],
                    )
                )
            if any(
                sum(e["admission"] == a["id"] for e in entitlements) != a["statement"]["quota"]
                for a in admissions
            ):
                raise ValueError("incomplete admission")
            snapshot = dict(
                profile=self.PROFILE,
                policy=self.policy,
                admissions=admissions,
                entitlements=entitlements,
            )
            publication = {"snapshot": snapshot, "digest": digest(snapshot)}
            self.db.execute("INSERT INTO value_publication VALUES(1,?)", (canonical(publication),))
            self.db.commit()
            return publication
        except BaseException:
            self.db.rollback()
            raise
