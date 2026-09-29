import hashlib

import pytest
from py_ecc.bls import G2ProofOfPossession as BLS

from aidmesh.crypto.aggregate_certificate import AggregateRoster


@pytest.fixture(scope="module")
def material():
    keys = [BLS.KeyGen(bytes([i]) * 32) for i in range(1, 5)]
    public = [BLS.SkToPk(k) for k in keys]
    pops = [BLS.PopProve(k) for k in keys]
    roster = AggregateRoster(public, pops, threshold=3, context=bytes(32))
    statement = hashlib.sha256(b"unit-test").digest()
    shares = {i: BLS.Sign(keys[i], roster.message(statement)) for i in range(3)}
    return public, pops, roster, statement, shares


def test_possession_and_unique_roster_are_required(material):
    public, pops, *_ = material
    with pytest.raises(ValueError, match="possession"):
        AggregateRoster(public, pops[1:] + pops[:1], threshold=3, context=bytes(32))
    with pytest.raises(ValueError, match="roster"):
        AggregateRoster([public[0]] * 4, pops, threshold=3, context=bytes(32))


def test_quorum_context_and_wire_binding(material):
    _, _, roster, statement, shares = material
    with pytest.raises(ValueError, match="signer set"):
        roster.aggregate(statement, {i: shares[i] for i in (0, 1)})
    wire = roster.aggregate(statement, shares)
    assert len(wire) == 166 and roster.verify(statement, wire)
    assert not roster.verify(bytes(32), wire)
    for altered in (wire[:-1], wire + b"x", wire[:68] + b"\xff\xff" + wire[70:]):
        assert not roster.verify(statement, altered)
    pytest.importorskip("blspy")
    assert roster.verify_native(statement, wire)
