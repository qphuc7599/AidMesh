"""Coconut-to-credential-registry admission boundary (Linux optional runtime).

Issuance hides a P1 secret commitment from issuers. Admission selectively reveals
that commitment and policy to a registry builder while retaining one private holder
attribute. Voucher presentation subsequently hides the registry leaf with P1.
This adapter does not prove that the builder published the approved Merkle root.
"""

from dataclasses import dataclass

P1_FIELD = 21888242871839275222246405745257275088548364400416034343698204186575808495617


@dataclass(frozen=True)
class AdmissionStatement:
    commitment: int
    campaign: int
    epoch: int
    cell: int
    group: int
    category: int
    quota: int
    deployment_context: int

    def __post_init__(self):
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if type(value) is not int or not 0 <= value < P1_FIELD:
                raise ValueError(f"{name} must be a canonical P1 field element")
        if self.quota == 0:
            raise ValueError("quota must be positive")

    @staticmethod
    def limbs(value):
        return [value >> 128, value & (2**128 - 1)]

    def policy_attributes(self):
        return [
            limb
            for value in (
                self.campaign,
                self.epoch,
                self.cell,
                self.group,
                self.category,
                self.quota,
                self.deployment_context,
            )
            for limb in self.limbs(value)
        ]

    def disclosed_attributes(self):
        return [*self.limbs(self.commitment), *self.policy_attributes()]


class CoconutAdmissionProfile:
    """Research adapter with trusted threshold setup, not distributed key generation."""

    def __init__(self, threshold=3, issuers=5):
        if not 1 <= threshold <= issuers:
            raise ValueError("invalid issuer threshold")
        from coconut import scheme
        from coconut.utils import elgamal_keygen
        from petlib.bn import Bn

        self.scheme, self.elgamal_keygen, self.Bn = scheme, elgamal_keygen, Bn
        self.params = scheme.setup(17)
        self.secret_keys, keys = scheme.ttp_keygen(self.params, threshold, issuers)
        self.verification_key = scheme.agg_key(self.params, keys)
        self.threshold, self.issuers = threshold, issuers

    def scalars(self, values):
        # Explicit conversion avoids petlib's machine-word constructor limit.
        return [self.Bn.from_decimal(str(value)) for value in values]

    def issue(self, statement, holder_secret, selected):
        selected = tuple(selected)
        if len(set(selected)) != len(selected) or len(selected) < self.threshold:
            raise ValueError("need distinct threshold issuers")
        if any(type(i) is not int or not 0 <= i < self.issuers for i in selected):
            raise ValueError("unknown issuer")
        hidden = [holder_secret, *self.scalars(statement.limbs(statement.commitment))]
        public = self.scalars(statement.policy_attributes())
        d, gamma = self.elgamal_keygen(self.params)
        request = self.scheme.prepare_blind_sign(self.params, gamma, hidden, public_m=public)
        shares = [None] * self.issuers
        for i in selected:
            blinded = self.scheme.blind_sign(
                self.params, self.secret_keys[i], gamma, request, public_m=public
            )
            shares[i] = self.scheme.unblind(self.params, blinded, d)
        return self.scheme.agg_cred(self.params, shares)

    def show_for_admission(self, credential, holder_secret):
        return self.scheme.prove_cred(
            self.params, self.verification_key, credential, [holder_secret]
        )

    def verify_admission(self, statement, showing):
        try:
            return bool(
                self.scheme.verify_cred(
                    self.params,
                    self.verification_key,
                    showing,
                    public_m=self.scalars(statement.disclosed_attributes()),
                )
            )
        except (AssertionError, ValueError, TypeError):
            return False
