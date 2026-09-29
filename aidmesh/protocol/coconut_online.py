"""Coconut-Online unit-token adaptation with one serializable redemption ledger.

One blindly issued credential per unit. The showing discloses the token's random
commitment while retaining the private holder attribute. This is distinct from
AidMesh's one quota credential plus indexed P1 proofs; issuance cost must be counted.
"""

import sqlite3

from aidmesh.protocol.terminal_recovery import canonical, digest


class CoconutOnlineLedger:
    def __init__(self, path, *, campaign, epoch, deployment_context, sites, verify):
        self.verify = verify
        self.policy = dict(
            campaign=campaign,
            epoch=epoch,
            deployment_context=deployment_context,
            sites=sorted(sites),
        )
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript(
            "CREATE TABLE IF NOT EXISTS policy(id INTEGER PRIMARY KEY,body TEXT);"
            "CREATE TABLE IF NOT EXISTS redeemed(token TEXT PRIMARY KEY,site TEXT);"
        )
        self.db.execute("BEGIN IMMEDIATE")
        try:
            old = self.db.execute("SELECT body FROM policy WHERE id=1").fetchone()
            if old and old[0] != canonical(self.policy):
                raise ValueError("policy change")
            self.db.execute("INSERT OR IGNORE INTO policy VALUES(1,?)", (canonical(self.policy),))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            self.db.close()
            raise

    def redeem(self, statement, showing, *, site, reachable=True):
        if not reachable:
            return dict(accepted=False, credit=0, reason="online ledger unreachable")
        if (
            site not in self.policy["sites"]
            or statement.quota != 1
            or any(
                getattr(statement, k) != self.policy[k]
                for k in ("campaign", "epoch", "deployment_context")
            )
            or not self.verify(statement, showing)
        ):
            raise ValueError("invalid Coconut token")
        token = digest(
            dict(
                campaign=statement.campaign,
                epoch=statement.epoch,
                context=statement.deployment_context,
                commitment=statement.commitment,
            )
        )
        self.db.execute("BEGIN IMMEDIATE")
        try:
            old = self.db.execute("SELECT site FROM redeemed WHERE token=?", (token,)).fetchone()
            if old:
                self.db.commit()
                return dict(accepted=False, credit=0, reason="already redeemed")
            self.db.execute("INSERT INTO redeemed VALUES(?,?)", (token, site))
            self.db.commit()
            return dict(accepted=True, credit=1, reason="valid first redemption")
        except BaseException:
            self.db.rollback()
            raise

    def close(self):
        self.db.close()
