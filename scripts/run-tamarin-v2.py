"""Run and archive the selected AidMesh protocol-V2 Tamarin lemmas."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shlex
import subprocess
from pathlib import Path


LEMMAS = [
    "executable_authorized_release",
    "executable_ambiguous_lock",
    "executable_terminal_accept",
    "executable_terminal_reclaim",
    "honest_intent_non_equivocation",
    "authorization_certificate_non_conflict",
    "automatic_actuator_at_most_once_per_slot",
    "terminal_certificate_non_conflict",
    "terminal_application_exclusive",
    "settlement_first_valid_wins",
    "right_single_spend",
    "cross_domain_receipt_separation",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def wsl_path(path: Path) -> str:
    resolved = str(path.resolve()).replace("\\", "/")
    if len(resolved) >= 3 and resolved[1:3] == ":/":
        return f"/mnt/{resolved[0].lower()}/{resolved[3:]}"
    completed = subprocess.run(
        ["wsl.exe", "bash", "-lc", f"wslpath -a {shlex.quote(resolved)}"],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=False)
    model_wsl = wsl_path(args.model)
    binary_wsl = wsl_path(args.binary)
    selected: list[dict[str, object]] = []

    for lemma in LEMMAS:
        command = (
            f"{shlex.quote(binary_wsl)} --prove={shlex.quote(lemma)} "
            f"{shlex.quote(model_wsl)}"
        )
        completed = subprocess.run(
            ["wsl.exe", "bash", "-lc", command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        log_text = completed.stdout + completed.stderr
        log_path = args.output / f"{lemma}.log"
        log_path.write_text(log_text, encoding="utf-8")
        pattern = re.compile(
            rf"^\s*{re.escape(lemma)} \([^\n]+\): verified \((\d+) steps\)\s*$",
            re.MULTILINE,
        )
        match = pattern.search(log_text)
        verified = completed.returncode == 0 and match is not None
        selected.append(
            {
                "lemma": lemma,
                "verified": verified,
                "steps": int(match.group(1)) if match else None,
                "return_code": completed.returncode,
                "log": log_path.name,
                "log_sha256": sha256(log_path),
            }
        )
        if not verified:
            raise RuntimeError(f"Tamarin did not verify {lemma}; see {log_path}")

    manifest = {
        "schema_version": 2,
        "tool": "tamarin-prover",
        "tool_version": "1.12.0",
        "maude_version": "3.2",
        "maude_version_warning": (
            "Tamarin 1.12.0 reports Maude 3.2 outside its supported-version "
            "whitelist, while its installation self-check reports OK."
        ),
        "model": args.model.name,
        "model_sha256": sha256(args.model),
        "binary_sha256": sha256(args.binary),
        "all_selected_lemmas_verified": all(row["verified"] for row in selected),
        "selected_proofs": selected,
        "claim_scope": (
            "Unbounded-session symbolic K=4/q=3/f=1 honest-intersection; "
            "AuthorizationIntentV1 non-equivocation; path-independent release-slot "
            "automatic at-most-once abstraction across quorum/fallback intents; mutually exclusive certified "
            "accept/reclaim; first-valid-wins; right single-spend; and receipt "
            "domain separation. Cryptographic primitives, bytecode, storage "
            "firmware, and physical sensors are external obligations."
        ),
    }
    manifest_path = args.output / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    checksum_paths = [args.model, *sorted(args.output.glob("*.log")), manifest_path]
    checksums = args.output / "SHA256SUMS.txt"
    checksums.write_text(
        "\n".join(f"{sha256(path)}  {path.name}" for path in checksum_paths) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
