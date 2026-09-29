"""Explicit cryptographic *interfaces* used by the executable model.

The model deliberately does not claim to implement anonymous credentials or
zero-knowledge proofs.  Experiments inject audited implementations (or clearly
labelled test doubles) through these callbacks.
"""

from __future__ import annotations

from typing import Protocol

type Digest = str
type Signature = str
type Proof = bytes | str


class MessageSigner(Protocol):
    def __call__(self, signer_id: str, message_digest: Digest, /) -> Signature:
        """Return a signature supplied by an external signature interface."""


class SignatureVerifier(Protocol):
    def __call__(self, signer_id: str, message_digest: Digest, signature: Signature, /) -> bool:
        """Verify through an external signature interface."""


class VoucherProofVerifier(Protocol):
    def __call__(self, voucher: object, header: object, /) -> bool:
        """Verify the exact campaign voucher relation externally."""


class FallbackProofVerifier(Protocol):
    def __call__(
        self, voucher: object, fallback_window: int, link_tag: str, proof: Proof, /
    ) -> bool:
        """Verify the exact fallback-tag relation externally."""
