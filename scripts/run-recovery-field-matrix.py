# ruff: noqa: E402 -- standalone script adds the repository to sys.path.
"""Exhaustive scalar-field tampering of both recovery decisions, off-chain gate.

Real Ed25519 and durable journals; authorization is an explicit unit fixture.
This does not replace the real-P1 Solidity attack campaign.
"""

import json
import sys
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from aidmesh.protocol.recovered_release import RecoveredReleaseSite
from aidmesh.protocol.recovery_gateway import RecoveryGateway
from aidmesh.protocol.release import ReleaseSlotKey
from aidmesh.protocol.terminal_recovery import DurableTerminalReplica, canonical


def leaves(value, path=()):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from leaves(child, path + (key,))
    elif isinstance(value, list):
        for key, child in enumerate(value):
            yield from leaves(child, path + (key,))
    else:
        yield path, value


def main():
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=False)
    rows = []
    members = tuple(f"w{i}" for i in range(4))
    keys = {m: Ed25519PrivateKey.generate() for m in (*members, "a", "b")}

    def sign(m, d):
        return keys[m].sign(bytes.fromhex(d)).hex()

    def verify(m, d, signature):
        try:
            keys[m].public_key().verify(bytes.fromhex(signature), bytes.fromhex(d))
            return True
        except (InvalidSignature, ValueError, KeyError, TypeError):
            return False

    for decision in ("accept", "reclaim"):
        directory = out / decision
        directory.mkdir()
        sites = {
            m: RecoveredReleaseSite(
                directory / f"{m}.db",
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
            for m in ("a", "b")
        }
        transfer = sites["a"].begin("transfer", "b", 1)
        owner = sites["b" if decision == "accept" else "a"]
        rules = owner.recovery_rules(transfer)
        replicas = [DurableTerminalReplica(directory / f"{m}.db", rules, m, sign) for m in members]
        proposal = replicas[0].propose(0, decision)
        prepared = [r.prepare(proposal) for r in replicas]
        commits = [r.commit(prepared) for r in replicas]
        owner.finalize(transfer, commits)
        voucher = {"unit_fixture": "NOT_REAL_P1"}
        receipt = owner.release(
            ReleaseSlotKey("D", 1, "n", "b" if decision == "accept" else "a"),
            value=1,
            mode="fallback",
            authorization=voucher,
            verify_authorization=lambda *_: True,
            actuator=lambda *_: "ack",
        )["receipt"]
        packet = dict(
            transfer=json.loads(transfer), commits=commits, receipt=receipt, voucher=voucher
        )
        (directory / "packet.json").write_text(json.dumps(packet, indent=2))

        def gateway(name, directory=directory, voucher=voucher):
            return RecoveryGateway(
                directory / f"gateway-{name}.db",
                domain="D",
                epoch=1,
                sites=("a", "b"),
                witnesses=members,
                faults=1,
                verify=verify,
                verify_voucher=lambda _, v: v == voucher,
            )

        for index, (path, value) in enumerate(leaves(packet)):
            changed = deepcopy(packet)
            parent = changed
            for key in path[:-1]:
                parent = parent[key]
            parent[path[-1]] = value + 1 if type(value) is int else str(value) + "!"
            g = gateway(str(index))
            rejected, error = False, None
            try:
                g.accept(canonical(changed))
            except (ValueError, TypeError, KeyError) as exc:
                rejected, error = True, type(exc).__name__ + ": " + str(exc)
            counts = {
                table: g.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("received", "spending", "winners")
            }
            g.close()
            rows.append(
                dict(
                    decision=decision,
                    field=list(path),
                    rejected=rejected,
                    state_unchanged=all(n == 0 for n in counts.values()),
                    error=error,
                )
            )
        g = gateway("valid")
        assert g.accept(canonical(packet))["credit"] == 1
        g.close()
        g = gateway("valid")
        assert g.accept(json.dumps(packet, indent=2))["credit"] == 0
        g.close()
        for s in sites.values():
            s.close()
    (out / "matrix.json").write_text(json.dumps(rows, indent=2))
    summary = dict(
        cases=len(rows),
        rejected=sum(r["rejected"] for r in rows),
        unchanged=sum(r["state_unchanged"] for r in rows),
        positive_profiles=2,
        restart_replay_profiles=2,
        scope=__doc__,
    )
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary))
    assert all(r["rejected"] and r["state_unchanged"] for r in rows)


if __name__ == "__main__":
    main()
