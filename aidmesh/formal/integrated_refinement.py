"""Executable simulation relation for composed trace-to-EVM event logs.

The abstraction function maps one concrete event identity to the TLA+ stages
``proofOK``, ``authorization``, ``releases`` and ``credits``.  This is a bounded,
trace-based refinement check; it complements, but does not replace, the
exhaustive finite-state TLC result.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from aidmesh.common.canonical import stable_hash

ACTION_MAP = {
    ("groth16-local-evm", "verifyProof", "verified"): "VerifyDomainBoundProof",
    ("quorum", "assemble-certificate", "authorized"): "AuthorizeQuorum",
    ("risk-right", "spend", "authorized"): "AuthorizeFallback",
    ("inventory", "release", "committed"): "Release",
    ("local-evm", "verifyAndSettle", "settled"): "GatewayAccept;Settle",
}
ABSTRACT_STAGE = {
    "VerifyDomainBoundProof": 0,
    "AuthorizeQuorum": 1,
    "AuthorizeFallback": 1,
    "Release": 2,
    "GatewayAccept;Settle": 3,
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_events(path: Path) -> list[dict[str, Any]]:
    events = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                events.append(json.loads(line))
    return sorted(events, key=lambda item: int(item["sequence"]))


def _check_prefix_simulation(
    path: Path,
    events: list[dict[str, Any]],
    by_event: dict[str, list[dict[str, Any]]],
) -> dict[str, int | bool]:
    """Check every concrete prefix against the projected composed state machine."""

    event_nullifier: dict[str, str] = {}
    for event_id, rows in by_event.items():
        nullifiers = {
            str(row["circuit_nullifier"])
            for row in rows
            if row.get("circuit_nullifier") is not None
        }
        if len(nullifiers) > 1:
            raise AssertionError(
                f"event identity has ambiguous circuit nullifier: {path} {event_id}"
            )
        if nullifiers:
            event_nullifier[event_id] = next(iter(nullifiers))

    projected: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "proof_ok": False,
            "quorum_authorized": False,
            "fallback_debit_ids": set(),
            "quorum_releases": 0,
            "fallback_releases": 0,
            "settlements": 0,
        }
    )
    local: dict[str, dict[str, bool]] = defaultdict(
        lambda: {
            "proof_seen": False,
            "quorum_authorized": False,
            "fallback_authorized": False,
            "released": False,
        }
    )
    debit_owner: dict[str, str] = {}
    mapped_steps = 0
    stuttering_steps = 0
    for row in events:
        action = ACTION_MAP.get((row["component"], row["action"], row["outcome"]))
        if action is None:
            stuttering_steps += 1
            continue
        mapped_steps += 1
        event_id = str(row["event_id"])
        if event_id not in event_nullifier:
            raise AssertionError(
                f"mapped event lacks a circuit nullifier: {path} {event_id}"
            )
        nullifier = event_nullifier[event_id]
        state = projected[nullifier]
        local_state = local[event_id]
        if action == "VerifyDomainBoundProof":
            state["proof_ok"] = True
            local_state["proof_seen"] = True
        elif action == "AuthorizeQuorum":
            if not local_state["proof_seen"]:
                raise AssertionError(
                    f"quorum authorization precedes proof: {path} {event_id}"
                )
            state["quorum_authorized"] = True
            local_state["quorum_authorized"] = True
        elif action == "AuthorizeFallback":
            if not local_state["proof_seen"]:
                raise AssertionError(
                    f"fallback authorization precedes proof: {path} {event_id}"
                )
            debit_id = stable_hash(event_id, domain="aidmesh/e2e/spend/v1")
            if debit_id in debit_owner:
                raise AssertionError(
                    f"fallback debit id is counted twice: {path} {debit_id}"
                )
            debit_owner[debit_id] = nullifier
            state["fallback_debit_ids"].add(debit_id)
            local_state["fallback_authorized"] = True
        elif action == "Release":
            if not local_state["proof_seen"]:
                raise AssertionError(f"release precedes proof: {path} {event_id}")
            path_label = str(row.get("path", ""))
            if path_label == "quorum":
                if not local_state["quorum_authorized"]:
                    raise AssertionError(
                        f"quorum release lacks local authorization: {path} {event_id}"
                    )
                state["quorum_releases"] += 1
            elif path_label == "fallback":
                if not local_state["fallback_authorized"]:
                    raise AssertionError(
                        f"fallback release lacks local debit: {path} {event_id}"
                    )
                state["fallback_releases"] += 1
            else:
                raise AssertionError(
                    f"release has no recognized path label: {path} {event_id}"
                )
            local_state["released"] = True
        elif action == "GatewayAccept;Settle":
            if not (local_state["proof_seen"] and local_state["released"]):
                raise AssertionError(
                    f"settlement does not refine local release: {path} {event_id}"
                )
            state["settlements"] += 1

        if state["fallback_releases"] > len(state["fallback_debit_ids"]):
            raise AssertionError(
                f"prefix fallback release exceeds debit: {path} {nullifier}"
            )
        if state["quorum_releases"] > 1:
            raise AssertionError(
                f"prefix quorum at-most-once release violated: {path} {nullifier}"
            )
        if state["settlements"] > 1:
            raise AssertionError(
                f"prefix settlement at-most-once violated: {path} {nullifier}"
            )
    return {
        "projected_mapped_steps": mapped_steps,
        "projected_stuttering_steps": stuttering_steps,
        "projected_nullifiers": len(projected),
        "prefix_simulation_holds": True,
    }


def check_event_log(
    path: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    events = _read_events(path)
    by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        by_event[str(event["event_id"])].append(event)
    prefix_summary = _check_prefix_simulation(path, events, by_event)

    relation_rows: list[dict[str, Any]] = []
    settled_nullifiers: set[str] = set()
    per_nullifier: dict[str, dict[str, int]] = defaultdict(
        lambda: {
            "proof_events": 0,
            "quorum_authorizations": 0,
            "fallback_debits": 0,
            "quorum_releases": 0,
            "fallback_releases": 0,
            "settlements": 0,
        }
    )
    debit_ids_by_nullifier: dict[str, set[str]] = defaultdict(set)
    debit_owner: dict[str, str] = {}
    verified = authorized = released = settled = 0
    for event_id, concrete in sorted(by_event.items()):
        proof_rows = [
            row
            for row in concrete
            if (row["component"], row["action"], row["outcome"])
            == ("groth16-local-evm", "verifyProof", "verified")
        ]
        authorization_rows = [
            row
            for row in concrete
            if (row["action"], row["outcome"])
            in {("assemble-certificate", "authorized"), ("spend", "authorized")}
        ]
        release_rows = [
            row
            for row in concrete
            if (row["component"], row["action"], row["outcome"])
            == ("inventory", "release", "committed")
        ]
        settlement_rows = [
            row
            for row in concrete
            if (row["component"], row["action"], row["outcome"])
            == ("local-evm", "verifyAndSettle", "settled")
        ]
        proof_ok = bool(proof_rows)
        is_authorized = bool(authorization_rows)
        is_released = bool(release_rows)
        is_settled = bool(settlement_rows)
        if is_released and not (proof_ok and is_authorized):
            raise AssertionError(f"release does not refine authorization: {path} {event_id}")
        if is_settled and not (proof_ok and is_released):
            raise AssertionError(f"settlement does not refine release: {path} {event_id}")
        ordered_actions = [
            (
                int(row["sequence"]),
                ACTION_MAP.get((row["component"], row["action"], row["outcome"])),
            )
            for row in concrete
            if ACTION_MAP.get((row["component"], row["action"], row["outcome"]))
        ]
        stage_trace = [ABSTRACT_STAGE[action] for _, action in ordered_actions]
        if stage_trace != sorted(stage_trace):
            raise AssertionError(
                f"concrete actions violate abstract stage order: {path} {event_id}"
            )
        if is_released:
            if max(int(row["sequence"]) for row in proof_rows) >= min(
                int(row["sequence"]) for row in release_rows
            ):
                raise AssertionError(f"proof verification occurs after release: {path} {event_id}")
            if max(int(row["sequence"]) for row in authorization_rows) >= min(
                int(row["sequence"]) for row in release_rows
            ):
                raise AssertionError(f"authorization occurs after release: {path} {event_id}")
        if is_settled and max(int(row["sequence"]) for row in release_rows) >= min(
            int(row["sequence"]) for row in settlement_rows
        ):
            raise AssertionError(f"settlement occurs before release: {path} {event_id}")
        nullifiers = {
            str(row["circuit_nullifier"])
            for row in proof_rows + release_rows + settlement_rows
            if row.get("circuit_nullifier") is not None
        }
        if (is_released or is_settled) and len(nullifiers) != 1:
            raise AssertionError(
                f"event identity has ambiguous circuit nullifier: {path} {event_id}"
            )
        if nullifiers:
            nullifier = next(iter(nullifiers))
            state = per_nullifier[nullifier]
            state["proof_events"] += len(proof_rows)
            quorum_authorizations = [
                row for row in authorization_rows if row["action"] == "assemble-certificate"
            ]
            fallback_debits = [row for row in authorization_rows if row["action"] == "spend"]
            fallback_debit_ids = {
                stable_hash(
                    str(row["event_id"]),
                    domain="aidmesh/e2e/spend/v1",
                )
                for row in fallback_debits
            }
            if len(fallback_debit_ids) != len(fallback_debits):
                raise AssertionError(
                    f"fallback authorization reuses a debit id: {path} {event_id}"
                )
            for debit_id in fallback_debit_ids:
                previous_nullifier = debit_owner.get(debit_id)
                if previous_nullifier is not None and previous_nullifier != nullifier:
                    raise AssertionError(
                        f"fallback debit id crosses nullifiers: {path} {debit_id}"
                    )
                if debit_id in debit_ids_by_nullifier[nullifier]:
                    raise AssertionError(
                        f"fallback debit id is counted twice: {path} {debit_id}"
                    )
                debit_owner[debit_id] = nullifier
                debit_ids_by_nullifier[nullifier].add(debit_id)
            if release_rows and bool(quorum_authorizations) == bool(fallback_debits):
                raise AssertionError(
                    f"release must select exactly one authorization path: {path} {event_id}"
                )
            state["quorum_authorizations"] += len(quorum_authorizations)
            state["fallback_debits"] += len(fallback_debit_ids)
            state["quorum_releases"] += len(release_rows) * int(bool(quorum_authorizations))
            state["fallback_releases"] += len(release_rows) * int(bool(fallback_debits))
            state["settlements"] += len(settlement_rows)
            if settlement_rows:
                if nullifier in settled_nullifiers:
                    raise AssertionError(f"at-most-once settlement violated: {path} {nullifier}")
                settled_nullifiers.add(nullifier)
        verified += int(proof_ok)
        authorized += int(is_authorized)
        released += int(is_released)
        settled += int(is_settled)
        relation_rows.append(
            {
                "event_log": path.as_posix(),
                "event_id": event_id,
                "abstract_proof_ok": proof_ok,
                "abstract_authorized": is_authorized,
                "abstract_released": is_released,
                "abstract_settled": is_settled,
                "derived_fallback_debit_ids": ";".join(sorted(fallback_debit_ids))
                if nullifiers
                else "",
                "concrete_sequences": ";".join(
                    f"{sequence}:{action}" for sequence, action in ordered_actions
                ),
                "simulation_relation_holds": True,
            }
        )
    nullifier_rows: list[dict[str, Any]] = []
    duplicate_physical_releases = 0
    fallback_debits = fallback_releases = quorum_releases = 0
    for nullifier, state in sorted(per_nullifier.items()):
        total_releases = state["quorum_releases"] + state["fallback_releases"]
        if state["fallback_releases"] > state["fallback_debits"]:
            raise AssertionError(f"fallback release exceeds conserved debit: {path} {nullifier}")
        if state["quorum_releases"] > 1:
            raise AssertionError(f"quorum at-most-once release violated: {path} {nullifier}")
        if state["settlements"] > 1:
            raise AssertionError(f"settlement duplicated: {path} {nullifier}")
        duplicate_physical_releases += max(0, total_releases - 1)
        fallback_debits += state["fallback_debits"]
        fallback_releases += state["fallback_releases"]
        quorum_releases += state["quorum_releases"]
        nullifier_rows.append(
            {
                "event_log": path.as_posix(),
                "circuit_nullifier": nullifier,
                **state,
                "distinct_fallback_debit_ids": len(debit_ids_by_nullifier[nullifier]),
                "physical_releases": total_releases,
                "duplicate_physical_releases": max(0, total_releases - 1),
                "fallback_release_leq_debit": (
                    state["fallback_releases"] <= state["fallback_debits"]
                ),
                "quorum_at_most_once_release": state["quorum_releases"] <= 1,
                "settlement_at_most_once": state["settlements"] <= 1,
                "simulation_relation_holds": True,
            }
        )
    return (
        {
            "events": len(events),
            "event_identities": len(by_event),
            "verified": verified,
            "authorized": authorized,
            "released": released,
            "settled": settled,
            "unique_settled_nullifiers": len(settled_nullifiers),
            "fallback_debits": fallback_debits,
            "fallback_releases": fallback_releases,
            "quorum_releases": quorum_releases,
            "duplicate_physical_releases": duplicate_physical_releases,
            **prefix_summary,
        },
        relation_rows,
        nullifier_rows,
    )


def check_campaign(campaign: Path, output: Path) -> dict[str, Any]:
    manifest_path = campaign / "e2e_campaign_manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"campaign manifest missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    registry = manifest.get("registry", {})
    profile = registry.get("profile") or manifest.get("profile")
    if profile != "aidmesh-voucher-p1-groth16-bn128-domain-v2-tagged":
        raise AssertionError(f"unexpected refinement profile: {profile!r}")
    logs = sorted((campaign / "runs").rglob("events.jsonl"))
    if not logs:
        raise FileNotFoundError(f"no E2E event logs under {campaign / 'runs'}")
    aggregate = defaultdict(int)
    rows: list[dict[str, Any]] = []
    nullifier_rows: list[dict[str, Any]] = []
    nonempty = 0
    for path in logs:
        summary, relation, nullifier_relation = check_event_log(path)
        nonempty += int(summary["events"] > 0)
        for key, value in summary.items():
            aggregate[key] += int(value)
        rows.extend(relation)
        nullifier_rows.extend(nullifier_relation)
    output.mkdir(parents=True, exist_ok=True)
    csv_path = output / "integrated_simulation_relation.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else [])
        if rows:
            writer.writeheader()
            writer.writerows(rows)
    nullifier_csv_path = output / "integrated_nullifier_relation.csv"
    with nullifier_csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=list(nullifier_rows[0]) if nullifier_rows else []
        )
        if nullifier_rows:
            writer.writeheader()
            writer.writerows(nullifier_rows)
    result = {
        "schema_version": 1,
        "formal_model": "formal/AidMeshIntegrated.tla",
        "campaign": str(campaign),
        "campaign_manifest_sha256": _sha256(manifest_path),
        "profile": profile,
        "event_logs": len(logs),
        "nonempty_event_logs": nonempty,
        **dict(aggregate),
        "explicit_simulation_relation": {
            "proofOK": "verified groth16-local-evm/verifyProof",
            "authorized": "quorum/assemble-certificate OR risk-right/spend",
            "released": "inventory/release committed",
            "settled": "local-evm/verifyAndSettle settled",
            "fallback_bounded_release": (
                "per-nullifier fallback releases <= authorized conserved right spends"
            ),
            "quorum_at_most_once_release": "per-nullifier quorum releases <= 1",
        },
        "all_concrete_traces_refine_composed_model": True,
        "scope": "bounded trace refinement, not an unbounded mechanized proof",
        "relation_csv_sha256": _sha256(csv_path),
        "nullifier_relation_csv_sha256": _sha256(nullifier_csv_path),
    }
    summary_path = output / "integrated_refinement_summary.json"
    summary_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = check_campaign(args.campaign, args.output)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
