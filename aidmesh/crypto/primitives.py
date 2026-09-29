"""Concrete cryptographic primitives used by the reference implementation.

Ed25519 is provided by ``cryptography``. HMAC-SHA-256 is used as the PRF.  The
reference profile is deliberately not an anonymous credential or a ZK system;
see :mod:`aidmesh.crypto.reference` and ``docs/crypto_profile.md``.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass, field

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .encoding import domain_hash, fixed_bytes, text, transcript, u64

KEY_BYTES = 32
PUBLIC_KEY_BYTES = 32
SIGNATURE_BYTES = 64


def random_bytes(length: int) -> bytes:
    if isinstance(length, bool) or not isinstance(length, int) or length <= 0:
        raise ValueError("length must be a positive integer")
    return os.urandom(length)


def _derive_key(master_seed: bytes, purpose: str) -> bytes:
    """HKDF-style extract-and-expand with one SHA-256 output block."""

    fixed_bytes(master_seed, KEY_BYTES, "master_seed")
    salt = b"AIDMESH/KEY-DERIVATION/V1\x00"
    prk = hmac.new(salt, master_seed, hashlib.sha256).digest()
    info = transcript("key-derivation", text(purpose, "purpose"))
    return hmac.new(prk, info + b"\x01", hashlib.sha256).digest()


@dataclass(frozen=True)
class Ed25519Keypair:
    """An Ed25519 private seed with domain-separated signing helpers."""

    _seed: bytes = field(repr=False)

    def __post_init__(self) -> None:
        fixed_bytes(self._seed, KEY_BYTES, "seed")

    @classmethod
    def generate(cls) -> Ed25519Keypair:
        return cls(random_bytes(KEY_BYTES))

    @classmethod
    def from_seed(cls, seed: bytes) -> Ed25519Keypair:
        return cls(seed)

    @property
    def public_key(self) -> bytes:
        key = Ed25519PrivateKey.from_private_bytes(self._seed).public_key()
        return key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)

    def sign(self, domain: str, *parts: bytes) -> bytes:
        return Ed25519PrivateKey.from_private_bytes(self._seed).sign(transcript(domain, *parts))

    def sign_digest(self, digest: bytes) -> bytes:
        """Sign an already domain-separated 32-byte digest as the Ed25519 message."""

        fixed_bytes(digest, 32, "digest")
        return Ed25519PrivateKey.from_private_bytes(self._seed).sign(digest)


def verify_ed25519(public_key: bytes, signature: bytes, domain: str, *parts: bytes) -> bool:
    try:
        fixed_bytes(public_key, PUBLIC_KEY_BYTES, "public_key")
        fixed_bytes(signature, SIGNATURE_BYTES, "signature")
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, transcript(domain, *parts))
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


def verify_ed25519_digest(public_key: bytes, signature: bytes, digest: bytes) -> bool:
    try:
        fixed_bytes(public_key, PUBLIC_KEY_BYTES, "public_key")
        fixed_bytes(signature, SIGNATURE_BYTES, "signature")
        fixed_bytes(digest, 32, "digest")
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, digest)
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


def prf(key: bytes, domain: str, *parts: bytes) -> bytes:
    fixed_bytes(key, KEY_BYTES, "PRF key")
    return hmac.new(key, transcript(domain, *parts), hashlib.sha256).digest()


def derive_nullifier(
    key: bytes,
    *,
    epoch: int,
    cell: str,
    category: str,
    index: int,
    value: int,
) -> bytes:
    if epoch <= 0 or index <= 0 or value <= 0:
        raise ValueError("epoch, index and value must be positive")
    return prf(
        key,
        "voucher-nullifier-v1",
        u64(epoch),
        text(cell, "cell"),
        text(category, "category"),
        u64(index),
        u64(value),
    )


def derive_fallback_tag(
    key: bytes,
    *,
    epoch: int,
    window: int,
    cell: str,
    category: str,
) -> bytes:
    if epoch <= 0 or window <= 0:
        raise ValueError("epoch and window must be positive")
    return prf(
        key,
        "fallback-link-tag-v1",
        u64(epoch),
        u64(window),
        text(cell, "cell"),
        text(category, "category"),
    )


def key_commitment(key: bytes) -> bytes:
    fixed_bytes(key, KEY_BYTES, "key")
    return domain_hash("reference-prf-key-commitment-v1", key)


@dataclass(frozen=True)
class BeneficiarySecrets:
    """Separated possession-signing and PRF keys derived from one local seed."""

    _master_seed: bytes = field(repr=False)

    def __post_init__(self) -> None:
        fixed_bytes(self._master_seed, KEY_BYTES, "master_seed")

    @classmethod
    def generate(cls) -> BeneficiarySecrets:
        return cls(random_bytes(KEY_BYTES))

    @classmethod
    def from_seed(cls, seed: bytes) -> BeneficiarySecrets:
        return cls(seed)

    @property
    def possession_keypair(self) -> Ed25519Keypair:
        return Ed25519Keypair.from_seed(
            _derive_key(self._master_seed, "beneficiary-possession-ed25519")
        )

    @property
    def possession_public_key(self) -> bytes:
        return self.possession_keypair.public_key

    @property
    def prf_key(self) -> bytes:
        return _derive_key(self._master_seed, "beneficiary-nullifier-prf")

    @property
    def prf_key_commitment(self) -> bytes:
        return key_commitment(self.prf_key)
