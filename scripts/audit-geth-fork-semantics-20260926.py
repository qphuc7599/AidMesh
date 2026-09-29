"""Extract and validate the fork semantics of the preserved Geth execution."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--geth", type=Path, required=True)
    parser.add_argument("--datadir", type=Path, required=True)
    parser.add_argument("--settlement-summary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    version = subprocess.run(
        [str(args.geth), "version"], capture_output=True, text=True, check=True
    ).stdout.strip()
    dumped = subprocess.run(
        [str(args.geth), "--datadir", str(args.datadir), "dumpgenesis"],
        capture_output=True,
        text=True,
        check=True,
    )
    genesis = json.loads(dumped.stdout.strip().splitlines()[-1])
    config = genesis["config"]
    settlement = json.loads(args.settlement_summary.read_text())
    observed = {
        "chainId": config.get("chainId"),
        "pragueTime": config.get("pragueTime"),
        "osakaTime": config.get("osakaTime"),
        "gasLimit": int(genesis["gasLimit"], 16),
        "eip7825Cap": int(settlement["eip7825Cap"]),
        "oversizedTransactionRejected": bool(settlement["oversizedTransactionRejected"]),
        "maxTransactionGas": int(settlement["maxTransactionGas"]),
    }
    checks = {
        "prague_active_at_genesis": observed["pragueTime"] == 0,
        "osaka_active_at_genesis": observed["osakaTime"] == 0,
        "block_limit_is_60m": observed["gasLimit"] == 60_000_000,
        "eip7825_cap_is_2_pow_24": observed["eip7825Cap"] == 2**24,
        "over_cap_rejected": observed["oversizedTransactionRejected"],
        "finalizer_under_cap": observed["maxTransactionGas"] < observed["eip7825Cap"],
    }
    if not all(checks.values()):
        raise AssertionError(checks)
    payload = {
        "profile": "preserved-geth-fork-semantics-audit",
        "gethVersion": version.splitlines(),
        "observed": observed,
        "checks": checks,
        "interpretation": (
            "The preserved execution enabled both Prague and Osaka at genesis. "
            "EIP-7825 compatibility is attributed to the active post-Prague rules, "
            "not to Prague/Pectra alone."
        ),
        "inputSha256": {
            "settlementSummary": sha256(args.settlement_summary),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
