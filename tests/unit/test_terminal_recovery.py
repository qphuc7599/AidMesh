"""Adversarial recovery schedules, authenticated with independent Ed25519 keys."""

from copy import deepcopy
from itertools import combinations

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from aidmesh.protocol.terminal_recovery import DurableTerminalReplica, RecoveryRules, digest


@pytest.fixture
def cluster(tmp_path):
    keys = {f"w{i}": Ed25519PrivateKey.generate() for i in range(4)}

    def sign(member, message):
        return keys[member].sign(bytes.fromhex(message)).hex()

    def verify(member, message, signature):
        try:
            keys[member].public_key().verify(bytes.fromhex(signature), bytes.fromhex(message))
            return True
        except (InvalidSignature, ValueError, KeyError):
            return False

    rules = RecoveryRules(
        {"transfer": "t", "source": "a", "destination": "b", "epoch": 1, "amount": 1},
        tuple(keys),
        1,
        verify,
    )
    replicas = [DurableTerminalReplica(tmp_path / f"{m}.sqlite", rules, m, sign) for m in keys]
    yield rules, replicas, sign, tmp_path
    for replica in replicas:
        replica.close()


def complete(rules, replicas, proposal):
    prepares = [r.prepare(proposal) for r in replicas]
    commits = [r.commit(prepares) for r in replicas]
    assert rules.certificate(commits, "commit")[1] == proposal["message"]["value"]
    return commits


def test_split_vote_recovers_with_one_withholder(cluster):
    rules, replicas, sign, _ = cluster
    accept = replicas[0].propose(0, "accept")
    # Byzantine leader equivocates; no prepare quorum can exist for either value.
    reclaim = deepcopy(accept)
    reclaim["message"]["value"] = "reclaim"
    reclaim["signature"] = sign("w0", digest(reclaim["message"]))
    votes = [r.prepare(accept if i < 2 else reclaim) for i, r in enumerate(replicas)]
    with pytest.raises(ValueError):
        rules.certificate(votes, "prepare")
    honest = replicas[1:]
    reports = [r.change_view(1) for r in honest]
    proposal = replicas[1].propose(1, "reclaim", reports)
    commits = complete(rules, honest, proposal)
    assert all(r.decide(commits) == "reclaim" for r in replicas)
    with pytest.raises(ValueError):
        replicas[1].prepare(accept)


def test_prepared_lock_survives_restart_and_view_change(cluster):
    rules, replicas, sign, path = cluster
    proposal = replicas[0].propose(0, "accept")
    prepares = [r.prepare(proposal) for r in replicas]
    old_commits = [r.commit(prepares) for r in replicas[:3]]
    replicas[1].close()
    replicas[1] = DurableTerminalReplica(path / "w1.sqlite", rules, "w1", sign)
    reports = [r.change_view(1) for r in replicas[1:]]
    with pytest.raises(ValueError, match="highest prepared"):
        replicas[1].propose(1, "reclaim", reports)
    commits = complete(rules, replicas[1:], replicas[1].propose(1, "accept", reports))
    assert replicas[3].decide(old_commits) == "accept"  # delayed old certificate
    assert replicas[0].decide(commits) == "accept"


def test_prepared_but_uncommitted_can_safely_be_superseded(cluster):
    rules, replicas, _, _ = cluster
    proposal = replicas[0].propose(0, "accept")
    prepares = [r.prepare(proposal) for r in replicas]
    replicas[0].commit(prepares)  # only one commit, excluded by the next quorum
    reports = [r.change_view(1) for r in replicas[1:]]
    commits = complete(rules, replicas[1:], replicas[1].propose(1, "reclaim", reports))
    assert replicas[0].decide(commits) == "reclaim"
    with pytest.raises(ValueError):
        replicas[1].commit(prepares)


def test_forged_repeated_wrong_context_and_stale_reports(cluster):
    rules, replicas, _, _ = cluster
    proposal = replicas[0].propose(0, "accept")
    prepares = [r.prepare(proposal) for r in replicas]
    with pytest.raises(ValueError):
        rules.certificate([prepares[0]] * 3, "prepare")
    forged = deepcopy(prepares)
    forged[0]["message"]["value"] = "reclaim"
    with pytest.raises(ValueError):
        rules.certificate(forged, "prepare")
    foreign = RecoveryRules({"transfer": "other"}, rules.members, 1, rules.verify)
    with pytest.raises(ValueError):
        foreign.certificate(prepares, "prepare")
    reports = [r.change_view(1) for r in replicas]
    with pytest.raises(ValueError):
        rules.selection(reports, 2)
    with pytest.raises(ValueError):
        replicas[0].prepare(proposal)


def test_persist_before_signer_failure_and_context_pin(cluster):
    rules, replicas, sign, path = cluster
    replicas[0].sign = lambda *_: (_ for _ in ()).throw(RuntimeError("signer died"))
    with pytest.raises(RuntimeError):
        replicas[0].propose(0, "accept")
    replicas[0].close()
    replicas[0] = DurableTerminalReplica(path / "w0.sqlite", rules, "w0", sign)
    with pytest.raises(ValueError, match="equivocate"):
        replicas[0].propose(0, "reclaim")
    assert replicas[0].propose(0, "accept")["message"]["value"] == "accept"
    with pytest.raises(ValueError, match="another context"):
        DurableTerminalReplica(path / "w0.sqlite", rules, "w1", sign)


def test_every_new_view_quorum_preserves_committed_value(cluster):
    rules, replicas, _, _ = cluster
    proposal = replicas[0].propose(0, "accept")
    prepares = [r.prepare(proposal) for r in replicas]
    # Three committers imply every 3-of-4 report set includes an honest lock.
    for r in replicas[:3]:
        r.commit(prepares)
    reports = [r.change_view(1) for r in replicas]
    for quorum in combinations(reports, 3):
        assert rules.selection(list(quorum), 1) == "accept"


def test_process_death_after_prepare_intent_before_signature(cluster):
    import json
    import subprocess
    import sys

    rules, replicas, _, path = cluster
    proposal = replicas[0].propose(0, "accept")
    proposal_path = path / "proposal.json"
    proposal_path.write_text(json.dumps(proposal))
    # Child verifier is an explicitly labelled test double: authenticity is covered
    # by the other tests; this test isolates the actual OS process-exit boundary.
    child = """
import json, os, sys
from aidmesh.protocol.terminal_recovery import RecoveryRules, DurableTerminalReplica
rules = RecoveryRules(json.loads(sys.argv[3]), tuple("w"+str(i) for i in range(4)),
                      1, lambda *_: True)
r = DurableTerminalReplica(sys.argv[1], rules, "w3", lambda *_: os._exit(73))
r.prepare(json.load(open(sys.argv[2])))
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            child,
            str(path / "w3.sqlite"),
            str(proposal_path),
            json.dumps(rules.context),
        ],
        timeout=30,
    )
    assert result.returncode == 73
    assert replicas[3].prepare(proposal)["message"]["value"] == "accept"
    report = replicas[3].change_view(1)
    assert report["message"]["proof"] is None  # no prepared quorum was received
    with pytest.raises(ValueError, match="stale"):
        replicas[3].prepare(proposal)
