#!/usr/bin/env python3
"""Run the registered Asturies event-respecting oracle audit."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aidmesh.experiments.catr_oracle_audit import run_oracle_audit


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--registry",
        type=Path,
        default=Path("configs/trace_families/catr_oracle_audit_20260822.yaml"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("results/catr_confirmatory_20260822/oracle_audit"),
    )
    parser.add_argument("--workspace", type=Path, default=Path("."))
    args = parser.parse_args()
    assets = run_oracle_audit(
        args.registry,
        args.output,
        workspace=args.workspace.resolve(),
    )
    print(json.dumps({name: str(path) for name, path in assets.items()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
