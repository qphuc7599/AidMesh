import json
from copy import deepcopy
from dataclasses import asdict

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from aidmesh.protocol.recovered_release import RecoveredReleaseSite
from aidmesh.protocol.release import ReleaseSlotKey
from aidmesh.protocol.terminal_recovery import DurableTerminalReplica, digest


@pytest.fixture
def setup(tmp_path):
    members = tuple(f"w{i}" for i in range(4))
    keys = {m: Ed25519PrivateKey.generate() for m in (*members, "a", "b")}

    def sign(m, d):
        return keys[m].sign(bytes.fromhex(d)).hex()

    def verify(m, d, s):
        try:
            keys[m].public_key().verify(bytes.fromhex(s), bytes.fromhex(d))
            return True
        except (ValueError, InvalidSignature, KeyError):
            return False

    def site(name):
        return RecoveredReleaseSite(
            tmp_path / f"{name}.sqlite",
            site=name,
            domain="D",
            epoch=1,
            initial=2 if name == "a" else 0,
            sites=("a", "b"),
            witnesses=members,
            faults=1,
            sign=sign,
            verify=verify,
        )

    def authorize(slot, mode):
        statement = dict(
            profile="aidmesh/recovered-release/v1", slot=asdict(slot), value=1, mode=mode
        )
        return sign("w0", digest(statement))

    return site, sign, verify, authorize, members


@pytest.mark.parametrize("decision", ["accept", "reclaim"])
def test_recovery_credit_to_release_receipt_and_cross_path_stutter(tmp_path, setup, decision):
    factory, sign, verify, authorize, members = setup
    a, b = factory("a"), factory("b")
    payload = a.begin("t", "b", 2)
    rules = b.recovery_rules(payload)
    witnesses = [DurableTerminalReplica(tmp_path / f"{m}.db", rules, m, sign) for m in members]
    reports = [w.change_view(1) for w in witnesses]
    proposal = witnesses[1].propose(1, decision, reports)
    prepares = [w.prepare(proposal) for w in witnesses]
    commits = [w.commit(prepares) for w in witnesses]
    for site in (a, b):
        site.finalize(payload, commits)
    target = b if decision == "accept" else a
    name = target.site
    slot = ReleaseSlotKey("D", 1, "nullifier", name)
    calls = []

    def verify_auth(statement, signature):
        return verify("w0", digest(statement), signature)

    first = target.release(
        slot,
        value=1,
        mode="fallback",
        authorization=authorize(slot, "fallback"),
        verify_authorization=verify_auth,
        actuator=lambda k, v: calls.append((k, v)) or "ack",
    )
    assert first["invoked"] and target.balance() == 1
    assert target.verify_receipt(target.receipt_outbox()[0])["acknowledgment"] == "ack"
    target.close()
    target = factory(name)
    for mode in ("quorum", "fallback"):
        result = target.release(
            slot,
            value=1,
            mode=mode,
            authorization=authorize(slot, mode),
            verify_authorization=verify_auth,
            actuator=lambda *_: pytest.fail("duplicate invocation"),
        )
        assert not result["invoked"] and result["status"] == "closed"
    assert len(calls) == 1 and target.balance() == 1
    envelope = json.loads(target.receipt_outbox()[0])
    paths = [
        ("body", "release_id"),
        ("body", "authorization_digest"),
        ("body", "acknowledgment"),
        ("body", "statement", "mode"),
        ("body", "statement", "value"),
        *(("body", "statement", "slot", key) for key in asdict(slot)),
        ("signature",),
        ("signer",),
    ]
    for path in paths:
        altered = deepcopy(envelope)
        parent = altered
        for part in path[:-1]:
            parent = parent[part]
        old = parent[path[-1]]
        parent[path[-1]] = old + 1 if type(old) is int else old + "x"
        with pytest.raises(ValueError):
            target.verify_receipt(json.dumps(altered))
    target.close()
    (a if decision == "accept" else b).close()
    for w in witnesses:
        w.close()


def test_ambiguous_handoff_never_reinvoked_or_refunded(setup):
    factory, _, verify, authorize, _ = setup
    a = factory("a")
    slot = ReleaseSlotKey("D", 1, "n", "a")

    def failed(*_):
        raise RuntimeError("ack lost after handoff")

    with pytest.raises(RuntimeError):
        a.release(
            slot,
            value=1,
            mode="fallback",
            authorization=authorize(slot, "fallback"),
            verify_authorization=lambda s, a: verify("w0", digest(s), a),
            actuator=failed,
        )
    assert a.balance() == 1 and a.receipt_outbox() == []
    a.close()

    a = factory("a")
    a.recover_ambiguities()
    result = a.release(
        slot,
        value=1,
        mode="quorum",
        authorization=authorize(slot, "quorum"),
        verify_authorization=lambda s, a: verify("w0", digest(s), a),
        actuator=lambda *_: pytest.fail("ambiguous retry"),
    )
    assert not result["invoked"] and result["status"] == "ambiguous" and a.balance() == 1
    a.close()


@pytest.mark.parametrize("boundary", ["before_actuator", "after_actuator", "before_receipt_sign"])
def test_real_process_death_preserves_claim_and_debit(tmp_path, boundary):
    import subprocess
    import sys

    path = tmp_path / "site.sqlite"
    count = tmp_path / "actuator.log"
    child = """
import os,sys
from pathlib import Path
from aidmesh.protocol.recovered_release import RecoveredReleaseSite
from aidmesh.protocol.release import ReleaseSlotKey
database,counter,boundary=sys.argv[1:]
def sign(*_):
    if boundary=='before_receipt_sign':os._exit(73)
    return 'test-double-signature'
def actuator(*_):
    if boundary=='before_actuator':os._exit(73)
    with open(counter,'ab',buffering=0) as f:
        f.write(b'CALL\\n');os.fsync(f.fileno())
    if boundary=='after_actuator':os._exit(73)
    return 'ack'
s=RecoveredReleaseSite(database,site='a',domain='D',epoch=1,initial=1,
    sites=('a','b'),witnesses=('w0','w1','w2','w3'),faults=1,
    sign=sign,verify=lambda *_:True)
s.release(ReleaseSlotKey('D',1,'n','a'),value=1,mode='fallback',authorization='fixture',
          verify_authorization=lambda *_:True,actuator=actuator)
"""
    result = subprocess.run(
        [sys.executable, "-c", child, str(path), str(count), boundary], timeout=30
    )
    assert result.returncode == 73
    site = RecoveredReleaseSite(
        path,
        site="a",
        domain="D",
        epoch=1,
        initial=1,
        sites=("a", "b"),
        witnesses=("w0", "w1", "w2", "w3"),
        faults=1,
        sign=lambda *_: "fixture",
        verify=lambda *_: True,
    )
    site.recover_ambiguities()
    replay = site.release(
        ReleaseSlotKey("D", 1, "n", "a"),
        value=1,
        mode="quorum",
        authorization="fixture",
        verify_authorization=lambda *_: True,
        actuator=lambda *_: pytest.fail("reinvoked after process crash"),
    )
    assert replay["status"] == "ambiguous" and site.balance() == 0
    assert (count.read_bytes().count(b"CALL") if count.exists() else 0) == int(
        boundary != "before_actuator"
    )
    site.close()
