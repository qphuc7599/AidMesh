from dataclasses import replace

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from aidmesh.protocol.root_endorsement import (
    RootEndorser,
    RootStatement,
    committee_digest,
    verify_root_endorsement,
)
from aidmesh.protocol.witness import SQLiteWitnessStore


def test_threshold_root_mutation_and_restart(tmp_path):
    members = ("a", "b", "c", "d")
    keys = {m: Ed25519PrivateKey.generate() for m in members}

    def sign(m, d):
        return keys[m].sign(bytes.fromhex(d)).hex()

    def verify(m, d, s):
        try:
            keys[m].public_key().verify(bytes.fromhex(s), bytes.fromhex(d))
            return True
        except (ValueError, InvalidSignature):
            return False

    statement = RootStatement(
        1, 1, 2, 3, 4, "a" * 64, "b" * 64, "c" * 64, committee_digest(members, 3, 1)
    )
    stores = [SQLiteWitnessStore(tmp_path / f"{m}.db", signer_id=m) for m in members]
    shares = [
        RootEndorser(m, statement, store, sign).endorse(statement)
        for m, store in zip(members, stores, strict=True)
    ]
    kwargs = dict(members=members, threshold=3, faults=1, verify=verify)
    assert verify_root_endorsement(statement, shares[:3], **kwargs)
    for field in statement.__dataclass_fields__:
        old = getattr(statement, field)
        new = old + 1 if isinstance(old, int) else ("d" * 64 if old != "d" * 64 else "e" * 64)
        with pytest.raises(ValueError):
            verify_root_endorsement(replace(statement, **{field: new}), shares, **kwargs)
    with pytest.raises(ValueError):
        verify_root_endorsement(statement, [shares[0]] * 3, **kwargs)
    with pytest.raises(ValueError):
        verify_root_endorsement(statement, shares[:2], **kwargs)
    for store in stores:
        store.close()
    store = SQLiteWitnessStore(tmp_path / "a.db", signer_id="a")
    changed = replace(statement, credential_root=5)
    with pytest.raises(ValueError, match="already endorsed"):
        RootEndorser("a", changed, store, sign).endorse(changed)
    store.close()
