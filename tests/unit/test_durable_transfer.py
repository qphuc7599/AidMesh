import json

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from aidmesh.protocol.durable_transfer import DurableTransferSite
from aidmesh.protocol.terminal_recovery import DurableTerminalReplica


@pytest.mark.parametrize("decision", ["accept", "reclaim"])
def test_independent_site_databases_terminal_replay_restart(tmp_path, decision):
    members = tuple(f"w{i}" for i in range(4))
    keys = {name: Ed25519PrivateKey.generate() for name in (*members, "a", "b")}

    def sign(member, message):
        return keys[member].sign(bytes.fromhex(message)).hex()

    def verify(member, message, signature):
        try:
            keys[member].public_key().verify(bytes.fromhex(signature), bytes.fromhex(message))
            return True
        except (InvalidSignature, ValueError, KeyError):
            return False

    def site(name):
        return DurableTransferSite(
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

    source, destination = site("a"), site("b")
    payload = source.begin("t1", "b", 2)
    assert source.balance() == destination.balance() == 0
    source.close()
    source = site("a")
    assert source.pending() == [payload]
    assert source.begin("t1", "b", 2) == payload
    with pytest.raises(ValueError):
        source.begin("t2", "b", 1)
    changed = json.loads(payload)
    changed["body"]["amount"] = 3
    with pytest.raises(ValueError):
        destination.recovery_rules(json.dumps(changed).encode())
    rules = destination.recovery_rules(payload)
    witnesses = [DurableTerminalReplica(tmp_path / f"{m}.sqlite", rules, m, sign) for m in members]
    proposal = witnesses[0].propose(0, decision)
    prepares = [w.prepare(proposal) for w in witnesses[:3]]
    commits = [w.commit(prepares) for w in witnesses[:3]]
    destination.finalize(payload, commits)
    destination.close()
    destination = site("b")
    destination.finalize(payload, commits)
    source.finalize(payload, commits)
    source.finalize(payload, commits)
    assert source.balance() + destination.balance() == 2
    assert destination.balance() == (2 if decision == "accept" else 0)
    assert source.pending() == []
    for witness in witnesses:
        witness.close()
    source.close()
    destination.close()


def test_source_process_death_cannot_erase_debit_or_outbox(tmp_path):
    import subprocess
    import sys

    path = tmp_path / "source.sqlite"
    child = """
import os, sys
from aidmesh.protocol.durable_transfer import DurableTransferSite
s = DurableTransferSite(sys.argv[1], site="a", domain="D", epoch=1, initial=2,
    sites=("a","b"), witnesses=("w0","w1","w2","w3"), faults=1,
    sign=lambda *_: os._exit(73), verify=lambda *_: True)
s.begin("t", "b", 2)
"""
    result = subprocess.run([sys.executable, "-c", child, str(path)], timeout=30)
    assert result.returncode == 73
    source = DurableTransferSite(
        path,
        site="a",
        domain="D",
        epoch=1,
        initial=2,
        sites=("a", "b"),
        witnesses=("w0", "w1", "w2", "w3"),
        faults=1,
        sign=lambda _, message: message,
        verify=lambda *_: True,
    )
    assert source.balance() == 0
    assert len(source.pending()) == 1
    with pytest.raises(ValueError, match="insufficient"):
        source.begin("other", "b", 1)
    source.close()
