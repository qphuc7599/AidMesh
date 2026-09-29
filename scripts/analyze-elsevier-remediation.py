"""Generate reviewer-requested direct and sensitivity analyses."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aidmesh.analysis.elsevier_remediation import write_elsevier_remediation_analysis


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--factorial",
        type=Path,
        default=Path("results/catr_confirmatory_20260822/factorial/catr_factorial_runs.csv"),
    )
    parser.add_argument(
        "--r200-manifest",
        type=Path,
        default=Path("external/asturies_wildfires/normalized/family_manifest.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("results/elsevier_remediation_20260901/analysis"),
    )
    parser.add_argument("--bootstrap-repetitions", type=int, default=9999)
    args = parser.parse_args()
    range_root = Path("results/elsevier_remediation_20260901/range_sensitivity")
    assets = write_elsevier_remediation_analysis(
        args.factorial,
        args.r200_manifest,
        {
            10: range_root / "r10/family_manifest.json",
            50: range_root / "r50/family_manifest.json",
            200: args.r200_manifest,
        },
        args.output,
        repetitions=args.bootstrap_repetitions,
    )
    print(json.dumps({name: str(path) for name, path in assets.items()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
