"""Normalize all upstream Asturies-Wildfires incidents without inspecting outcomes."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from aidmesh.experiments.trace_preprocess import (
    AsturiesWildfireConversionConfig,
    write_asturies_wildfire_trace,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--upstream-commit", required=True)
    parser.add_argument("--range-m", type=int, default=200)
    args = parser.parse_args()
    scenarios = args.source / "one" / "scenarios"
    sources = sorted(
        scenarios.glob("WF*_one.csv"),
        key=lambda path: int(re.search(r"WF(\d+)", path.name).group(1)),
    )
    if len(sources) != 30:
        raise SystemExit(f"expected 30 upstream incidents, found {len(sources)}")
    records = []
    for source in sources:
        incident = int(re.search(r"WF(\d+)", source.name).group(1))
        context = f"asturies_wf{incident:02d}_r{args.range_m}"
        target = args.output / context
        paths = write_asturies_wildfire_trace(
            source,
            config=AsturiesWildfireConversionConfig(
                context=context,
                communication_range_m=args.range_m,
            ),
            output=target,
            upstream_commit=args.upstream_commit,
        )
        provenance = json.loads(paths["provenance"].read_text(encoding="utf-8"))
        eligible = (
            provenance["nodes_with_contacts"] >= 8
            and provenance["duration_us"] >= 2 * 3_600_000_000
            and provenance["calibration_contacts"] >= 100
            and provenance["heldout_contacts"] >= 100
        )
        records.append(
            {
                "context": context,
                "source_file": source.name,
                "eligible": eligible,
                "eligibility_rule": (
                    ">=8 nodes with contacts, >=2h duration, and >=100 contacts in "
                    "each 40/60 temporal partition"
                ),
                **{
                    key: provenance[key]
                    for key in (
                        "normalized_contacts",
                        "raw_contact_samples",
                        "nodes_with_contacts",
                        "duration_us",
                        "calibration_contacts",
                        "heldout_contacts",
                        "contacts_sha256",
                    )
                },
            }
        )
    manifest = {
        "schema_version": 1,
        "family": "asturies_wildfires",
        "source_repository": "https://github.com/sergiocabrero/asturies-wildfires",
        "upstream_commit": args.upstream_commit,
        "communication_range_m": args.range_m,
        "outcome_blind_eligibility": True,
        "incidents": records,
        "eligible_contexts": [row["context"] for row in records if row["eligible"]],
    }
    args.output.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output / "family_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    checksum_path = args.output / "CHECKSUMS.sha256"
    files = sorted(
        path
        for path in args.output.rglob("*")
        if path.is_file() and path != checksum_path
    )
    checksum_path.write_text(
        "".join(
            f"{_sha256(path)}  {path.relative_to(args.output).as_posix()}\n"
            for path in files
        ),
        encoding="ascii",
    )
    print(json.dumps({
        "incidents": len(records),
        "eligible": len(manifest["eligible_contexts"]),
        "manifest": str(manifest_path),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
