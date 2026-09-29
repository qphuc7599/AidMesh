"""Fail closed before admitting unvalidated baseline labels to confirmation."""

import json
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
registry = json.loads((root / "configs/development/original_baseline_validation.json").read_text())
blocked = {
    name: row["remaining"]
    for name, row in registry["baselines"].items()
    if row["eligible"] is not True or row["remaining"]
}
print(json.dumps(dict(eligible=not blocked, blocked=blocked), indent=2))
sys.exit(2 if blocked else 0)
