"""Durable single-slot prepare/commit with certified view changes.

Research reference profile, inspired by PBFT (Castro/Liskov, OSDI 1999).
Not wire-compatible with the one-shot TerminalCertificate or the EVM gateway.
Each instance has immutable transfer context and committee; callbacks authenticate
domain-separated messages. A scheduler/transport must supply eventual honest
leadership and delivery. No timeout alone authorizes reclaim.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from pathlib import Path


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class RecoveryRules:
    def __init__(self, context: dict, members: tuple[str, ...], faults: int, verify):
        if (
            type(faults) is not int
            or faults < 0
            or len(members) < 3 * faults + 1
            or not members
            or len(set(members)) != len(members)
        ):
            raise ValueError("invalid Byzantine committee")
        if not context or any(not isinstance(x, str) or not x for x in members):
            raise ValueError("context and member identifiers required")
        self.members = tuple(sorted(members))
        self.threshold = (len(members) + faults) // 2 + 1
        self.context = json.loads(canonical(context))
        self.domain = digest(
            {
                "profile": "aidmesh/terminal-recovery/v1",
                "context": context,
                "members": self.members,
                "faults": faults,
            }
        )
        self.verify = verify

    def message(self, phase: str, view: int, value: str | None, proof=None) -> dict:
        if type(view) is not int or view < 0:
            raise ValueError("invalid view")
        return {"domain": self.domain, "phase": phase, "view": view, "value": value, "proof": proof}

    def check(self, envelope: dict, phase: str) -> dict:
        if set(envelope) != {"message", "signer", "signature"}:
            raise ValueError("invalid signed envelope")
        message = envelope["message"]
        if set(message) != {"domain", "phase", "view", "value", "proof"}:
            raise ValueError("invalid message fields")
        if (
            message["domain"] != self.domain
            or message["phase"] != phase
            or type(message["view"]) is not int
            or message["view"] < 0
            or envelope["signer"] not in self.members
            or not self.verify(envelope["signer"], digest(message), envelope["signature"])
        ):
            raise ValueError("unauthenticated or wrong-context message")
        return message

    def certificate(self, votes: list[dict], phase: str) -> tuple[int, str]:
        if phase not in ("prepare", "commit") or len(votes) < self.threshold:
            raise ValueError("insufficient certificate")
        signers = set()
        target = None
        for vote in votes:
            message = self.check(vote, phase)
            current = (message["view"], message["value"])
            if (
                message["proof"] is not None
                or message["value"] not in ("accept", "reclaim")
                or vote["signer"] in signers
                or (target is not None and current != target)
            ):
                raise ValueError("conflicting, repeated, or malformed certificate")
            signers.add(vote["signer"])
            target = current
        return target

    def selection(self, reports: list[dict], view: int) -> str | None:
        if view <= 0 or len(reports) < self.threshold:
            raise ValueError("new view needs a report quorum")
        signers, prepared = set(), []
        for report in reports:
            message = self.check(report, "view-change")
            if (
                message["view"] != view
                or message["value"] is not None
                or report["signer"] in signers
            ):
                raise ValueError("invalid view-change quorum")
            signers.add(report["signer"])
            if message["proof"] is not None:
                previous, value = self.certificate(message["proof"], "prepare")
                if previous >= view:
                    raise ValueError("prepared certificate is not from a prior view")
                prepared.append((previous, value))
        if not prepared:
            return None
        highest = max(v for v, _ in prepared)
        values = {value for v, value in prepared if v == highest}
        if len(values) != 1:
            raise ValueError("conflicting highest prepared certificates")
        return values.pop()


class DurableTerminalReplica:
    """One transfer slot per DB; serialize transitions with BEGIN IMMEDIATE.

    The database pins the profile, transfer context and replica identity. Immutable
    signed-message intentions survive a crash between commit and signing. Never
    import prior one-shot votes: migration requires a new, separately issued slot.
    """

    def __init__(self, path: str | Path, rules: RecoveryRules, member: str, sign: Callable):
        if member not in rules.members:
            raise ValueError("unknown replica")
        self.rules, self.member, self.sign = rules, member, sign
        self.db = sqlite3.connect(path, timeout=30, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY, body TEXT)")
        self.db.execute("CREATE TABLE IF NOT EXISTS signed (key TEXT PRIMARY KEY, body TEXT)")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute("SELECT body FROM state WHERE id=1").fetchone()
            if row is None:
                self._save(
                    {
                        "domain": rules.domain,
                        "member": member,
                        "view": 0,
                        "prepared": None,
                        "decided": None,
                    }
                )
            elif (json.loads(row[0])["domain"], json.loads(row[0])["member"]) != (
                rules.domain,
                member,
            ):
                raise ValueError("database belongs to another context or replica")
            self.db.commit()
        except BaseException:
            self.db.rollback()
            self.db.close()
            raise

    def close(self):
        self.db.close()

    def _state(self):
        return json.loads(self.db.execute("SELECT body FROM state WHERE id=1").fetchone()[0])

    def _save(self, state):
        self.db.execute("INSERT OR REPLACE INTO state VALUES (1, ?)", (canonical(state),))

    def _emit(self, phase, view, value, proof, transition):
        message = self.rules.message(phase, view, value, proof)
        key = f"{phase}:{view}"
        self.db.execute("BEGIN IMMEDIATE")
        try:
            state = self._state()
            # Even retries of old votes must not send after joining a newer view.
            if view < state["view"]:
                raise ValueError("stale view")
            row = self.db.execute("SELECT body FROM signed WHERE key=?", (key,)).fetchone()
            if row is not None:
                if row[0] != canonical(message):
                    raise ValueError("replica would equivocate")
            else:
                transition(state)
                self._save(state)
                self.db.execute("INSERT INTO signed VALUES (?, ?)", (key, canonical(message)))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        # Critical boundary: a signer exception/process death cannot erase intent.
        signature = self.sign(self.member, digest(message))
        if not signature:
            raise ValueError("empty signature")
        return {"message": message, "signer": self.member, "signature": signature}

    def propose(self, view: int, value: str, reports: list[dict] | None = None):
        if type(view) is not int or view < 0:
            raise ValueError("invalid view")
        if self.rules.members[view % len(self.rules.members)] != self.member:
            raise ValueError("only the view leader proposes")
        if value not in ("accept", "reclaim"):
            raise ValueError("invalid terminal decision")
        if view == 0:
            if reports is not None:
                raise ValueError("initial view has no reports")
        else:
            selected = self.rules.selection(reports or [], view)
            if selected is not None and value != selected:
                raise ValueError("proposal discards highest prepared value")

        def transition(state):
            if state["decided"] is not None and state["decided"] != value:
                raise ValueError("conflicts with decision")
            state["view"] = view

        return self._emit("proposal", view, value, reports, transition)

    def prepare(self, proposal: dict):
        message = self.rules.check(proposal, "proposal")
        view, value = message["view"], message["value"]
        if proposal["signer"] != self.rules.members[
            view % len(self.rules.members)
        ] or value not in ("accept", "reclaim"):
            raise ValueError("invalid leader or value")
        if view:
            selected = self.rules.selection(message["proof"] or [], view)
            if selected is not None and selected != value:
                raise ValueError("unsafe new-view selection")
        elif message["proof"] is not None:
            raise ValueError("invalid initial view")

        def transition(state):
            if state["decided"] is not None and state["decided"] != value:
                raise ValueError("conflicts with decision")
            state["view"] = view

        return self._emit("prepare", view, value, None, transition)

    def commit(self, prepared: list[dict]):
        view, value = self.rules.certificate(prepared, "prepare")

        def transition(state):
            if view != state["view"]:
                raise ValueError("must join view before commit")
            vote = self.db.execute(
                "SELECT body FROM signed WHERE key=?", (f"prepare:{view}",)
            ).fetchone()
            if vote is None or json.loads(vote[0])["value"] != value:
                raise ValueError("must prepare matching proposal before commit")
            if state["decided"] is not None and state["decided"] != value:
                raise ValueError("conflicts with decision")
            state["prepared"] = prepared

        return self._emit("commit", view, value, None, transition)

    def change_view(self, view: int):
        # Snapshot and report persistence must occur under the SAME write lock.
        self.db.execute("BEGIN IMMEDIATE")
        try:
            state = self._state()
            if type(view) is not int or view <= 0 or view < state["view"]:
                raise ValueError("view must advance")
            key = f"view-change:{view}"
            row = self.db.execute("SELECT body FROM signed WHERE key=?", (key,)).fetchone()
            if row:
                message = json.loads(row[0])
            else:
                if view == state["view"]:
                    raise ValueError("cannot report after already voting in target view")
                message = self.rules.message("view-change", view, None, state["prepared"])
                state["view"] = view
                self._save(state)
                self.db.execute("INSERT INTO signed VALUES (?, ?)", (key, canonical(message)))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        return {
            "message": message,
            "signer": self.member,
            "signature": self.sign(self.member, digest(message)),
        }

    def decide(self, committed: list[dict]):
        _, value = self.rules.certificate(committed, "commit")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            state = self._state()
            if state["decided"] is not None and state["decided"] != value:
                raise ValueError("conflicting decision")
            state["decided"] = value
            self._save(state)
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        return value
