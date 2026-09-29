from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from aidmesh.crypto.coconut_admission import AdmissionStatement
from aidmesh.protocol.coconut_online import CoconutOnlineLedger


def test_concurrent_online_redemption_has_one_winner_across_sites(tmp_path):
    statement = AdmissionStatement(1, 1, 1, 1, 1, 1, 1, 1)
    barrier = Barrier(2)

    def ledger(verify):
        return CoconutOnlineLedger(
            tmp_path / "ledger.db",
            campaign=1,
            epoch=1,
            deployment_context=1,
            sites=("a", "b"),
            verify=verify,
        )

    # Cryptographic predicate is a fixture here; real Coconut is tested by the Linux runner.
    def predicate(s, proof):
        barrier.wait(timeout=10)
        return s == statement and proof == "valid-fixture"

    def redeem(site):
        db = ledger(predicate)
        try:
            return db.redeem(statement, "valid-fixture", site=site)
        finally:
            db.close()

    # Provision the campaign before accepting clients. This test races redemption,
    # not simultaneous first-time SQLite journal-mode migration.
    initialized = ledger(lambda *_: True)
    initialized.close()
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(redeem, ("a", "b")))
    assert sum(r["credit"] for r in results) == 1
    db = ledger(lambda s, _: s == statement)
    assert db.redeem(statement, "valid-fixture", site="a")["credit"] == 0
    assert db.db.execute("SELECT COUNT(*) FROM redeemed").fetchone()[0] == 1
    db.close()


def test_unreachable_baseline_does_not_call_verifier_or_credit(tmp_path):
    def unexpected(*_):
        raise AssertionError("unreachable server must not verify")

    db = CoconutOnlineLedger(
        tmp_path / "ledger.db",
        campaign=1,
        epoch=1,
        deployment_context=1,
        sites=("a",),
        verify=unexpected,
    )
    assert db.redeem(None, None, site="a", reachable=False)["credit"] == 0
    assert db.db.execute("SELECT COUNT(*) FROM redeemed").fetchone()[0] == 0
    db.close()
