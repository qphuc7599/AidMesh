import json
from copy import deepcopy

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from aidmesh.protocol.recovered_release import RecoveredReleaseSite
from aidmesh.protocol.recovery_gateway import RecoveryGateway
from aidmesh.protocol.release import ReleaseSlotKey
from aidmesh.protocol.terminal_recovery import DurableTerminalReplica, canonical, digest


def test_recovery_gateway_authentication_accounting_restart(tmp_path):
    members = tuple(f"w{i}" for i in range(4))
    keys = {m: Ed25519PrivateKey.generate() for m in (*members, "a", "b")}

    def sign(m, d):
        return keys[m].sign(bytes.fromhex(d)).hex()

    def verify(m, d, s):
        try:
            keys[m].public_key().verify(bytes.fromhex(s), bytes.fromhex(d))
            return True
        except (InvalidSignature, ValueError, KeyError):
            return False

    def site(m):
        return RecoveredReleaseSite(
            tmp_path / f"{m}.db",
            site=m,
            domain="D",
            epoch=1,
            initial=1 if m == "a" else 0,
            sites=("a", "b"),
            witnesses=members,
            faults=1,
            sign=sign,
            verify=verify,
        )

    a, b = site("a"), site("b")
    transfer = a.begin("t", "b", 1)
    rules = b.recovery_rules(transfer)
    witnesses = [DurableTerminalReplica(tmp_path / f"{m}.db", rules, m, sign) for m in members]
    proposal = witnesses[0].propose(0, "accept")
    prepared = [w.prepare(proposal) for w in witnesses]
    commits = [w.commit(prepared) for w in witnesses]
    b.finalize(transfer, commits)
    voucher = {"test_fixture": "no P1 in unit test"}
    receipt = b.release(
        ReleaseSlotKey("D", 1, "n", "b"),
        value=1,
        mode="fallback",
        authorization=voucher,
        verify_authorization=lambda *_: True,
        actuator=lambda *_: "ack",
    )["receipt"]
    packet = dict(transfer=json.loads(transfer), commits=commits, receipt=receipt, voucher=voucher)

    def gateway():
        return RecoveryGateway(
            tmp_path / "g.db",
            domain="D",
            epoch=1,
            sites=("a", "b"),
            witnesses=members,
            faults=1,
            verify=verify,
            verify_voucher=lambda _, v: v == voucher,
        )

    g = gateway()
    with pytest.raises(ValueError, match="duplicate JSON"):
        g.accept('{"transfer":0,"transfer":1}')
    changed = deepcopy(packet)
    changed["commits"] = changed["commits"][:2]
    with pytest.raises(ValueError):
        g.accept(canonical(changed))
    changed = deepcopy(packet)
    changed["voucher"] = {"test_fixture": "substituted"}
    with pytest.raises(ValueError, match="substitution"):
        g.accept(canonical(changed))
    assert g.accept(canonical(packet))["credit"] == 1
    g.close()
    g = gateway()
    assert g.accept(json.dumps(packet, indent=2))["credit"] == 0
    # A Byzantine site can sign a second receipt but cannot reuse a consumed right.
    changed = deepcopy(packet)
    body = changed["receipt"]["body"]
    body["statement"]["slot"]["nullifier"] = "different"
    body["release_id"] = digest(
        dict(profile="aidmesh/release-slot/v1", slot=body["statement"]["slot"])
    )
    changed["receipt"]["signature"] = sign("b", digest(body))
    with pytest.raises(ValueError, match="overspent"):
        g.accept(canonical(changed))
    assert g.db.execute("SELECT COUNT(*) FROM winners").fetchone()[0] == 1
    g.close()
    a.close()
    b.close()
    for w in witnesses:
        w.close()
