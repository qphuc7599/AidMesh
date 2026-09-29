from __future__ import annotations

import pytest

from aidmesh.common.errors import DuplicateError, ValidationError
from aidmesh.protocol.campaign import SafetyDomain
from aidmesh.protocol.risk_crdt import DistributedRiskRights, RiskCrdtOperation


def _domain() -> SafetyDomain:
    return SafetyDomain("campaign", 1, 1, "committee")


def test_risk_crdt_merge_is_idempotent_and_conserved() -> None:
    left = DistributedRiskRights.from_initial_rights(
        domain=_domain(),
        replica_id="replica-a",
        initial_rights={"s1": 5, "s2": 2},
    )
    right = DistributedRiskRights.from_initial_rights(
        domain=_domain(),
        replica_id="replica-b",
        initial_rights={"s1": 5, "s2": 2},
    )
    left.transfer(transfer_id="t1", source_site="s1", destination_site="s2", amount=2)
    right.merge(left)
    right.merge(left)
    right.spend(spend_id="d1", site_id="s2", nullifier="n1", amount=3)
    left.merge(right)

    assert left.audit().safe
    assert right.audit().safe
    assert left.available("s1") == 3
    assert left.available("s2") == 1


def test_risk_crdt_detects_partition_overspend_after_merge() -> None:
    left = DistributedRiskRights.from_initial_rights(
        domain=_domain(),
        replica_id="replica-a",
        initial_rights={"s1": 4, "s2": 0, "s3": 0},
    )
    right = DistributedRiskRights.from_initial_rights(
        domain=_domain(),
        replica_id="replica-b",
        initial_rights={"s1": 4, "s2": 0, "s3": 0},
    )
    left.transfer(transfer_id="t1", source_site="s1", destination_site="s2", amount=3)
    right.transfer(transfer_id="t2", source_site="s1", destination_site="s3", amount=3)
    left.merge(right)

    audit = left.audit()
    assert audit.conserved
    assert audit.negative_sites == ("s1",)
    assert not audit.safe


def test_risk_crdt_rejects_conflicting_operation_id_locally() -> None:
    replica = DistributedRiskRights.from_initial_rights(
        domain=_domain(),
        replica_id="replica-a",
        initial_rights={"s1": 5, "s2": 0},
    )
    replica.transfer(transfer_id="t1", source_site="s1", destination_site="s2", amount=1)
    with pytest.raises(DuplicateError):
        replica.transfer(transfer_id="t1", source_site="s1", destination_site="s2", amount=2)


def test_risk_crdt_rejects_wrong_domain_operation() -> None:
    replica = DistributedRiskRights.from_initial_rights(
        domain=_domain(),
        replica_id="replica-a",
        initial_rights={"s1": 5},
    )
    foreign = RiskCrdtOperation(
        op_id="foreign",
        kind="spend",
        source_site="s1",
        destination_site=None,
        amount=1,
        nullifier="n",
        issuer_replica="replica-x",
        domain_digest="not-this-domain",
    )
    other = DistributedRiskRights(domain=_domain(), replica_id="replica-b")
    other._ops[foreign.op_id] = foreign
    with pytest.raises(ValidationError):
        replica.merge(other)
