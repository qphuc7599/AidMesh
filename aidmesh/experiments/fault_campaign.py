"""Deterministic, laptop-native fault campaign for protocol safety boundaries."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from aidmesh.common.canonical import stable_hash
from aidmesh.common.errors import DuplicateError, ValidationError
from aidmesh.protocol.campaign import CampaignHeader, CommitteeConfig, SafetyDomain
from aidmesh.protocol.model import Voucher, witness_request_for
from aidmesh.protocol.risk import RiskRightsLedger
from aidmesh.protocol.risk_escrow import EscrowRiskLedger
from aidmesh.protocol.witness import InMemoryWitnessStore, Witness


@dataclass(frozen=True)
class FaultResult:
    fault_id: str
    expected: str
    observed: str
    safety_preserved: bool
    evidence: dict[str, object]


def _header() -> CampaignHeader:
    committee = CommitteeConfig("fault-committee", tuple(f"w{i}" for i in range(7)), 5, 2)
    return CampaignHeader(
        domain=SafetyDomain("fault-campaign", 1, 1, committee.committee_id),
        risk_cell="cell-a",
        committee=committee,
        authorized_sites=("site-a", "site-b"),
        fallback_categories=("food",),
        initial_risk_rights=(("site-a", 4), ("site-b", 4)),
        start_tick=0,
        end_tick=100,
        verifier_id="reference",
        policy_hash="frozen-policy",
    )


def _voucher(header: CampaignHeader) -> Voucher:
    return Voucher(header.domain, "cell-a", "food", 1, 1, "nullifier-1", "cred-1", b"proof")


def _signer(counter: dict[str, int]) -> Callable[[str, str], str]:
    def sign(signer_id: str, digest: str) -> str:
        counter["calls"] += 1
        return stable_hash({"signer": signer_id, "digest": digest}, domain="fault/signature")

    return sign


def run_fault_campaign() -> list[FaultResult]:
    """Exercise fail-closed behavior without Docker or process isolation."""

    header = _header()
    request_a = witness_request_for(_voucher(header), "site-a")
    request_b = witness_request_for(_voucher(header), "site-b")
    results: list[FaultResult] = []

    calls = {"calls": 0}

    def crash_before_commit(_key, _digest) -> None:
        raise RuntimeError("injected crash before durable commit")

    store = InMemoryWitnessStore(before_commit=crash_before_commit)
    witness = Witness("w0", header, store, _signer(calls))
    rejected = False
    try:
        witness.sign(request_a)
    except RuntimeError:
        rejected = True
    results.append(
        FaultResult(
            "F1_CRASH_BEFORE_WITNESS_COMMIT",
            "no signature and no durable decision",
            "fail_closed" if rejected and calls["calls"] == 0 and not store.journal else "unsafe",
            rejected and calls["calls"] == 0 and not store.journal,
            {"signature_calls": calls["calls"], "journal_entries": len(store.journal)},
        )
    )

    calls = {"calls": 0}
    store = InMemoryWitnessStore()
    witness = Witness("w0", header, store, _signer(calls))
    witness.sign(request_a)
    conflict_rejected = False
    try:
        witness.sign(request_b)
    except DuplicateError:
        conflict_rejected = True
    results.append(
        FaultResult(
            "F2_RESTART_EQUIVALENT_CONFLICT",
            "persisted first decision rejects conflicting request",
            "conflict_rejected" if conflict_rejected else "unsafe",
            conflict_rejected and calls["calls"] == 1 and len(store.journal) == 1,
            {"signature_calls": calls["calls"], "journal_entries": len(store.journal)},
        )
    )

    ledger = RiskRightsLedger(header.domain, header.risk_rights)
    stale = ledger.state_token("site-a")
    ledger.transfer(
        transfer_id="transfer-1",
        source_site="site-a",
        destination_site="site-b",
        amount=1,
        expected_source=stale,
        expected_destination=ledger.state_token("site-b"),
    )
    before = ledger.snapshot()
    stale_rejected = False
    try:
        ledger.debit(
            site_id="site-a",
            debit_id="debit-stale",
            nullifier="nullifier-stale",
            amount=1,
            expected_state=stale,
        )
    except ValidationError:
        stale_rejected = True
    after = ledger.snapshot()
    unchanged = before == after
    results.append(
        FaultResult(
            "F3_STALE_RISK_STATE",
            "compare-and-set rejects stale token without partial mutation",
            "atomic_rejection" if stale_rejected and unchanged else "unsafe",
            stale_rejected and unchanged and after.is_conserved,
            {
                "state_unchanged": unchanged,
                "remaining_total": after.remaining_total,
                "debited_total": after.debited_total,
            },
        )
    )

    epoch_rejected = False
    try:
        header.transition(epoch=1, header_version=2, policy_hash="attacker-policy")
    except ValidationError:
        epoch_rejected = True
    successor = header.transition(epoch=2, header_version=1, policy_hash="next-policy")
    CampaignHeader.validate_successor(header, successor)
    results.append(
        FaultResult(
            "F4_EPOCH_HANDOFF",
            "same-epoch safety change rejected; newer epoch accepted",
            "domain_separated" if epoch_rejected else "unsafe",
            epoch_rejected and successor.domain.epoch == 2,
            {
                "old_domain": header.domain.digest,
                "new_domain": successor.domain.digest,
                "domains_distinct": header.domain.digest != successor.domain.digest,
            },
        )
    )

    escrow = EscrowRiskLedger({"site-a": 4, "site-b": 0})
    reorder_rejected = False
    try:
        escrow.accept_transfer("not-yet-debited")
    except ValidationError:
        reorder_rejected = True
    results.append(
        FaultResult(
            "F5_TRANSFER_ACCEPT_BEFORE_DEBIT",
            "out-of-order accept cannot mint destination rights",
            "reorder_rejected" if reorder_rejected else "unsafe",
            reorder_rejected and escrow.snapshot().conserved,
            {"snapshot": asdict(escrow.snapshot())},
        )
    )

    escrow.begin_transfer("escrow-1", "site-a", "site-b", 2)
    replay = escrow.begin_transfer("escrow-1", "site-a", "site-b", 2)
    in_transit_before_accept = escrow.snapshot().in_transit
    escrow.accept_transfer("escrow-1")
    escrow.accept_transfer("escrow-1")
    replay_snapshot = escrow.snapshot()
    results.append(
        FaultResult(
            "F6_TRANSFER_REPLAY",
            "duplicate debit and accept are idempotent",
            (
                "exactly_once_credit"
                if replay_snapshot.balances == (("site-a", 2), ("site-b", 2))
                else "unsafe"
            ),
            in_transit_before_accept == 2
            and replay.amount == 2
            and replay_snapshot.conserved
            and replay_snapshot.balances == (("site-a", 2), ("site-b", 2)),
            {"snapshot": asdict(replay_snapshot)},
        )
    )

    terminal_ledger = EscrowRiskLedger({"site-a": 3, "site-b": 0})
    terminal_ledger.begin_transfer("escrow-expire", "site-a", "site-b", 1)
    terminal_ledger.terminate_transfer("escrow-expire")
    resurrection_rejected = False
    try:
        terminal_ledger.accept_transfer("escrow-expire")
    except ValidationError:
        resurrection_rejected = True
    terminal_snapshot = terminal_ledger.snapshot()
    results.append(
        FaultResult(
            "F7_EXPIRED_RIGHT_RESURRECTION",
            "expired rights never return to spendable state",
            "monotone_terminal" if resurrection_rejected else "unsafe",
            resurrection_rejected
            and terminal_snapshot.expired == 1
            and terminal_snapshot.conserved,
            {"snapshot": asdict(terminal_snapshot)},
        )
    )
    return results


def write_fault_campaign(output: Path) -> Path:
    output.mkdir(parents=True, exist_ok=True)
    results = run_fault_campaign()
    payload = {
        "campaign": "native_single_process_fault_injection_v1",
        "scope_limit": "not process-isolated and not a storage durability benchmark",
        "all_safety_preserved": all(item.safety_preserved for item in results),
        "results": [asdict(item) for item in results],
    }
    payload["evidence_hash"] = stable_hash(payload, domain="aidmesh/fault-campaign/v1")
    path = output / "fault_campaign.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
