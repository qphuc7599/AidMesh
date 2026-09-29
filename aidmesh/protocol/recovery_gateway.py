"""Strict receiver for the recovery/release profile with durable reconciliation.

This is an off-chain gateway, not the Solidity verifier. The configured verifier
checks P1/context; site and witness keys must come from a trusted campaign roster.
No trust is placed in a roster supplied inside a received bundle.
"""

import json
import sqlite3

from aidmesh.protocol.release import ReleaseSlotKey
from aidmesh.protocol.terminal_recovery import RecoveryRules, digest


class RecoveryGateway:
    def __init__(self, path, *, domain, epoch, sites, witnesses, faults, verify, verify_voucher):
        self.domain, self.epoch, self.sites = domain, epoch, tuple(sites)
        self.witnesses, self.faults = tuple(witnesses), faults
        self.verify, self.verify_voucher = verify, verify_voucher
        self.db = sqlite3.connect(path, isolation_level=None, timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS config (id INTEGER PRIMARY KEY, digest TEXT);
          CREATE TABLE IF NOT EXISTS received (id TEXT PRIMARY KEY, packet TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS spending (transfer TEXT, release TEXT UNIQUE, amount INTEGER);
          CREATE TABLE IF NOT EXISTS winners (nullifier TEXT PRIMARY KEY, release TEXT UNIQUE);
        """)
        config = digest(
            dict(
                domain=domain,
                epoch=epoch,
                sites=sorted(sites),
                witnesses=sorted(witnesses),
                faults=faults,
            )
        )
        self.db.execute("BEGIN IMMEDIATE")
        try:
            old = self.db.execute("SELECT digest FROM config WHERE id=1").fetchone()
            if old and old[0] != config:
                raise ValueError("gateway context changed")
            self.db.execute("INSERT OR IGNORE INTO config VALUES(1,?)", (config,))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            self.db.close()
            raise

    def close(self):
        self.db.close()

    def accept(self, raw):
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate JSON key")
                result[key] = value
            return result

        packet = json.loads(raw, object_pairs_hook=unique)
        if set(packet) != {"transfer", "commits", "receipt", "voucher"}:
            raise ValueError("packet schema")
        transfer = packet["transfer"]
        receipt = packet["receipt"]
        if set(transfer) != {"body", "signer", "signature"} or set(receipt) != {
            "body",
            "signer",
            "signature",
        }:
            raise ValueError("envelope schema")
        body = transfer["body"]
        if set(body) != {
            "profile",
            "domain",
            "epoch",
            "transfer",
            "source",
            "destination",
            "amount",
        }:
            raise ValueError("transfer schema")
        if (
            body["profile"] != "aidmesh/durable-transfer/v1"
            or body["domain"] != self.domain
            or type(body["epoch"]) is not int
            or body["epoch"] != self.epoch
            or type(body["amount"]) is not int
            or body["amount"] <= 0
            or body["source"] not in self.sites
            or body["destination"] not in self.sites
            or body["source"] == body["destination"]
            or transfer["signer"] != body["source"]
            or not isinstance(body["transfer"], str)
            or not body["transfer"]
            or not self.verify(transfer["signer"], digest(body), transfer["signature"])
        ):
            raise ValueError("transfer authentication")
        rules = RecoveryRules(body, self.witnesses, self.faults, self.verify)
        _, decision = rules.certificate(packet["commits"], "commit")
        owner = body["destination"] if decision == "accept" else body["source"]
        r = receipt["body"]
        if set(r) != {
            "profile",
            "release_id",
            "statement",
            "authorization_digest",
            "acknowledgment",
        }:
            raise ValueError("receipt schema")
        statement = r["statement"]
        if set(statement) != {"profile", "slot", "value", "mode"}:
            raise ValueError("statement schema")
        slot = ReleaseSlotKey(**statement["slot"])
        if (
            r["profile"] != "aidmesh/recovered-receipt/v1"
            or statement["profile"] != "aidmesh/recovered-release/v1"
            or statement["mode"] != "fallback"
            or slot.site_id != owner
            or slot.domain_id != self.domain
            or slot.epoch != self.epoch
            or type(statement["value"]) is not int
            or statement["value"] <= 0
            or not isinstance(r["acknowledgment"], str)
            or not r["acknowledgment"]
            or r["release_id"]
            != digest(dict(profile="aidmesh/release-slot/v1", slot=statement["slot"]))
            or receipt["signer"] != owner
            or not self.verify(owner, digest(r), receipt["signature"])
        ):
            raise ValueError("release authentication/binding")
        if r["authorization_digest"] != digest(packet["voucher"]):
            raise ValueError("voucher substitution")
        if not self.verify_voucher(statement, packet["voucher"]):
            raise ValueError("invalid voucher")
        transfer_id = digest(body)
        release = r["release_id"]
        amount = statement["value"]
        # Same receipt through a different serialization is the same operation.
        packet_id = digest(packet)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            old = self.db.execute("SELECT packet FROM received WHERE id=?", (release,)).fetchone()
            if old:
                if old[0] != packet_id:
                    raise ValueError("release evidence changed")
                self.db.commit()
                return dict(release=release, new=False, credit=0)
            charged = self.db.execute(
                "SELECT COALESCE(SUM(amount),0) FROM spending WHERE transfer=?", (transfer_id,)
            ).fetchone()[0]
            if charged + amount > body["amount"]:
                raise ValueError("recovered right overspent")
            self.db.execute("INSERT INTO spending VALUES(?,?,?)", (transfer_id, release, amount))
            winner = self.db.execute(
                "SELECT release FROM winners WHERE nullifier=?", (slot.nullifier,)
            ).fetchone()
            credit = 0 if winner else amount
            if not winner:
                self.db.execute("INSERT INTO winners VALUES(?,?)", (slot.nullifier, release))
            self.db.execute("INSERT INTO received VALUES(?,?)", (release, packet_id))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        return dict(release=release, new=True, credit=credit)
