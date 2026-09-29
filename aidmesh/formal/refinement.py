"""Protocol-to-code refinement checks for the safety-critical state machines.

The transition oracles in this module intentionally do not call implementation
helpers.  Each generated action is applied to an independent, small state
machine and to the production Python code; settlement actions are additionally
replayed against the pinned Solidity adapter on a local EVM.
"""

from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from aidmesh.common.errors import ProtocolError
from aidmesh.protocol.campaign import CampaignHeader, CommitteeConfig, SafetyDomain
from aidmesh.protocol.contract import ContractModel, ContractVerifierInterfaces
from aidmesh.protocol.model import FallbackEvidence, Receipt, RedemptionPath, Voucher
from aidmesh.protocol.risk_escrow import EscrowRiskLedger
from aidmesh.protocol.witness import PersistOutcome, SQLiteWitnessStore


@dataclass(frozen=True)
class EscrowAction:
    kind: str
    operation_id: str
    source: str = "a"
    destination: str = "b"
    amount: int = 1


class _EscrowOracle:
    def __init__(self, balances: dict[str, int]) -> None:
        self.balances = dict(balances)
        self.issued = sum(balances.values())
        self.transfers: dict[str, tuple[str, str, int, str]] = {}
        self.spends: dict[str, tuple[str, int]] = {}
        self.spent = 0
        self.expired = 0
        self.revoked = 0
        self.reclaimed = 0

    def apply(self, action: EscrowAction) -> bool:
        if action.kind == "begin_transfer":
            if (
                not action.operation_id
                or action.source == action.destination
                or action.amount <= 0
                or action.source not in self.balances
                or action.destination not in self.balances
            ):
                return False
            existing = self.transfers.get(action.operation_id)
            fields = (action.source, action.destination, action.amount)
            if existing is not None:
                return existing[:3] == fields
            if self.balances[action.source] < action.amount:
                return False
            self.balances[action.source] -= action.amount
            self.transfers[action.operation_id] = (*fields, "in_transit")
            return True
        if action.kind == "accept_transfer":
            existing = self.transfers.get(action.operation_id)
            if existing is None:
                return False
            source, destination, amount, status = existing
            if status == "accepted":
                return True
            if status != "in_transit":
                return False
            self.balances[destination] += amount
            self.transfers[action.operation_id] = (source, destination, amount, "accepted")
            return True
        if action.kind == "reclaim_transfer":
            existing = self.transfers.get(action.operation_id)
            if existing is None:
                return False
            source, destination, amount, status = existing
            if status == "reclaimed":
                return True
            if status != "in_transit":
                return False
            self.balances[source] += amount
            self.reclaimed += amount
            self.transfers[action.operation_id] = (source, destination, amount, "reclaimed")
            return True
        if action.kind in {"expire_transfer", "revoke_transfer"}:
            existing = self.transfers.get(action.operation_id)
            if existing is None:
                return False
            source, destination, amount, status = existing
            wanted = "revoked" if action.kind == "revoke_transfer" else "expired"
            if status == wanted:
                return True
            if status != "in_transit":
                return False
            if wanted == "revoked":
                self.revoked += amount
            else:
                self.expired += amount
            self.transfers[action.operation_id] = (source, destination, amount, wanted)
            return True
        if action.kind == "spend":
            if not action.operation_id or action.amount <= 0 or action.source not in self.balances:
                return False
            existing = self.spends.get(action.operation_id)
            fields = (action.source, action.amount)
            if existing is not None:
                return existing == fields
            if self.balances[action.source] < action.amount:
                return False
            self.balances[action.source] -= action.amount
            self.spends[action.operation_id] = fields
            self.spent += action.amount
            return True
        raise ValueError(f"unknown escrow action: {action.kind}")

    def snapshot(self) -> dict[str, Any]:
        in_transit = sum(
            amount
            for _source, _destination, amount, status in self.transfers.values()
            if status == "in_transit"
        )
        return {
            "issued": self.issued,
            "remaining": sum(self.balances.values()),
            "in_transit": in_transit,
            "spent": self.spent,
            "expired": self.expired,
            "revoked": self.revoked,
            "reclaimed": self.reclaimed,
            "balances": sorted(self.balances.items()),
        }


def _apply_escrow(ledger: EscrowRiskLedger, action: EscrowAction) -> bool:
    try:
        if action.kind == "begin_transfer":
            ledger.begin_transfer(
                action.operation_id,
                action.source,
                action.destination,
                action.amount,
            )
        elif action.kind == "accept_transfer":
            ledger.accept_transfer(action.operation_id)
        elif action.kind == "reclaim_transfer":
            ledger.reclaim_transfer(action.operation_id)
        elif action.kind == "expire_transfer":
            ledger.terminate_transfer(action.operation_id)
        elif action.kind == "revoke_transfer":
            ledger.terminate_transfer(action.operation_id, revoked=True)
        elif action.kind == "spend":
            ledger.spend(action.operation_id, action.source, action.amount)
        else:
            raise ValueError(f"unknown action: {action.kind}")
        return True
    except ProtocolError:
        return False


