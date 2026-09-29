"""Strict canonical encodings and domain-separated SHA-256 helpers.

This module intentionally does not use JSON, ``repr`` or delimiter joining for
security-critical transcripts.  Every variable-length field is length-prefixed
and every data structure fixes its field order at the call site.
"""

from __future__ import annotations

import hashlib
import unicodedata
from collections.abc import Iterable

_HASH_PREFIX = b"AIDMESH/HASH/V1\x00"
_TRANSCRIPT_PREFIX = b"AIDMESH/TRANSCRIPT/V1\x00"
MAX_FIELD_BYTES = 1 << 20


class EncodingError(ValueError):
    """Raised when a value has no valid AidMesh canonical encoding."""


def _checked_int(value: int, bits: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise EncodingError(f"{name} must be an integer, not {type(value).__name__}")
    if value < 0 or value >= 1 << bits:
        raise EncodingError(f"{name} must fit in uint{bits}")
    return value


def u8(value: int) -> bytes:
    return _checked_int(value, 8, "value").to_bytes(1, "big")


def u16(value: int) -> bytes:
    return _checked_int(value, 16, "value").to_bytes(2, "big")


def u32(value: int) -> bytes:
    return _checked_int(value, 32, "value").to_bytes(4, "big")


def u64(value: int) -> bytes:
    return _checked_int(value, 64, "value").to_bytes(8, "big")


def u128(value: int) -> bytes:
    return _checked_int(value, 128, "value").to_bytes(16, "big")


def fixed_bytes(value: bytes, length: int, name: str = "value") -> bytes:
    if not isinstance(value, bytes):
        raise EncodingError(f"{name} must be bytes")
    if len(value) != length:
        raise EncodingError(f"{name} must be exactly {length} bytes")
    return value


def opaque(value: bytes, name: str = "value") -> bytes:
    if not isinstance(value, bytes):
        raise EncodingError(f"{name} must be bytes")
    if len(value) > MAX_FIELD_BYTES:
        raise EncodingError(f"{name} exceeds {MAX_FIELD_BYTES} bytes")
    return u32(len(value)) + value


def text(value: str, name: str = "value", *, allow_empty: bool = False) -> bytes:
    if not isinstance(value, str):
        raise EncodingError(f"{name} must be str")
    normalized = unicodedata.normalize("NFC", value)
    if normalized != value:
        raise EncodingError(f"{name} must already be NFC-normalized")
    encoded = value.encode("utf-8", "strict")
    if not encoded and not allow_empty:
        raise EncodingError(f"{name} must not be empty")
    return opaque(encoded, name)


def vector(items: Iterable[bytes], name: str = "items") -> bytes:
    materialized = tuple(items)
    if len(materialized) >= 1 << 32:
        raise EncodingError(f"{name} has too many elements")
    return u32(len(materialized)) + b"".join(opaque(item, name) for item in materialized)


def transcript(domain: str, *parts: bytes) -> bytes:
    """Encode a typed transcript with an explicit semantic domain.

    The domain is encoded separately from the data fields, so moving a valid
    signature or MAC between protocol roles cannot succeed accidentally.
    """

    return _TRANSCRIPT_PREFIX + text(domain, "domain") + vector(parts, "parts")


def domain_hash(domain: str, *parts: bytes) -> bytes:
    """Return SHA-256 over a versioned, canonical, domain-separated transcript."""

    return hashlib.sha256(_HASH_PREFIX + text(domain, "domain") + vector(parts, "parts")).digest()


def sha256(data: bytes) -> bytes:
    if not isinstance(data, bytes):
        raise EncodingError("data must be bytes")
    return hashlib.sha256(data).digest()
