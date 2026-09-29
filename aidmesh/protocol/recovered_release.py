"""Recovery-funded, cross-path release and durable receipt outbox.

Inherits the independently stored site balance credited by verified recovery commit
certificates. All fallback debit and actuator-claim changes use that same SQLite
transaction. Only the winning caller may invoke the actuator. Lost acknowledgements
remain ambiguous forever unless an external reconciliation protocol resolves them.
"""

import json
from dataclasses import asdict

from aidmesh.protocol.durable_transfer import DurableTransferSite
from aidmesh.protocol.release import ReleaseSlotKey
from aidmesh.protocol.terminal_recovery import canonical, digest


class RecoveredReleaseSite(DurableTransferSite):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS release_claims (
                slot TEXT PRIMARY KEY, body TEXT NOT NULL, status TEXT NOT NULL,
                receipt TEXT);
            CREATE TABLE IF NOT EXISTS release_events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, body TEXT NOT NULL);
        """)

    def release(
        self, slot: ReleaseSlotKey, *, value, mode, authorization, verify_authorization, actuator
    ):
        """Verify exact structured statement before taking the path-independent CAS.

        The verification callback must validate the full original voucher relation
        and quorum evidence when applicable. No default accept-all callback exists.
        """
        if (
            slot.site_id != self.site
            or slot.domain_id != self.config["domain"]
            or slot.epoch != self.config["epoch"]
            or type(value) is not int
            or value <= 0
            or mode not in ("quorum", "fallback")
        ):
            raise ValueError("invalid release domain, value or path")
        statement = dict(
            profile="aidmesh/recovered-release/v1", slot=asdict(slot), value=value, mode=mode
        )
        if not verify_authorization(statement, authorization):
            raise ValueError("invalid release authorization")
        key = digest(dict(profile="aidmesh/release-slot/v1", slot=asdict(slot)))
        body = dict(statement=statement, authorization=authorization, release_id=key)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            previous = self.db.execute(
                "SELECT body,status,receipt FROM release_claims WHERE slot=?", (key,)
            ).fetchone()
            if previous:
                original = json.loads(previous[0])["statement"]
                if original["value"] != value:
                    raise ValueError("same entitlement cannot change value")
                self.db.commit()
                return dict(
                    invoked=False,
                    status=previous[1],
                    receipt=None if previous[2] is None else json.loads(previous[2]),
                )
            if mode == "fallback":
                if self.balance() < value:
                    raise ValueError("insufficient recovered/static rights")
                self.db.execute("UPDATE state SET balance=balance-? WHERE id=1", (value,))
            self.db.execute(
                "INSERT INTO release_claims VALUES(?,?,?,NULL)", (key, canonical(body), "intent")
            )
            self.db.execute(
                "INSERT INTO release_events(body) VALUES(?)",
                (canonical(dict(kind="claim", **body)),),
            )
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        # No other caller (including after process restart) can reach this branch.
        try:
            acknowledgment = actuator(key, value)
            if not isinstance(acknowledgment, str) or not acknowledgment:
                raise ValueError("missing actuator acknowledgment")
            receipt = dict(
                profile="aidmesh/recovered-receipt/v1",
                release_id=key,
                statement=statement,
                authorization_digest=digest(authorization),
                acknowledgment=acknowledgment,
            )
            signature = self.sign(self.site, digest(receipt))
            if not signature:
                raise ValueError("empty receipt signature")
            envelope = dict(body=receipt, signer=self.site, signature=signature)
            self.db.execute("BEGIN IMMEDIATE")
            self.db.execute(
                "UPDATE release_claims SET status=?,receipt=? WHERE slot=?",
                ("closed", canonical(envelope), key),
            )
            self.db.execute(
                "INSERT INTO release_events(body) VALUES(?)",
                (canonical(dict(kind="closed", receipt=envelope)),),
            )
            self.db.commit()
            return dict(invoked=True, status="closed", receipt=envelope)
        except BaseException:
            self.db.rollback()
            with self.db:
                self.db.execute("UPDATE release_claims SET status='ambiguous' WHERE slot=?", (key,))
            raise

    def receipt_outbox(self):
        return [
            row[0].encode()
            for row in self.db.execute(
                "SELECT receipt FROM release_claims WHERE status='closed' ORDER BY slot"
            )
        ]

    def recover_ambiguities(self):
        # Never infer non-delivery from process death or lack of receipt.
        self.db.execute("UPDATE release_claims SET status='ambiguous' WHERE status='intent'")

    def verify_receipt(self, payload):
        envelope = json.loads(payload)
        if set(envelope) != {"body", "signer", "signature"}:
            raise ValueError("invalid receipt fields")
        body = envelope["body"]
        if set(body) != {
            "profile",
            "release_id",
            "statement",
            "authorization_digest",
            "acknowledgment",
        }:
            raise ValueError("invalid receipt body")
        statement = body["statement"]
        if set(statement) != {"profile", "slot", "value", "mode"}:
            raise ValueError("invalid statement fields")
        slot = ReleaseSlotKey(**statement["slot"])
        expected = digest(dict(profile="aidmesh/release-slot/v1", slot=asdict(slot)))
        if (
            body["profile"] != "aidmesh/recovered-receipt/v1"
            or statement["profile"] != "aidmesh/recovered-release/v1"
            or slot.domain_id != self.config["domain"]
            or slot.epoch != self.config["epoch"]
            or slot.site_id not in self.config["sites"]
            or envelope["signer"] != slot.site_id
            or body["release_id"] != expected
            or type(statement["value"]) is not int
            or statement["value"] <= 0
            or statement["mode"] not in ("quorum", "fallback")
            or not body["acknowledgment"]
            or not self.verify(envelope["signer"], digest(body), envelope["signature"])
        ):
            raise ValueError("invalid receipt binding/signature")
        return body
