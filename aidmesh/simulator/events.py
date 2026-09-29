"""Append-only canonical event recording used by the experiment harness."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema

from aidmesh.common.canonical import canonical_json

OPTIONAL_FIELDS = (
    "risk_cell",
    "group_code",
    "category",
    "value_units",
    "nullifier_hash",
    "receipt_id",
    "bundle_id",
    "parent_event_seq",
    "path",
    "committee_K",
    "threshold_q",
    "registered_f",
    "rights_before",
    "rights_after",
    "payload_bytes",
    "copies",
    "deadline_us",
    "terminal_outcome",
    "failure_class",
    "failure_code",
)


@dataclass
class EventRecorder:
    run_id: str
    experiment_id: str
    scenario_id: str
    phase: str
    method: str
    seed: int
    safety_domain: str
    config_hash: str
    software_commit: str
    records: list[dict[str, Any]] = field(default_factory=list)

    def emit(
        self,
        event_type: str,
        sim_time_us: int,
        *,
        actor_id: str,
        actor_role: str,
        **fields: Any,
    ) -> dict[str, Any]:
        unknown = set(fields) - set(OPTIONAL_FIELDS)
        if unknown:
            raise ValueError(f"unknown event fields: {sorted(unknown)}")
        record: dict[str, Any] = {
            "schema_version": "2.0.0",
            "run_id": self.run_id,
            "experiment_id": self.experiment_id,
            "scenario_id": self.scenario_id,
            "phase": self.phase,
            "method": self.method,
            "seed": self.seed,
            "sim_time_us": sim_time_us,
            "event_seq": len(self.records),
            "actor_id": actor_id,
            "actor_role": actor_role,
            "event_type": event_type,
            "safety_domain": self.safety_domain,
            "config_hash": self.config_hash,
            "software_commit": self.software_commit,
        }
        record.update({name: fields.get(name) for name in OPTIONAL_FIELDS})
        self.records.append(record)
        return record

    @property
    def checksum(self) -> str:
        digest = hashlib.sha256()
        for record in self.records:
            digest.update(canonical_json(record).encode("utf-8"))
            digest.update(b"\n")
        return digest.hexdigest()

    def validate(self, schema_path: Path) -> None:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        validator = jsonschema.Draft202012Validator(schema)
        for record in self.records:
            validator.validate(record)

    def write_jsonl(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            for record in self.records:
                handle.write(canonical_json(record))
                handle.write("\n")
