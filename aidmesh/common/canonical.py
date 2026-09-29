"""Canonical serialization for audit hashes and deterministic replay.

This is an experiment/state-commitment codec.  It is not a zero-knowledge proof
system and does not replace the campaign's production wire-format review.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
from collections.abc import Mapping
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any


def _normalize(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return {
            field.name: _normalize(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
    if isinstance(value, Enum):
        return _normalize(value.value)
    if isinstance(value, bytes):
        return {"$bytes_hex": value.hex()}
    if isinstance(value, Decimal):
        return {"$decimal": format(value, "f")}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("canonical mappings require string keys")
            normalized[key] = _normalize(item)
        return {key: normalized[key] for key in sorted(normalized)}
    if isinstance(value, (tuple, list)):
        return [_normalize(item) for item in value]
    if isinstance(value, (set, frozenset)):
        items = [_normalize(item) for item in value]
        return sorted(items, key=lambda item: canonical_json(item))
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("NaN and infinity are not canonical")
        return value
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise TypeError(f"unsupported canonical value: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """Return stable UTF-8 JSON text with sorted keys and no whitespace."""

    return json.dumps(
        _normalize(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_bytes(value: Any) -> bytes:
    return canonical_json(value).encode("utf-8")


def stable_hash(value: Any, *, domain: str = "aidmesh/state/v1") -> str:
    """Hash canonical state with explicit domain separation.

    SHA-256 is used as an audit/state hash interface.  Signature and proof
    security remain supplied by explicit verifier callbacks elsewhere.
    """

    if not domain:
        raise ValueError("hash domain must be non-empty")
    hasher = hashlib.sha256()
    encoded_domain = domain.encode("utf-8")
    hasher.update(len(encoded_domain).to_bytes(4, "big"))
    hasher.update(encoded_domain)
    payload = canonical_bytes(value)
    hasher.update(len(payload).to_bytes(8, "big"))
    hasher.update(payload)
    return hasher.hexdigest()
