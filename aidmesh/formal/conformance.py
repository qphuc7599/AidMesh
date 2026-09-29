"""Replay one risk-right transcript against an independent simple state oracle.

The oracle intentionally does not reuse the ledger implementation's locks,
tokens or hash-chain code.  It checks only the semantic state transition; any
divergence is evidence that the implementation no longer conforms to the
registered conservation model.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from aidmesh.common.errors import ValidationError
from aidmesh.protocol.campaign import SafetyDomain
from aidmesh.protocol.risk import RiskRightsLedger

OperationKind = Literal["transfer", "debit"]


@dataclass(frozen=True)
class RiskOperation:
    kind: OperationKind
    operation_id: str
    source_site: str
    amount: int
    destination_site: str | None = None
    nullifier: str | None = None


@dataclass
class _RiskOracle:
    balances: dict[str, int]
    debited: int = 0

    def apply(self, operation: RiskOperation) -> bool:
        if operation.amount <= 0 or operation.source_site not in self.balances:
            return False
        if operation.kind == "transfer":
            if (
                operation.destination_site is None
                or operation.destination_site == operation.source_site
                or operation.destination_site not in self.balances
                or self.balances[operation.source_site] < operation.amount
            ):
                return False
            self.balances[operation.source_site] -= operation.amount
            self.balances[operation.destination_site] += operation.amount
            return True
        if operation.kind == "debit":
            if not operation.nullifier or self.balances[operation.source_site] < operation.amount:
                return False
            self.balances[operation.source_site] -= operation.amount
            self.debited += operation.amount
            return True
        raise ValueError(f"unknown operation kind: {operation.kind}")


def default_risk_transcript() -> tuple[RiskOperation, ...]:
    return (
        RiskOperation("transfer", "t-1", "site-a", 1, destination_site="site-b"),
        RiskOperation("debit", "d-1", "site-b", 2, nullifier="n-1"),
        RiskOperation("debit", "d-invalid", "site-a", 9, nullifier="n-2"),
        RiskOperation("transfer", "t-2", "site-b", 1, destination_site="site-a"),
        RiskOperation("debit", "d-2", "site-a", 1, nullifier="n-3"),
    )


def replay_risk_conformance(
    initial_rights: dict[str, int],
    operations: tuple[RiskOperation, ...],
) -> dict:
    """Run a fixed transcript through the formal oracle and production ledger."""

    domain = SafetyDomain("formal-conformance", 1, 1, "committee")
    ledger = RiskRightsLedger(domain, initial_rights)
    oracle = _RiskOracle(dict(initial_rights))
    rows: list[dict] = []
    for sequence, operation in enumerate(operations):
        expected_accept = oracle.apply(operation)
        try:
            if operation.kind == "transfer":
                assert operation.destination_site is not None
                ledger.transfer(
                    transfer_id=operation.operation_id,
                    source_site=operation.source_site,
                    destination_site=operation.destination_site,
                    amount=operation.amount,
                    expected_source=ledger.state_token(operation.source_site),
                    expected_destination=ledger.state_token(operation.destination_site),
                )
            else:
                assert operation.nullifier is not None
                ledger.debit(
                    site_id=operation.source_site,
                    debit_id=operation.operation_id,
                    nullifier=operation.nullifier,
                    amount=operation.amount,
                    expected_state=ledger.state_token(operation.source_site),
                )
            implementation_accept = True
        except ValidationError:
            implementation_accept = False
        snapshot = ledger.snapshot()
        balances = dict(snapshot.balances)
        debited = snapshot.debited_total
        if implementation_accept != expected_accept:
            raise AssertionError(f"accept/reject divergence at operation {operation.operation_id}")
        if balances != oracle.balances or debited != oracle.debited:
            raise AssertionError(f"state divergence at operation {operation.operation_id}")
        rows.append(
            {
                "sequence": sequence,
                "operation": asdict(operation),
                "accepted": implementation_accept,
                "balances": balances,
                "debited": debited,
                "journal_hash": snapshot.journal_hash,
            }
        )
    return {
        "initial_rights": initial_rights,
        "operations": rows,
        "risk_rights_conserved": ledger.snapshot().is_conserved,
        "formal_oracle_matches_python_ledger": True,
    }


def write_risk_conformance(output: Path) -> Path:
    output.mkdir(parents=True, exist_ok=True)
    report = replay_risk_conformance({"site-a": 3, "site-b": 2}, default_risk_transcript())
    path = output / "risk_ledger_conformance.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
