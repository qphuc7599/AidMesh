"""Verify view-change refinement and a highest-prepared unsafe control."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "formal/AidMeshRecoveryViewRefinement.spthy"
OUT = ROOT / "results/recovery_view_refinement_20260910"
OUT.mkdir(parents=True, exist_ok=True)


def wsl(path: Path) -> str:
    value = path.resolve().as_posix()
    return "/mnt/" + value[0].lower() + value[2:]


safe_source = MODEL.read_text()
unsafe_rule = """
rule UnsafeNewProposalIgnoresHighest:
  [ !Report('h1', domain, slot, 'v1', 'v0', 'accept'),
    !Report('h2', domain, slot, 'v1', 'v0', 'accept') ]
  --[ Proposal(domain, slot, 'v1', 'reclaim') ]->
  [ !ProposalFact(domain, slot, 'v1', 'reclaim') ]
"""
unsafe = OUT / "AidMeshRecoveryViewUnsafeHighestPrepared.spthy"
unsafe.write_text(
    safe_source.replace(
        "theory AidMeshRecoveryViewRefinement",
        "theory AidMeshRecoveryViewUnsafeHighestPrepared",
        1,
    ).rsplit("end", 1)[0]
    + unsafe_rule
    + "\nend\n"
)

jobs = [
    (
        MODEL,
        [
            ("executable_view_change", "verified"),
            ("certificates_do_not_conflict", "verified"),
            ("new_view_preserves_highest_prepared", "verified"),
            ("actuation_requires_prior_certificate", "verified"),
            ("actuator_at_most_once", "verified"),
            ("settlement_requires_closed_receipt", "verified"),
        ],
    ),
    (unsafe, [("certificates_do_not_conflict", "falsified")]),
]
records = []
for model, lemmas in jobs:
    command = [
        "wsl.exe",
        "timeout",
        "120",
        wsl(ROOT / "external/tamarin/tamarin-prover"),
        "+RTS",
        "-N2",
        "-RTS",
        *(f"--prove={name}" for name, _ in lemmas),
        wsl(model),
    ]
    run = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=150,
    )
    log = run.stdout + run.stderr
    log_path = OUT / f"{model.stem}.log"
    log_path.write_text(log, encoding="utf-8")
    for lemma, expected in lemmas:
        match = re.search(
            r"^\s*"
            + re.escape(lemma)
            + r" \([^\n]+\): "
            + expected
            + r"(?: - found trace)? \((\d+) steps\)",
            log,
            re.M,
        )
        row = {
            "model": str(model.relative_to(ROOT)),
            "lemma": lemma,
            "expected": expected,
            "matched": match is not None,
            "steps": int(match.group(1)) if match else None,
            "returncode": run.returncode,
            "model_sha256": hashlib.sha256(model.read_bytes()).hexdigest(),
            "log_sha256": hashlib.sha256(log_path.read_bytes()).hexdigest(),
        }
        records.append(row)
        print(json.dumps(row), flush=True)

(OUT / "manifest.json").write_text(json.dumps(records, indent=2))
if not all(row["matched"] and row["returncode"] == 0 for row in records):
    raise SystemExit("formal refinement did not meet its declared oracle")
