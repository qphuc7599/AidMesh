import hashlib
from dataclasses import replace

import pytest
from eth_account import Account
from py_ecc.bls import G2ProofOfPossession as BLS

from aidmesh.protocol.snappy_reference import (
    ApprovedPayment,
    SnappyApprovalRoster,
    customer_claim,
    literal_statekeeper_claim,
    payment_digest,
    sign_final_transaction,
    verify_approved_payment,
)


def payment(index=1, value=5):
    return ApprovedPayment("c", "m", index, value, str(index), frozenset({0, 1, 2}))


def test_customer_claim_reserves_pending_and_conserves_collateral():
    assert customer_claim(payment(2), [payment()], set(), 8, 4) == (3, 2, 5)
    assert customer_claim(payment(), [], {1}, 8, 4) == (0, 5, 8)
    with pytest.raises(ValueError, match="missing"):
        customer_claim(payment(2), [], set(), 8, 4)
    with pytest.raises(ValueError, match="duplicate"):
        customer_claim(payment(2), [payment(), payment()], set(), 8, 4)


def test_majority_is_snappy_majority_not_aidmesh_threshold():
    payment().validate(5)  # 3/5 is a majority.
    with pytest.raises(ValueError):
        replace(payment(), signers=frozenset({0, 1})).validate(4)


def test_literal_equal_value_equivocation_is_retained_as_validation_blocker():
    p = payment()
    conflict = replace(p, merchant="other", transaction_id="conflict", signers=frozenset({1, 2, 3}))
    assert literal_statekeeper_claim(p, 5, [(p, conflict)], {1: 10}, 4) == (0, 5, {1: 10})


def test_literal_different_value_equivocation_debits_selected_signer():
    p = payment()
    conflict = replace(p, value=3, transaction_id="conflict", signers=frozenset({1, 2, 3}))
    assert literal_statekeeper_claim(p, 5, [(p, conflict)], {1: 10}, 4) == (2, 3, {1: 8})


def _crypto_material(members=4):
    secret = [
        BLS.KeyGen(hashlib.sha256(f"snappy-{index}".encode()).digest())
        for index in range(members)
    ]
    public = [BLS.SkToPk(value) for value in secret]
    pops = [BLS.PopProve(value) for value in secret]
    roster = SnappyApprovalRoster(public, pops, context=hashlib.sha256(b"campaign").digest())
    customer = Account.create(b"snappy-customer")
    unsigned = ApprovedPayment(
        customer.address,
        "0x" + "22" * 20,
        1,
        5,
        "tx-1",
        frozenset(),
    )
    shares = {
        index: BLS.Sign(secret[index], roster.message(unsigned))
        for index in range(roster.threshold)
    }
    approval = roster.aggregate(unsigned, shares)
    signature = sign_final_transaction(unsigned, approval, bytes(customer.key))
    return roster, unsigned, approval, signature


def test_bls_majority_and_customer_signature_bind_every_payment_field():
    roster, unsigned, approval, signature = _crypto_material()
    verified = verify_approved_payment(unsigned, approval, signature, roster)
    assert verified.signers == frozenset({0, 1, 2})
    for mutation in (
        replace(unsigned, merchant="0x" + "33" * 20),
        replace(unsigned, index=2),
        replace(unsigned, value=6),
        replace(unsigned, transaction_id="tx-2"),
    ):
        with pytest.raises(ValueError, match="approval"):
            verify_approved_payment(mutation, approval, signature, roster)


def test_customer_signature_and_roster_negative_controls():
    roster, unsigned, approval, signature = _crypto_material()
    other = Account.create(b"other-customer")
    wrong_signature = sign_final_transaction(unsigned, approval, bytes(other.key))
    with pytest.raises(ValueError, match="customer"):
        verify_approved_payment(unsigned, approval, wrong_signature, roster)
    damaged = approval[:-1] + bytes([approval[-1] ^ 1])
    with pytest.raises(ValueError, match="approval"):
        verify_approved_payment(unsigned, damaged, signature, roster)
    assert payment_digest(unsigned) != payment_digest(replace(unsigned, value=6))
