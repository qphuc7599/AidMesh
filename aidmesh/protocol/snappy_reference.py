"""Executable reconstruction of the claim and approval core of NDSS 2020 Snappy.

This is not the authors' original contract. It follows the published majority-BLS
approval, customer final-signature, and Algorithms 1--4 claim semantics closely
enough to supply auditable differential vectors. The literal equal-value
equivocation behavior is retained rather than silently repaired.
"""

import hashlib
import json
from dataclasses import dataclass

from eth_account import Account
from eth_account.messages import encode_defunct
from py_ecc.bls import G2ProofOfPossession as BLS


@dataclass(frozen=True)
class ApprovedPayment:
    customer: str
    merchant: str
    index: int
    value: int
    transaction_id: str
    signers: frozenset[int]

    def validate(self, members: int) -> None:
        if (
            self.index < 1
            or self.value <= 0
            or not self.transaction_id
            or len(self.signers) <= members // 2
            or any(type(i) is not int or not 0 <= i < members for i in self.signers)
        ):
            raise ValueError("invalid approved payment metadata")


def _canonical_payment(payment: ApprovedPayment) -> bytes:
    return json.dumps(
        {
            "customer": payment.customer.lower(),
            "merchant": payment.merchant.lower(),
            "index": payment.index,
            "value": payment.value,
            "transactionId": payment.transaction_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def payment_digest(payment: ApprovedPayment) -> bytes:
    """Domain-separated digest of the intent approved by Snappy statekeepers."""

    return hashlib.sha256(b"Snappy/payment-intent/v1\x00" + _canonical_payment(payment)).digest()


class SnappyApprovalRoster:
    """PoP-secured BLS majority roster for the published approval round."""

    def __init__(self, public_keys, possession_proofs, *, context: bytes):
        self.keys = tuple(bytes(key) for key in public_keys)
        if (
            not self.keys
            or len(self.keys) > 1024
            or len(set(self.keys)) != len(self.keys)
            or len(possession_proofs) != len(self.keys)
            or len(context) != 32
        ):
            raise ValueError("invalid Snappy statekeeper roster")
        if not all(
            BLS.PopVerify(key, proof)
            for key, proof in zip(self.keys, possession_proofs, strict=True)
        ):
            raise ValueError("invalid statekeeper proof of possession")
        self.threshold = len(self.keys) // 2 + 1
        self.context = bytes(context)
        self.roster_hash = hashlib.sha256(
            b"Snappy/statekeeper-roster/v1\x00"
            + self.context
            + len(self.keys).to_bytes(2, "big")
            + b"".join(self.keys)
        ).digest()

    def message(self, payment: ApprovedPayment) -> bytes:
        return b"Snappy/statekeeper-approval/v1\x00" + self.roster_hash + payment_digest(payment)

    def aggregate(self, payment: ApprovedPayment, shares: dict[int, bytes]) -> bytes:
        indices = sorted(shares)
        if (
            len(indices) < self.threshold
            or any(type(index) is not int or not 0 <= index < len(self.keys) for index in indices)
        ):
            raise ValueError("Snappy approval does not contain a majority")
        message = self.message(payment)
        if not all(BLS.Verify(self.keys[index], message, shares[index]) for index in indices):
            raise ValueError("invalid Snappy approval share")
        bitmap_bytes = (len(self.keys) + 7) // 8
        bitmap = sum(1 << index for index in indices).to_bytes(bitmap_bytes, "big")
        return b"SNP1" + self.roster_hash + payment_digest(payment) + bitmap + BLS.Aggregate(
            [shares[index] for index in indices]
        )

    def verify(self, payment: ApprovedPayment, wire: bytes) -> frozenset[int] | None:
        bitmap_bytes = (len(self.keys) + 7) // 8
        if (
            len(wire) != 4 + 32 + 32 + bitmap_bytes + 96
            or wire[:4] != b"SNP1"
            or wire[4:36] != self.roster_hash
            or wire[36:68] != payment_digest(payment)
        ):
            return None
        bitmap = int.from_bytes(wire[68 : 68 + bitmap_bytes], "big")
        if bitmap >> len(self.keys) or bitmap.bit_count() < self.threshold:
            return None
        signers = frozenset(index for index in range(len(self.keys)) if bitmap & (1 << index))
        if not BLS.FastAggregateVerify(
            [self.keys[index] for index in sorted(signers)], self.message(payment), wire[-96:]
        ):
            return None
        return signers


def final_transaction_digest(payment: ApprovedPayment, approval_wire: bytes) -> bytes:
    return hashlib.sha256(
        b"Snappy/final-transaction/v1\x00" + payment_digest(payment) + approval_wire
    ).digest()


def sign_final_transaction(
    payment: ApprovedPayment, approval_wire: bytes, customer_private_key: bytes
) -> bytes:
    signed = Account.sign_message(
        encode_defunct(primitive=final_transaction_digest(payment, approval_wire)),
        customer_private_key,
    )
    return bytes(signed.signature)


def verify_approved_payment(
    payment: ApprovedPayment,
    approval_wire: bytes,
    customer_signature: bytes,
    roster: SnappyApprovalRoster,
) -> ApprovedPayment:
    signers = roster.verify(payment, approval_wire)
    if signers is None:
        raise ValueError("invalid Snappy majority approval")
    recovered = Account.recover_message(
        encode_defunct(primitive=final_transaction_digest(payment, approval_wire)),
        signature=customer_signature,
    )
    if recovered.lower() != payment.customer.lower():
        raise ValueError("invalid Snappy customer signature")
    verified = ApprovedPayment(
        payment.customer,
        payment.merchant,
        payment.index,
        payment.value,
        payment.transaction_id,
        signers,
    )
    verified.validate(len(roster.keys))
    return verified


def customer_claim(payment, pending, finalized_indices, collateral, members):
    """Algorithm 3 accounting, after authentication of finalized transactions.

    Returns (payment to merchant, residual claim, remaining collateral).
    No caller-owned state is mutated on rejection.
    """
    payment.validate(members)
    if collateral < 0:
        raise ValueError("negative collateral")
    preceding = {}
    for item in pending:
        item.validate(members)
        if item.customer != payment.customer or item.index >= payment.index:
            raise ValueError("wrong pending customer/index")
        if item.index in preceding or item.index in finalized_indices:
            raise ValueError("duplicate pending/finalized index")
        preceding[item.index] = item
    if not set(range(1, payment.index)) <= (set(preceding) | set(finalized_indices)):
        raise ValueError("missing preceding payment")
    if payment.index in finalized_indices:
        return 0, payment.value, collateral
    reserved = sum(item.value for item in preceding.values())
    paid = min(payment.value, max(0, collateral - reserved))
    return paid, payment.value - paid, collateral - paid


def literal_statekeeper_claim(payment, residual, conflicts, collateral, members):
    """Literal Algorithm 4 delta accounting; collateral is merchant-specific.

    The paper pseudocode uses abs(v1-v2). Equal-value conflicts therefore debit
    zero in this reconstruction. This unresolved case blocks full validation;
    it is not silently repaired to improve the baseline's outcomes.
    """
    payment.validate(members)
    if not 0 <= residual <= payment.value or any(v < 0 for v in collateral.values()):
        raise ValueError("invalid residual/collateral")
    balances = dict(collateral)
    left = residual
    for first, second in conflicts:
        first.validate(members)
        second.validate(members)
        if (
            first.customer != payment.customer
            or second.customer != payment.customer
            or first.index != second.index
            or first.index > payment.index
            or first.transaction_id == second.transaction_id
        ):
            raise ValueError("not a preceding conflicting pair")
        delta = abs(first.value - second.value)
        # The pseudocode selects one common signer per supplied pair.
        common = sorted(first.signers & second.signers)
        if not common:
            raise ValueError("no equivocator")
        signer = common[0]
        if balances.get(signer, 0) >= delta:
            balances[signer] -= delta
            left -= delta
    paid = min(payment.value, abs(payment.value - left))
    return paid, left, balances
