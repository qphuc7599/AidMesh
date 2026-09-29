"""Check structural validity of the five frozen external-trace contexts."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "configs/postreview_20260928/external_validity_three_family.yaml"
OUTPUT = ROOT / "results/external_validity_three_family_20260928/TRACE_PREFLIGHT.json"


def main() -> int:
    registry = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
    rows = []
    for item in registry["contexts"]:
        path = ROOT / item["path"] / "contacts.csv"
        frame = pd.read_csv(path)
        row = {
            "family": item["family"],
            "context": item["context"],
            "rows": int(len(frame)),
            "duplicateRows": int(frame.duplicated().sum()),
            "nonPositiveDuration": int((frame["end_us"] <= frame["start_us"]).sum()),
            "selfContacts": int(
                (frame["node_a"].astype(str) == frame["node_b"].astype(str)).sum()
            ),
            "nonPositiveCapacity": int((frame["bytes_per_second"] <= 0).sum()),
            "nodes": int(
                pd.concat([frame["node_a"], frame["node_b"]]).astype(str).nunique()
            ),
            "startUs": int(frame["start_us"].min()),
            "endUs": int(frame["end_us"].max()),
        }
        row["passed"] = all(
            row[name] == 0
            for name in (
                "duplicateRows",
                "nonPositiveDuration",
                "selfContacts",
                "nonPositiveCapacity",
            )
        )
        rows.append(row)
    result = {
        "families": len({row["family"] for row in rows}),
        "contexts": len(rows),
        "allPassed": all(row["passed"] for row in rows),
        "traces": rows,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if result["allPassed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
