from dataclasses import replace

import pytest

from aidmesh.crypto.coconut_admission import AdmissionStatement
from aidmesh.protocol.admission_registry import AdmissionRegistry


def open_registry(path, **overrides):
    config = dict(
        campaign=1,
        epoch=1,
        deployment_context=9,
        allocations=[dict(cell=2, group=3, category=4, quota=2, value=10)],
        verify=lambda statement, showing: showing == "valid",
    )
    config.update(overrides)
    return AdmissionRegistry(path, **config)


def statement():
    return AdmissionStatement(5, 1, 1, 2, 3, 4, 2, 9)


def test_atomic_issue_replay_restart_seal(tmp_path):
    path = tmp_path / "registry.sqlite"
    registry = open_registry(path)
    admission = registry.admit(statement(), "valid")
    registry.close()
    registry = open_registry(path)
    assert registry.admit(statement(), "valid") == admission
    snapshot = registry.seal()
    leaves = snapshot["snapshot"]["entitlements"]
    assert [leaf["index"] for leaf in leaves] == [1, 2]
    assert all(leaf["value"] == 10 and leaf["admission"] == admission for leaf in leaves)
    assert registry.seal() == snapshot
    registry.close()
    with pytest.raises(ValueError, match="replace registry policy"):
        open_registry(path, epoch=2)


def test_reject_uncredentialed_mutated_overissued_and_stale(tmp_path):
    registry = open_registry(tmp_path / "registry.sqlite")
    for altered, showing in [
        (statement(), "invalid"),
        (replace(statement(), quota=3), "valid"),
        (replace(statement(), epoch=2), "valid"),
        (replace(statement(), group=7), "valid"),
    ]:
        with pytest.raises(ValueError):
            registry.admit(altered, showing)
    with pytest.raises(ValueError, match="empty registry"):
        registry.seal()
    registry.admit(statement(), "valid")
    with pytest.raises(ValueError):
        registry.admit(replace(statement(), quota=1), "valid")
    with pytest.raises(ValueError):
        registry.admit(replace(statement(), commitment=6), "valid")
    assert len(registry.seal()["snapshot"]["admissions"]) == 1
    registry.close()


def test_multiple_holders_share_value_schedule_not_quota(tmp_path):
    path = tmp_path / "multi.sqlite"
    config = dict(
        multi_beneficiary=True, allocations=[dict(cell=2, group=3, category=4, quota=5, value=10)]
    )
    registry = open_registry(path, **config)
    first = registry.admit(statement(), "valid")
    second_statement = replace(statement(), commitment=6, quota=3)
    second = registry.admit(second_statement, "valid")
    with pytest.raises(ValueError, match="exceeds allocated"):
        registry.admit(replace(statement(), commitment=7, quota=1), "valid")
    registry.close()
    registry = open_registry(path, **config)
    assert registry.admit(second_statement, "valid") == second
    snapshot = registry.seal()["snapshot"]
    assert snapshot["profile"].endswith("/v2")
    leaves = snapshot["entitlements"]
    assert [e["index"] for e in leaves if e["admission"] == first] == [1, 2]
    assert [e["index"] for e in leaves if e["admission"] == second] == [1, 2, 3]
    assert sum(e["value"] for e in leaves) == 50
    registry.close()
    with pytest.raises(ValueError):
        open_registry(path)  # no reinterpretation using old index semantics
