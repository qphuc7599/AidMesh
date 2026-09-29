"""Analyze the frozen CATR primary and factorial campaigns."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aidmesh.analysis.catr_confirmatory import write_catr_confirmatory_analysis


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--primary", type=Path, required=True)
    parser.add_argument("--factorial", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bootstrap-repetitions", type=int, default=5000)
    args = parser.parse_args()
    assets = write_catr_confirmatory_analysis(
        args.primary,
        args.factorial,
        args.output,
        repetitions=args.bootstrap_repetitions,
    )
    print(json.dumps({name: str(path) for name, path in assets.items()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
