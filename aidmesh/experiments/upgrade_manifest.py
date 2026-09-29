"""Validate and hash the protocol-faithful 2026-07-18 upgrade evidence."""

from __future__ import annotations

import csv
import hashlib
import json
import re
import xml.etree.ElementTree as et
from pathlib import Path
from typing import Any


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_log(path: Path) -> str:
    raw = path.read_bytes()
    return raw.decode("utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8")


def _tlc_counts(path: Path) -> dict[str, int | bool]:
    text = _read_log(path)
    matches = re.findall(
        r"([\d,]+) states generated, ([\d,]+) distinct states found", text
    )
    depths = re.findall(r"depth of the complete state graph search is ([\d,]+)", text)
    if not matches or not depths:
        raise ValueError(f"TLC counts missing: {path}")
    generated, distinct = matches[-1]
    return {
        "generated": int(generated.replace(",", "")),
        "distinct": int(distinct.replace(",", "")),
        "depth": int(depths[-1].replace(",", "")),
        "no_error": "Model checking completed. No error has been found" in text,
    }


def _hash_entry(root: Path, path: Path) -> dict[str, str | int]:
    return {
        "path": str(path.relative_to(root)).replace("\\", "/"),
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def write_upgrade_evidence_manifest(root: Path, output: Path) -> Path:
    results = root / "results" / "upgrade_20260718"
    primary_tlc = results / "formal" / "tlc-final" / "safe-quorum" / "tlc.log"
    second_tlc = results / "formal" / "tlc-safe-quorum-fp1b.log"
    primary_counts = _tlc_counts(primary_tlc)
    second_counts = _tlc_counts(second_tlc)
    if primary_counts != second_counts:
        raise AssertionError("independent TLC fingerprint runs produced different state graphs")

    tlc_summary_path = results / "formal" / "tlc-final" / "tlc_summary.json"
    tlc_summary = json.loads(tlc_summary_path.read_text(encoding="utf-8-sig"))
    if len(tlc_summary) != 7 or not all(case["passed"] for case in tlc_summary):
        raise AssertionError("TLC safe/negative-control matrix is incomplete")

    refinement_path = results / "formal" / "refinement" / "protocol_refinement_summary.json"
    refinement = json.loads(refinement_path.read_text(encoding="utf-8"))
    if not refinement["overall_refinement_pass"]:
        raise AssertionError("protocol-to-code refinement did not pass")

    heldout_path = (
        results / "heldout_temporal_frontier" / "heldout_temporal_frontier_rows.csv"
    )
    with heldout_path.open("r", encoding="utf-8", newline="") as handle:
        heldout = list(csv.DictReader(handle))
    conserved = [row for row in heldout if row["method"] != "first_seen_unsafe"]
    heldout_checks = {
        "rows": len(heldout),
        "conserved_cap_violations": sum(int(row["cap_violation"]) for row in conserved),
        "fixed_home_duplicate_exposure": sum(
            int(row["duplicate_exposure"])
            for row in heldout
            if row["method"] == "fixed_home"
        ),
        "contexts": sorted({row["context"] for row in heldout}),
        "seeds": len({(row["context"], row["seed"]) for row in heldout}),
    }
    if heldout_checks["conserved_cap_violations"] != 0:
        raise AssertionError("held-out conserved method violated its cap")
    if heldout_checks["fixed_home_duplicate_exposure"] != 0:
        raise AssertionError("fixed-home negative control exposed duplicate value")

    one_path = results / "the_one_semantic_resolution" / "the_one_discrepancy_sweep.csv"
    with one_path.open("r", encoding="utf-8", newline="") as handle:
        one_rows = list(csv.DictReader(handle))
    if len(one_rows) != 1:
        raise AssertionError("expected one fine-grained semantic-resolution row")
    one = one_rows[0]
    one_checks = {
        "bundles": int(one["bundles"]),
        "the_one_delivered": int(one["delivered"]),
        "slot_causal_oracle_delivered": int(one["slot_causal_oracle_delivered"]),
        "intraslot_closure_oracle_delivered": int(
            one["intraslot_closure_oracle_delivered"]
        ),
        "matches_intraslot_closure_delivery_set": (
            one["matches_intraslot_closure_delivery_set"] == "True"
        ),
        "classified": sum(
            int(one[field])
            for field in ("both_delivered", "aidmesh_only", "the_one_only", "neither")
        ),
    }
    if not one_checks["matches_intraslot_closure_delivery_set"]:
        raise AssertionError("fine-grained The ONE does not match the closure oracle")
    if one_checks["classified"] != one_checks["bundles"]:
        raise AssertionError("The ONE comparison did not classify every message")

    junit_path = results / "tests" / "pytest.xml"
    suite = et.parse(junit_path).getroot().find("testsuite")
    if suite is None:
        raise ValueError("pytest JUnit suite missing")
    test_checks = {
        key: int(suite.attrib.get(key, "0"))
        for key in ("tests", "failures", "errors", "skipped")
    }
    if test_checks["failures"] or test_checks["errors"]:
        raise AssertionError("test suite has failures")

    evidence_files = [
        root / "formal" / "AidMeshProtocol.tla",
        root / "formal" / "AidMeshProtocolSafe.cfg",
        root / "formal" / "AidMeshProtocolSafeRights.cfg",
        root / "formal" / "AidMeshProtocolSafeSettlement.cfg",
        root / "formal" / "AidMeshProtocolUnsafeQuorum.cfg",
        root / "formal" / "AidMeshProtocolUnsafeRollback.cfg",
        root / "formal" / "AidMeshProtocolUnsafeConservation.cfg",
        root / "formal" / "AidMeshProtocolUnsafeSettlement.cfg",
        root / "aidmesh" / "formal" / "refinement.py",
        root / "aidmesh" / "experiments" / "temporal_frontier.py",
        root / "aidmesh" / "experiments" / "the_one_crosscheck.py",
        root / "aidmesh" / "experiments" / "dtn_crosscheck.py",
        primary_tlc,
        second_tlc,
        tlc_summary_path,
        refinement_path,
        results / "formal" / "refinement" / "refinement_events.jsonl",
        heldout_path,
        results / "heldout_temporal_frontier" / "heldout_temporal_manifest.json",
        results / "heldout_temporal_frontier" / "heldout_temporal_contrasts.csv",
        results / "heldout_temporal_frontier" / "heldout_temporal_frontier.pdf",
        one_path,
        results / "the_one_semantic_resolution" / "dt-0.1_buffer-1073741824"
        / "message_level_comparison.csv",
        results / "paper_assets" / "formal_refinement_upgrade.pdf",
        results / "paper_assets" / "the_one_semantic_resolution.pdf",
        junit_path,
        root / "source" / "paper.tex",
        root / "source" / "paper.pdf",
    ]
    missing = [str(path) for path in evidence_files if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"upgrade evidence missing: {missing}")
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "status": "validated",
        "tlc": {
            "safe_quorum": primary_counts,
            "independent_fingerprint_runs": 2,
            "matrix_cases": len(tlc_summary),
            "negative_controls": sum(case["negative_control"] for case in tlc_summary),
        },
        "refinement": refinement,
        "heldout_temporal": heldout_checks,
        "the_one_semantic_resolution": one_checks,
        "tests": test_checks,
        "limitations": [
            "TLC is bounded finite-state exploration, not an unbounded proof.",
            "Held-out overlays share three fixed public contact traces.",
            "The ONE continuous and AidMesh slot-causal semantics intentionally differ.",
        ],
        "files": [_hash_entry(root, path) for path in evidence_files],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output
