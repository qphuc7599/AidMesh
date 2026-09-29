"""Strict scenario loading and cross-field validation.

JSON Schema catches shape errors; these checks enforce protocol conditions that are
not conveniently expressible in the schema.  A safe label is never inferred from a
friendly name: it must satisfy the strict quorum inequality.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import jsonschema
import yaml

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "scenario.schema.json"


class ScenarioError(ValueError):
    """Raised when a scenario would make an experiment ambiguous or invalid."""


def canonical_json(value: Any) -> bytes:
    """Serialize research metadata deterministically for identifiers and manifests."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def digest_config(value: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def validate_scenario(config: dict[str, Any]) -> dict[str, Any]:
    """Validate shape and all safety-relevant cross-field constraints."""

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    try:
        jsonschema.Draft202012Validator(schema).validate(config)
    except jsonschema.ValidationError as exc:
        location = ".".join(str(part) for part in exc.absolute_path) or "<root>"
        raise ScenarioError(f"schema error at {location}: {exc.message}") from exc

    quorum = config["quorum"]
    k = quorum["K"]
    q = quorum["q"]
    f = quorum["registered_f"]
    mathematically_safe = 2 * q > k + f
    if q > k:
        raise ScenarioError("quorum.q must not exceed quorum.K")
    if f >= k:
        raise ScenarioError("registered_f must be smaller than K")
    if quorum["safe_label"] != mathematically_safe:
        relation = ">" if mathematically_safe else "<="
        raise ScenarioError(
            f"safe_label mismatch: 2q={2 * q} {relation} K+f={k + f}; "
            f"safe_label must be {mathematically_safe}"
        )

    if config["adversary"]["byzantine_witnesses"] > k:
        raise ScenarioError("byzantine_witnesses cannot exceed committee size")
    if config["network"]["partition_us"] > config["analysis_horizon_us"]:
        raise ScenarioError("partition cannot be longer than the analysis horizon")

    fallback = config["fallback"]
    if not fallback["enabled"] and fallback["total_rights"] != 0:
        raise ScenarioError("disabled fallback must have total_rights=0")
    if fallback["transfer_enabled"] and not fallback["enabled"]:
        raise ScenarioError("risk-right transfer requires fallback")

    routing = config["routing"]
    bucket = routing["padding_bucket_bytes"]
    if bucket and bucket < routing["payload_bytes"]:
        raise ScenarioError("padding bucket cannot be smaller than the payload")
    if routing["ttl_us"] > config["analysis_horizon_us"]:
        raise ScenarioError("TTL cannot exceed the pre-registered analysis horizon")

    return config


def load_scenario(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    with path.open("r", encoding="utf-8") as handle:
        value = yaml.safe_load(handle)
    if not isinstance(value, dict):
        raise ScenarioError("scenario root must be a mapping")
    return validate_scenario(value)
