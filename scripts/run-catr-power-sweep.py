"""Run the registered post-confirmatory CATR detection-power sweep."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from aidmesh.experiments.catr_power_sweep import run_power_sweep


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--workers", type=int, default=min(10, os.cpu_count() or 2))
    args = parser.parse_args()
    assets = run_power_sweep(
        args.registry.resolve(),
        args.output.resolve(),
        workspace=args.workspace.resolve(),
        workers=args.workers,
    )
    print(json.dumps({name: str(path) for name, path in assets.items()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
