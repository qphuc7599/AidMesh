"""Verify authorization-binding fields in integrated E2E event logs."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

BYTES32 = re.compile(r"^0x[0-9a-fA-F]{64}$")
ZERO_BYTES32 = "0x" + "00" * 32


def verify_authorization_events(root: Path) -> dict[str, Any]:
    """Check that every EVM transition carries nonzero, well-formed bindings."""

    event_files = sorted((root / "runs").rglob("events.jsonl"))
    transaction_events = 0
    settled = 0
    conflicts = 0
    run_ids: set[str] = set()
    winner_keys: set[tuple[str, str]] = set()
    failures: list[dict[str, Any]] = []
    for path in event_files:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                event = json.loads(line)
                if not (
                    event.get("component") == "local-evm"
                    and event.get("action") == "verifyAndSettle"
                ):
                    continue
                transaction_events += 1
                run_id = str(event.get("run_id", ""))
                run_ids.add(run_id)
                for field in (
                    "authorization_evidence_hash",
                    "authorization_commitment",
                ):
                    value = event.get(field)
                    if not isinstance(value, str) or not BYTES32.fullmatch(value):
                        failures.append(
                            {
                                "path": str(path.relative_to(root)),
                                "line": line_number,
                                "field": field,
                                "error": "not canonical bytes32",
                            }
                        )
                    elif value.lower() == ZERO_BYTES32:
                        failures.append(
                            {
                                "path": str(path.relative_to(root)),
                                "line": line_number,
                                "field": field,
                                "error": "zero binding",
                            }
                        )
                outcome = event.get("outcome")
                if outcome == "settled":
                    settled += 1
                    key = (run_id, str(event.get("circuit_nullifier", "")))
                    if key in winner_keys:
                        failures.append(
                            {
                                "path": str(path.relative_to(root)),
                                "line": line_number,
                                "field": "circuit_nullifier",
                                "error": "multiple settled winners in one run",
                            }
                        )
                    winner_keys.add(key)
                elif outcome == "conflict":
                    conflicts += 1
                else:
                    failures.append(
                        {
                            "path": str(path.relative_to(root)),
                            "line": line_number,
                            "field": "outcome",
                            "error": f"unexpected outcome {outcome!r}",
                        }
                    )
    return {
        "schema_version": 1,
        "status": "valid" if not failures else "invalid",
        "event_files": len(event_files),
        "runs_with_transactions": len(run_ids),
        "transaction_events": transaction_events,
        "settled": settled,
        "conflicts": conflicts,
        "distinct_winner_keys": len(winner_keys),
        "failures": failures,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = verify_authorization_events(args.input)
    rendered = json.dumps(result, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    if result["status"] != "valid":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
