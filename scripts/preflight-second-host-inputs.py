"""Fail-closed input preflight for the Linux second-host campaign.

The check covers every immutable input needed by the expensive TLC, Groth16,
privacy, held-out trace, and trace-to-EVM stages.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

import yaml


FIXED_INPUTS = (
    "requirements-linux.lock",
    "package-lock.json",
    "configs/e2e/registry_p1_v2_tagged_full.yaml",
    "configs/supporting_2026Q3/privacy_pareto/registry_sparse_preregistered.yaml",
    "zk/inputs/valid_quorum.json",
    "zk/inputs/valid_fallback.json",
    "zk/inputs/invalid_changed_value.json",
    "zk/build/aidmesh_voucher_js/aidmesh_voucher.wasm",
    "zk/build/aidmesh_dev_final.zkey",
    "zk/build/verification_key.json",
    "zk/domain_tags.json",
    "contracts/src/AidMeshGroth16Verifier.sol",
    "contracts/src/AidMeshVerifiedSettlementGateway.sol",
    "contracts/src/AidMeshSettlement.sol",
    "scripts/generate-e2e-inputs.mjs",
    "scripts/run-tla-integrated.sh",
)

TRACE_CONTEXTS = ("invs13", "lh10", "sfhh")
TRACE_FILES = ("contacts.csv", "mapping.json", "provenance.json")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _first_event(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        line = handle.readline()
    if not line:
        raise ValueError(f"empty privacy event log: {path}")
    event = json.loads(line)
    for key in ("run_id", "scenario_id", "method", "seed"):
        if key not in event:
            raise ValueError(f"missing {key!r} in first event: {path}")
    return event


def _privacy_closure(root: Path, registry_path: Path) -> dict[str, Any]:
    registry = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    source = root / str(registry["input_root"])
    scenario_id = str(registry["scenario_id"])
    expected_methods = tuple(str(item) for item in registry["expected_methods"])
    expected_seeds = int(registry["expected_seeds_per_method"])

    paths = sorted(
        path
        for path in source.rglob("events.jsonl")
        if scenario_id in path.as_posix()
    )
    identities: list[tuple[str, str, int]] = []
    selected: list[Path] = []
    for path in paths:
        event = _first_event(path)
        if str(event["scenario_id"]) != scenario_id:
            continue
        identities.append(
            (str(event["run_id"]), str(event["method"]), int(event["seed"]))
        )
        selected.append(path)

    if len(set(identities)) != len(identities):
        raise ValueError("duplicate privacy run identity")
    counts = Counter(method for _run_id, method, _seed in identities)
    expected_counts = {method: expected_seeds for method in expected_methods}
    if dict(sorted(counts.items())) != dict(sorted(expected_counts.items())):
        raise ValueError(
            "privacy coverage mismatch: "
            f"observed={dict(sorted(counts.items()))}, expected={expected_counts}"
        )
    for method in expected_methods:
        seeds = {
            seed
            for _run_id, observed_method, seed in identities
            if observed_method == method
        }
        if len(seeds) != expected_seeds:
            raise ValueError(
                f"privacy method {method} has {len(seeds)} distinct seeds; "
                f"expected {expected_seeds}"
            )

    digest = hashlib.sha256()
    for path in selected:
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(_sha256(path).encode("ascii"))
        digest.update(b"\n")
    return {
        "scenario_id": scenario_id,
        "runs": len(selected),
        "method_counts": dict(sorted(counts.items())),
        "bytes": sum(path.stat().st_size for path in selected),
        "selected_input_sha256": digest.hexdigest(),
    }


def run_preflight(root: Path) -> dict[str, Any]:
    root = root.resolve()
    missing = [relative for relative in FIXED_INPUTS if not (root / relative).is_file()]
    for context in TRACE_CONTEXTS:
        for filename in TRACE_FILES:
            relative = f"external/sociopatterns/normalized/{context}/{filename}"
            if not (root / relative).is_file():
                missing.append(relative)
    if missing:
        raise FileNotFoundError(
            "second-host input closure is incomplete:\n- " + "\n- ".join(sorted(missing))
        )

    registry_path = (
        root
        / "configs/supporting_2026Q3/privacy_pareto/"
        "registry_sparse_preregistered.yaml"
    )
    privacy = _privacy_closure(root, registry_path)
    fixed_hashes = {relative: _sha256(root / relative) for relative in FIXED_INPUTS}
    trace_hashes = {
        f"{context}/{filename}": _sha256(
            root / "external/sociopatterns/normalized" / context / filename
        )
        for context in TRACE_CONTEXTS
        for filename in TRACE_FILES
    }
    return {
        "schema_version": 1,
        "status": "pass",
        "fixed_input_count": len(FIXED_INPUTS),
        "trace_contexts": list(TRACE_CONTEXTS),
        "fixed_sha256": fixed_hashes,
        "trace_sha256": trace_hashes,
        "privacy": privacy,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run_preflight(args.root)
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
