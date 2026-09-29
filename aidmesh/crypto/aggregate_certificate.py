"""BLS12-381 PoP quorum certificate; separate from the EVM secp256k1 profile.

Roster registration verifies proof of possession. Membership and threshold are
trusted campaign configuration, never taken from a packet. Not threshold BLS/DKG.
"""

import hashlib

from py_ecc.bls import G2ProofOfPossession as BLS


class AggregateRoster:
    def __init__(self, public_keys, possession_proofs, *, threshold, context):
        self.keys = tuple(public_keys)
        if (
            not 1 <= threshold <= len(self.keys) <= 10
            or len(set(self.keys)) != len(self.keys)
            or len(possession_proofs) != len(self.keys)
            or len(context) != 32
        ):
            raise ValueError("invalid roster")
        if not all(BLS.PopVerify(k, p) for k, p in zip(self.keys, possession_proofs, strict=True)):
            raise ValueError("invalid possession proof")
        self.threshold = threshold
        self.context = bytes(context)
        self.roster_hash = hashlib.sha256(
            b"AidMesh/BLS-roster/v1"
            + context
            + bytes([threshold, len(self.keys)])
            + b"".join(self.keys)
        ).digest()

    def message(self, statement):
        if len(statement) != 32:
            raise ValueError("statement must be a digest")
        return b"AidMesh/BLS-certificate/v1" + self.roster_hash + statement

    def aggregate(self, statement, shares):
        if len(shares) < self.threshold or any(
            type(i) is not int or not 0 <= i < len(self.keys) for i in shares
        ):
            raise ValueError("invalid signer set")
        message = self.message(statement)
        if not all(BLS.Verify(self.keys[i], message, sig) for i, sig in shares.items()):
            raise ValueError("invalid signature share")
        bitmap = sum(1 << i for i in shares)
        return (
            b"AMC1"
            + self.roster_hash
            + statement
            + bitmap.to_bytes(2, "big")
            + BLS.Aggregate(list(shares.values()))
        )

    def verify(self, statement, wire):
        if (
            len(wire) != 166
            or wire[:4] != b"AMC1"
            or wire[4:36] != self.roster_hash
            or wire[36:68] != statement
        ):
            return False
        bitmap = int.from_bytes(wire[68:70], "big")
        if bitmap >> len(self.keys) or bitmap.bit_count() < self.threshold:
            return False
        return BLS.FastAggregateVerify(
            [k for i, k in enumerate(self.keys) if bitmap & (1 << i)],
            self.message(statement),
            wire[70:],
        )

    def verify_native(self, statement, wire):
        """Optional pinned blspy backend for cross-implementation benchmarking."""
        from blspy import G1Element, G2Element, PopSchemeMPL

        if (
            len(wire) != 166
            or wire[:4] != b"AMC1"
            or wire[4:36] != self.roster_hash
            or wire[36:68] != statement
        ):
            return False
        bitmap = int.from_bytes(wire[68:70], "big")
        if bitmap >> len(self.keys) or bitmap.bit_count() < self.threshold:
            return False
        try:
            keys = [G1Element.from_bytes(k) for i, k in enumerate(self.keys) if bitmap & (1 << i)]
            return PopSchemeMPL.fast_aggregate_verify(
                keys, self.message(statement), G2Element.from_bytes(wire[70:])
            )
        except ValueError:
            return False
