from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import pytest
from aidmesh.crypto.coconut_admission import AdmissionStatement
from aidmesh.protocol.value_admission_registry import ValueAdmissionRegistry


def open_registry(path):
    return ValueAdmissionRegistry(path, campaign=1, epoch=1, deployment_context=9,
        allocations=[dict(cell=2, group=3, category=4,
                          entitlements=[dict(serial=1,value=100),dict(serial=2,value=1)])],
        verify=lambda s, proof: proof == 'valid')


def holder(commitment):
    return AdmissionStatement(commitment,1,1,2,3,4,1,9)


def test_100_1_conservation_replay_restart(tmp_path):
    path=tmp_path/'value.sqlite'; r=open_registry(path)
    first=r.admit(holder(5),'valid',serials=[1])
    with pytest.raises(ValueError,match='already assigned'):
        r.admit(holder(6),'valid',serials=[1])
    second=r.admit(holder(6),'valid',serials=[2])
    r.close(); r=open_registry(path)
    assert r.admit(holder(5),'valid',serials=[1])==first
    sealed=r.seal(); rows=sealed['snapshot']['entitlements']
    assert sum(e['value'] for e in rows)==101
    assert {(e['commitment'],e['value']) for e in rows}=={(5,100),(6,1)}
    assert r.admit(holder(6),'valid',serials=[2])==second
    assert r.seal()==sealed
    r.close()


def test_race_has_only_one_assignment(tmp_path):
    path=tmp_path/'race.sqlite'; open_registry(path).close()
    def attempt(c):
        r=open_registry(path)
        try:
            r.admit(holder(c),'valid',serials=[1]); return True
        except ValueError:
            return False
        finally:
            r.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt,[5,6]))==[False,True]
    r=open_registry(path)
    assert sum(e['value'] for e in r.seal()['snapshot']['entitlements'])==100
    r.close()


def test_rejections_do_not_reserve_and_seal_detects_corruption(tmp_path):
    r=open_registry(tmp_path/'reject.sqlite')
    for s,proof,serials in [(holder(5),'bad',[1]),(holder(5),'valid',[3]),
        (replace(holder(5),epoch=2),'valid',[1]),
        (replace(holder(5),quota=2),'valid',[1,1])]:
        with pytest.raises(ValueError): r.admit(s,proof,serials=serials)
    r.admit(holder(5),'valid',serials=[1])
    r.db.execute("UPDATE value_assignments SET value='200'")
    with pytest.raises(ValueError,match='corrupt'): r.seal()
    r.close()
