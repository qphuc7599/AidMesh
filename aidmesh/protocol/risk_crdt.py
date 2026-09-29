"""State-based distributed CRDT model for fallback risk-right escrow.

The online ledger in :mod:`aidmesh.protocol.risk` is a single atomic state
machine.  This module models the offline replication layer: sites exchange
grow-only operation sets, merge them idempotently, and audit conservation after
partitions heal.  Safety depends on one writer/lease per source site or an
equivalent threshold authorization before a source can emit transfer/spend ops.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

from aidmesh.common.canonical import stable_hash
from aidmesh.common.errors import DuplicateError, ValidationError

from .campaign import SafetyDomain

OperationKind = Literal["mint", "transfer", "spend"]


@dataclass(frozen=True)
class RiskCrdtOperation:
    op_id: str
    kind: OperationKind
    source_site: str
    destination_site: str | None
    amount: int
    nullifier: str | None
    issuer_replica: str
    domain_digest: str

    @property
    def body_hash(self) -> str:
        return stable_hash(asdict(self), domain="aidmesh/risk-crdt/op/v1")


@dataclass(frozen=True)
class RiskCrdtAudit:
    domain: SafetyDomain
    total_minted: int
    total_spent: int
    total_available: int
    conserved: bool
    negative_sites: tuple[str, ...]
    conflict_ids: tuple[str, ...]

    @property
    def safe(self) -> bool:
        return self.conserved and not self.negative_sites and not self.conflict_ids


class DistributedRiskRights:
    """Grow-only operation-set CRDT for partition-tolerant risk-right accounting."""

    def __init__(self, *, domain: SafetyDomain, replica_id: str) -> None:
        if not replica_id:
            raise ValidationError("replica_id is required")
        self.domain = domain
        self.replica_id = replica_id
        self._ops: dict[str, RiskCrdtOperation] = {}
        self._conflicts: dict[str, tuple[RiskCrdtOperation, RiskCrdtOperation]] = {}

    @classmethod
    def from_initial_rights(
        cls,
        *,
        domain: SafetyDomain,
        replica_id: str,
        initial_rights: dict[str, int],
    ) -> DistributedRiskRights:
        replica = cls(domain=domain, replica_id=replica_id)
        for site, amount in sorted(initial_rights.items()):
            if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
                raise ValidationError("initial risk rights must be non-negative integers")
            if amount == 0:
                continue
            replica._add(
                RiskCrdtOperation(
                    op_id=f"mint:{site}",
                    kind="mint",
                    source_site=site,
                    destination_site=None,
                    amount=amount,
                    nullifier=None,
                    issuer_replica="genesis",
                    domain_digest=domain.digest,
                )
            )
        return replica

    @property
    def operations(self) -> tuple[RiskCrdtOperation, ...]:
        return tuple(sorted(self._ops.values(), key=lambda item: item.op_id))

    def available(self, site_id: str) -> int:
        return self._balances().get(site_id, 0)

    def transfer(
        self,
        *,
        transfer_id: str,
        source_site: str,
        destination_site: str,
        amount: int,
    ) -> RiskCrdtOperation:
        if source_site == destination_site:
            raise ValidationError("source and destination sites must differ")
        self._require_positive(amount)
        if self.available(source_site) < amount:
            raise ValidationError("insufficient local escrow rights")
        return self._add(
            RiskCrdtOperation(
                op_id=transfer_id,
                kind="transfer",
                source_site=source_site,
                destination_site=destination_site,
                amount=amount,
                nullifier=None,
                issuer_replica=self.replica_id,
                domain_digest=self.domain.digest,
            )
        )

    def spend(
        self,
        *,
        spend_id: str,
        site_id: str,
        nullifier: str,
        amount: int,
    ) -> RiskCrdtOperation:
        if not nullifier:
            raise ValidationError("nullifier is required")
        self._require_positive(amount)
        if self.available(site_id) < amount:
            raise ValidationError("insufficient local escrow rights")
        return self._add(
            RiskCrdtOperation(
                op_id=spend_id,
                kind="spend",
                source_site=site_id,
                destination_site=None,
                amount=amount,
                nullifier=nullifier,
                issuer_replica=self.replica_id,
                domain_digest=self.domain.digest,
            )
        )

    def merge(self, other: DistributedRiskRights) -> None:
        if other.domain != self.domain:
            raise ValidationError("cannot merge different safety domains")
        for operation in other.operations:
            self._add(operation, from_merge=True)
        for op_id, pair in other._conflicts.items():
            self._conflicts.setdefault(op_id, pair)

    def export_state(self) -> dict[str, Any]:
        return {
            "domain": asdict(self.domain),
            "replica_id": self.replica_id,
            "operations": [asdict(operation) for operation in self.operations],
            "conflict_ids": sorted(self._conflicts),
        }

    @classmethod
    def import_state(cls, value: dict[str, Any]) -> DistributedRiskRights:
        domain = SafetyDomain(**value["domain"])
        replica = cls(domain=domain, replica_id=value["replica_id"])
        for raw in value["operations"]:
            replica._add(RiskCrdtOperation(**raw), from_merge=True)
        return replica

    def audit(self) -> RiskCrdtAudit:
        balances = self._balances()
        total_minted = sum(op.amount for op in self._ops.values() if op.kind == "mint")
        total_spent = sum(op.amount for op in self._ops.values() if op.kind == "spend")
        total_available = sum(balances.values())
        negative_sites = tuple(sorted(site for site, value in balances.items() if value < 0))
        return RiskCrdtAudit(
            domain=self.domain,
            total_minted=total_minted,
            total_spent=total_spent,
            total_available=total_available,
            conserved=(total_available + total_spent == total_minted),
            negative_sites=negative_sites,
            conflict_ids=tuple(sorted(self._conflicts)),
        )

    def state_digest(self) -> str:
        return stable_hash(self.export_state(), domain="aidmesh/risk-crdt/state/v1")

    def _add(
        self,
        operation: RiskCrdtOperation,
        *,
        from_merge: bool = False,
    ) -> RiskCrdtOperation:
        if operation.domain_digest != self.domain.digest:
            raise ValidationError("operation belongs to another safety domain")
        self._require_positive(operation.amount)
        existing = self._ops.get(operation.op_id)
        if existing is not None:
            if existing == operation:
                return existing
            self._conflicts[operation.op_id] = (existing, operation)
            if from_merge:
                return existing
            raise DuplicateError("operation id has conflicting body")
        self._ops[operation.op_id] = operation
        return operation

    def _balances(self) -> dict[str, int]:
        balances: dict[str, int] = {}
        for operation in self._ops.values():
            balances.setdefault(operation.source_site, 0)
            if operation.destination_site is not None:
                balances.setdefault(operation.destination_site, 0)
            if operation.kind == "mint":
                balances[operation.source_site] += operation.amount
            elif operation.kind == "transfer":
                if operation.destination_site is None:
                    raise ValidationError("transfer missing destination")
                balances[operation.source_site] -= operation.amount
                balances[operation.destination_site] += operation.amount
            elif operation.kind == "spend":
                balances[operation.source_site] -= operation.amount
            else:
                raise ValidationError(f"unknown operation kind {operation.kind!r}")
        return balances

    @staticmethod
    def _require_positive(amount: int) -> None:
        if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
            raise ValidationError("risk-right amount must be a positive integer")
