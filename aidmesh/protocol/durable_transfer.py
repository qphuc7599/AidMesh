"""Per-site conserved escrow with atomic debit/outbox and terminal/inbox.

Research transport boundary: serialized authenticated envelopes, one DB per site.
Source sites are honest/non-rollbackable as in the bounded-risk model. Byzantine
witnesses are handled by RecoveryRules. This is not BPv7 or an EVM certificate.
"""

import json
import sqlite3

from aidmesh.protocol.terminal_recovery import RecoveryRules, canonical, digest


class DurableTransferSite:
    def __init__(
        self, path, *, site, domain, epoch, initial, sites, witnesses, faults, sign, verify
    ):
        if site not in sites or type(initial) is not int or initial < 0:
            raise ValueError("invalid site genesis")
        self.site, self.sign, self.verify = site, sign, verify
        self.config = dict(
            site=site,
            domain=domain,
            epoch=epoch,
            initial=initial,
            sites=sorted(sites),
            witnesses=sorted(witnesses),
            faults=faults,
        )
        RecoveryRules({"validation": True}, tuple(witnesses), faults, verify)
        self.db = sqlite3.connect(path, timeout=30, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY, config TEXT, balance INTEGER);
            CREATE TABLE IF NOT EXISTS transfers (id TEXT PRIMARY KEY, body TEXT, terminal TEXT);
            CREATE TABLE IF NOT EXISTS outbox (id TEXT PRIMARY KEY, body TEXT);
            CREATE TABLE IF NOT EXISTS inbox (id TEXT PRIMARY KEY, body TEXT);
        """)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute("SELECT config FROM state WHERE id=1").fetchone()
            if row is None:
                self.db.execute(
                    "INSERT INTO state VALUES(1,?,?)", (canonical(self.config), initial)
                )
            elif row[0] != canonical(self.config):
                raise ValueError("cannot change site genesis or committee")
            self.db.commit()
        except BaseException:
            self.db.rollback()
            self.db.close()
            raise

    def close(self):
        self.db.close()

    def balance(self):
        return self.db.execute("SELECT balance FROM state WHERE id=1").fetchone()[0]

    def _envelope(self, body):
        return canonical(
            {"body": body, "signer": self.site, "signature": self.sign(self.site, digest(body))}
        ).encode()

    def begin(self, transfer_id, destination, amount):
        if (
            not isinstance(transfer_id, str)
            or not transfer_id
            or destination == self.site
            or destination not in self.config["sites"]
            or type(amount) is not int
            or amount <= 0
        ):
            raise ValueError("invalid transfer")
        body = dict(
            profile="aidmesh/durable-transfer/v1",
            domain=self.config["domain"],
            epoch=self.config["epoch"],
            transfer=transfer_id,
            source=self.site,
            destination=destination,
            amount=amount,
        )
        key = digest(body)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            previous = self.db.execute(
                "SELECT body FROM transfers WHERE id=?", (transfer_id,)
            ).fetchone()
            if previous:
                if previous[0] != canonical(body):
                    raise ValueError("transfer id reused")
            else:
                if self.balance() < amount:
                    raise ValueError("insufficient rights")
                self.db.execute("UPDATE state SET balance=balance-? WHERE id=1", (amount,))
                self.db.execute(
                    "INSERT INTO transfers VALUES(?,?,NULL)", (transfer_id, canonical(body))
                )
                self.db.execute("INSERT INTO outbox VALUES(?,?)", (key, canonical(body)))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        return self._envelope(body)

    def pending(self):
        # Outbox retains immutable intents. Transport retries may have a new
        # signature encoding, but the canonical signed body never changes.
        return [
            self._envelope(json.loads(row[0]))
            for row in self.db.execute("SELECT body FROM outbox ORDER BY id")
        ]

    def decode(self, payload):
        envelope = json.loads(payload)
        if set(envelope) != {"body", "signer", "signature"}:
            raise ValueError("invalid envelope")
        body = envelope["body"]
        if set(body) != {
            "profile",
            "domain",
            "epoch",
            "transfer",
            "source",
            "destination",
            "amount",
        }:
            raise ValueError("invalid transfer fields")
        if (
            body["profile"] != "aidmesh/durable-transfer/v1"
            or body["domain"] != self.config["domain"]
            or body["epoch"] != self.config["epoch"]
            or body["source"] not in self.config["sites"]
            or body["destination"] not in self.config["sites"]
            or body["source"] == body["destination"]
            or type(body["amount"]) is not int
            or body["amount"] <= 0
            or not isinstance(body["transfer"], str)
            or not body["transfer"]
            or envelope["signer"] != body["source"]
            or not self.verify(body["source"], digest(body), envelope["signature"])
        ):
            raise ValueError("unauthenticated transfer")
        return body

    def recovery_rules(self, payload):
        return RecoveryRules(
            self.decode(payload),
            tuple(self.config["witnesses"]),
            self.config["faults"],
            self.verify,
        )

    def finalize(self, payload, committed):
        body = self.decode(payload)
        rules = self.recovery_rules(payload)
        _, decision = rules.certificate(committed, "commit")
        if self.site not in (body["source"], body["destination"]):
            raise ValueError("unrelated site")
        identity = digest([body["source"], body["transfer"]])
        record = canonical(dict(body=body, decision=decision))
        self.db.execute("BEGIN IMMEDIATE")
        try:
            previous = self.db.execute("SELECT body FROM inbox WHERE id=?", (identity,)).fetchone()
            if previous:
                if previous[0] != record:
                    raise ValueError("conflicting terminal delivery")
            else:
                if self.site == body["source"]:
                    transfer = self.db.execute(
                        "SELECT body FROM transfers WHERE id=?", (body["transfer"],)
                    ).fetchone()
                    if not transfer or transfer[0] != canonical(body):
                        raise ValueError("source debit not present")
                    self.db.execute(
                        "UPDATE transfers SET terminal=? WHERE id=?", (decision, body["transfer"])
                    )
                    self.db.execute("DELETE FROM outbox WHERE id=?", (digest(body),))
                credit = (decision == "accept" and self.site == body["destination"]) or (
                    decision == "reclaim" and self.site == body["source"]
                )
                if credit:
                    self.db.execute(
                        "UPDATE state SET balance=balance+? WHERE id=1", (body["amount"],)
                    )
                self.db.execute("INSERT INTO inbox VALUES(?,?)", (identity, record))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        return decision