def run_escrow_refinement(
    *, seed: int = 20260718, traces: int = 256, steps: int = 64
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rng = random.Random(seed)
    raw: list[dict[str, Any]] = []
    initial = {"a": 3, "b": 2, "c": 1}
    kinds = (
        "begin_transfer",
        "accept_transfer",
        "reclaim_transfer",
        "expire_transfer",
        "revoke_transfer",
        "spend",
    )
    sites = tuple(initial)
    accepted = rejected = 0
    for trace_id in range(traces):
        oracle = _EscrowOracle(initial)
        implementation = EscrowRiskLedger(initial)
        for sequence in range(steps):
            kind = rng.choice(kinds)
            action = EscrowAction(
                kind=kind,
                operation_id=(
                    f"t{rng.randrange(8)}" if kind != "spend" else f"sp{rng.randrange(8)}"
                ),
                source=rng.choice(sites),
                destination=rng.choice(sites),
                amount=rng.randrange(1, 4),
            )
            expected = oracle.apply(action)
            observed = _apply_escrow(implementation, action)
            actual = asdict(implementation.snapshot())
            actual["balances"] = [list(row) for row in actual["balances"]]
            wanted = oracle.snapshot()
            wanted["balances"] = [list(row) for row in wanted["balances"]]
            if expected != observed or actual != wanted:
                raise AssertionError(
                    f"escrow refinement divergence at trace={trace_id}, step={sequence}: "
                    f"{action!r}, expected={wanted!r}, actual={actual!r}"
                )
            accepted += int(observed)
            rejected += int(not observed)
            raw.append(
                {
                    "machine": "escrow_risk_rights",
                    "trace": trace_id,
                    "sequence": sequence,
                    "tla_action": {
                        "begin_transfer": "BeginTransfer",
                        "accept_transfer": "AcceptTransfer",
                        "reclaim_transfer": "ReclaimTransfer",
                        "expire_transfer": "ExpireTransfer",
                        "revoke_transfer": "RevokeTransfer",
                        "spend": "FallbackRelease",
                    }[kind],
                    "action": asdict(action),
                    "accepted": observed,
                    "state": actual,
                }
            )
    return (
        {
            "seed": seed,
            "traces": traces,
            "steps_per_trace": steps,
            "transitions": traces * steps,
            "accepted": accepted,
            "rejected": rejected,
            "all_states_refine_oracle": True,
            "all_states_conserved": True,
        },
        raw,
    )


def run_witness_refinement(
    output: Path, *, seed: int = 20260719, traces: int = 32, steps: int = 64
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rng = random.Random(seed)
    raw: list[dict[str, Any]] = []
    restarts = 0
    for trace_id in range(traces):
        decisions: dict[tuple[str, int, str], str] = {}
        db_path = output / "witness-db" / f"trace-{trace_id:03d}.sqlite3"
        for suffix in ("", "-wal", "-shm"):
            Path(f"{db_path}{suffix}").unlink(missing_ok=True)
        store = SQLiteWitnessStore(db_path, signer_id="w0")
        for sequence in range(steps):
            key = ("campaign", rng.choice((1, 2)), f"n{rng.randrange(8)}")
            digest = rng.choice(("digest-a", "digest-b", "digest-c"))
            existing = decisions.get(key)
            if existing is None:
                expected = PersistOutcome.CREATED
                decisions[key] = digest
            elif existing == digest:
                expected = PersistOutcome.IDEMPOTENT
            else:
                expected = PersistOutcome.CONFLICT
            observed = store.persist_once(key, digest)
            if observed is not expected or store.read(key) != decisions[key]:
                raise AssertionError(
                    f"witness refinement divergence at trace={trace_id}, step={sequence}"
                )
            raw.append(
                {
                    "machine": "persistent_witness",
                    "trace": trace_id,
                    "sequence": sequence,
                    "tla_action": "Persist",
                    "key": list(key),
                    "digest": digest,
                    "outcome": observed.value,
                }
            )
            if rng.random() < 0.125:
                store.close()
                store = SQLiteWitnessStore(db_path, signer_id="w0")
                restarts += 1
                if any(store.read(saved_key) != saved for saved_key, saved in decisions.items()):
                    raise AssertionError("durable witness decision lost after restart")
        store.close()

    crash_path = output / "witness-db" / "crash-before-commit.sqlite3"
    for suffix in ("", "-wal", "-shm"):
        Path(f"{crash_path}{suffix}").unlink(missing_ok=True)

    def fail_before_commit(_key, _digest) -> None:
        raise RuntimeError("injected crash before commit")

    crash_store = SQLiteWitnessStore(crash_path, signer_id="w0", before_commit=fail_before_commit)
    crash_key = ("campaign", 1, "crash-nullifier")
    try:
        crash_store.persist_once(crash_key, "digest-a")
    except RuntimeError:
        pass
    else:
        raise AssertionError("crash injection did not stop persistence")
    crash_store.close()
    reopened = SQLiteWitnessStore(crash_path, signer_id="w0")
    if reopened.read(crash_key) is not None:
        raise AssertionError("pre-commit crash leaked a durable witness decision")
    reopened.close()
    return (
        {
            "seed": seed,
            "traces": traces,
            "steps_per_trace": steps,
            "transitions": traces * steps,
            "durable_restarts": restarts,
            "crash_before_commit_fail_closed": True,
            "all_states_refine_oracle": True,
        },
        raw,
    )


@dataclass(frozen=True)
class SettlementAction:
    nullifier: str
    receipt_tag: str
    site: str
    value: int


def _settlement_header() -> CampaignHeader:
    committee = CommitteeConfig("committee", ("w0", "w1", "w2", "w3"), 3, 1)
    return CampaignHeader(
        domain=SafetyDomain("refinement", 1, 1, committee.committee_id),
        risk_cell="cell",
        committee=committee,
        authorized_sites=("site-a", "site-b"),
        fallback_categories=("food",),
        initial_risk_rights=(("site-a", 5), ("site-b", 5)),
        start_tick=0,
        end_tick=100,
        verifier_id="refinement-verifier",
        policy_hash="frozen-policy",
    )


def _receipt(header: CampaignHeader, action: SettlementAction) -> Receipt:
    voucher = Voucher(
        header.domain,
        header.risk_cell,
        "food",
        action.value,
        1,
        action.nullifier,
        f"commitment-{action.receipt_tag}",
        b"proof",
    )
    evidence = FallbackEvidence(
        domain=header.domain,
        nullifier=action.nullifier,
        fallback_window=0,
        link_tag_hash="link",
        debit_id=f"debit-{action.receipt_tag}",
        debit_amount=action.value,
        balance_before=100,
        balance_after=100 - action.value,
        site_nonce=1,
        previous_chain_hash="previous",
        chain_hash="chain",
    )
    return Receipt(
        voucher=voucher,
        site_id=action.site,
        path=RedemptionPath.FALLBACK,
        evidence=evidence,
        released_at=1,
        site_signature="signature",
    )


def run_settlement_refinement(root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Replay one transcript through oracle, Python reference and Solidity/EVM."""

    from eth_tester.exceptions import TransactionFailed
    from web3 import Web3
    from web3.providers.eth_tester import EthereumTesterProvider

    from aidmesh.experiments.evm_benchmark import compile_contract

    header = _settlement_header()
    model = ContractModel(
        ContractVerifierInterfaces(
            verify_voucher=lambda _voucher, _header: True,
            verify_signature=lambda _signer, _digest, _signature: True,
            verify_fallback_evidence=lambda _receipt, _header: True,
        )
    )
    initial_escrow = 10
    model.register_header(header, escrow=initial_escrow)

    abi, bytecode = compile_contract(root)
    web3 = Web3(EthereumTesterProvider())
    owner, adapter, site_a, site_b, unauthorized = web3.eth.accounts[:5]
    factory = web3.eth.contract(abi=abi, bytecode=bytecode)
    deployment = factory.constructor(adapter).transact({"from": owner})
    address = web3.eth.wait_for_transaction_receipt(deployment).contractAddress
    contract = web3.eth.contract(address=address, abi=abi)
    domain = Web3.keccak(text=header.domain.digest)
    header_hash = int.from_bytes(Web3.keccak(text=header.header_hash), "big")
    policy_hash = int.from_bytes(Web3.keccak(text=header.policy_hash), "big")
    committee_hash = int.from_bytes(
        Web3.keccak(text=header.committee.committee_id), "big"
    )
    contract.functions.registerCampaign(
        domain,
        header_hash,
        policy_hash,
        committee_hash,
        [site_a, site_b],
    ).transact({"from": owner})
    contract.functions.fundCampaign(domain, initial_escrow).transact({"from": owner})
    addresses = {"site-a": site_a, "site-b": site_b, "unauthorized": unauthorized}

    transcript = (
        SettlementAction("n0", "r0", "site-a", 3),
        SettlementAction("n0", "r0-replay", "site-b", 3),
        SettlementAction("n1", "r1", "site-b", 4),
        SettlementAction("n2", "r2-too-large", "site-a", 5),
        SettlementAction("n3", "r3-unauthorized", "unauthorized", 1),
        SettlementAction("n2", "r2", "site-a", 3),
    )
    winners: dict[str, str] = {}
    winner_authorizations: dict[str, bytes] = {}
    escrow = initial_escrow
    credits = {"site-a": 0, "site-b": 0}
    rows: list[dict[str, Any]] = []
    for sequence, action in enumerate(transcript):
        if action.site not in credits:
            expected = "rejected"
        elif action.nullifier in winners:
            expected = "conflict"
        elif escrow < action.value:
            expected = "rejected"
        else:
            expected = "settled"
            winners[action.nullifier] = action.receipt_tag
            winner_authorizations[action.nullifier] = Web3.keccak(
                text=f"authorization-{action.receipt_tag}"
            )
            escrow -= action.value
            credits[action.site] += action.value

        python_outcome = model.submit(_receipt(header, action)).status.value
        authorization_commitment = Web3.keccak(
            text=f"authorization-{action.receipt_tag}"
        )
        try:
            evm_return = contract.functions.settleVerified(
                domain,
                Web3.keccak(text=action.nullifier),
                Web3.keccak(text=action.receipt_tag),
                addresses[action.site],
                action.value,
                authorization_commitment,
            ).call({"from": adapter})
            transaction = contract.functions.settleVerified(
                domain,
                Web3.keccak(text=action.nullifier),
                Web3.keccak(text=action.receipt_tag),
                addresses[action.site],
                action.value,
                authorization_commitment,
            ).transact({"from": adapter})
            web3.eth.wait_for_transaction_receipt(transaction)
            evm_outcome = "settled" if evm_return else "conflict"
        except TransactionFailed:
            evm_outcome = "rejected"
        if expected != python_outcome or expected != evm_outcome:
            raise AssertionError(
                f"settlement refinement divergence at {sequence}: "
                f"expected={expected}, python={python_outcome}, evm={evm_outcome}"
            )
        rows.append(
            {
                "machine": "settlement",
                "trace": 0,
                "sequence": sequence,
                "tla_action": "Settle",
                "action": asdict(action),
                "outcome": expected,
                "authorization_commitment": authorization_commitment.hex(),
                "remaining_escrow": escrow,
                "credits": dict(credits),
            }
        )
    model.assert_at_most_once()
    evm_credits = {
        "site-a": contract.functions.credits(domain, site_a).call(),
        "site-b": contract.functions.credits(domain, site_b).call(),
    }
    evm_escrow = contract.functions.campaigns(domain).call()[3]
    evm_authorizations = {
        nullifier: contract.functions.winningAuthorization(
            domain, Web3.keccak(text=nullifier)
        ).call()
        for nullifier in winners
    }
    if (
        evm_credits != credits
        or evm_escrow != escrow
        or evm_authorizations != winner_authorizations
        or model.credits
        != {("refinement", 1, site): value for site, value in credits.items() if value}
    ):
        raise AssertionError("final settlement accounting does not refine the oracle")
    return (
        {
            "transitions": len(transcript),
            "python_reference_matches_oracle": True,
            "solidity_local_evm_matches_oracle": True,
            "final_credits": credits,
            "final_escrow": escrow,
            "solidity_profile": "P2 verifier-adapter; proof verification is outside this boundary",
        },
        rows,
    )


def write_protocol_refinement(
    output: Path,
    *,
    root: Path | None = None,
    escrow_traces: int = 256,
    escrow_steps: int = 64,
) -> dict[str, Path]:
    output.mkdir(parents=True, exist_ok=True)
    root = root or Path(__file__).resolve().parents[2]
    escrow, escrow_rows = run_escrow_refinement(traces=escrow_traces, steps=escrow_steps)
    witness, witness_rows = run_witness_refinement(output)
    settlement, settlement_rows = run_settlement_refinement(root)
    rows = escrow_rows + witness_rows + settlement_rows
    events_path = output / "refinement_events.jsonl"
    with events_path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
    summary = {
        "schema_version": 1,
        "model": "formal/AidMeshProtocol.tla",
        "action_mapping_is_explicit_in_raw_events": True,
        "escrow_risk_rights": escrow,
        "persistent_witness": witness,
        "settlement": settlement,
        "overall_refinement_pass": True,
        "limitations": [
            "Refinement is trace-based and bounded; it is not an unbounded theorem.",
            "The Solidity P2 adapter assumes an external receipt/proof verifier.",
        ],
    }
    summary_path = output / "protocol_refinement_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"events": events_path, "summary": summary_path}
