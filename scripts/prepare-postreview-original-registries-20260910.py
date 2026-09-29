"""Derive a current-source replay and unseen-seed holdout from the frozen registry."""

from __future__ import annotations

import copy
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = ROOT / "configs/confirmatory/registry.yaml"
DESTINATION = ROOT / "configs/postreview_20260910"
REPLAY = DESTINATION / "original_registry_replay_v1.yaml"
HOLDOUT = DESTINATION / "original_registry_holdout_v1.yaml"
HOLDOUT_SEED_STARTS = {
    8000: 95000,
    8100: 95100,
    8130: 95130,
    8160: 95160,
    8200: 95200,
    8230: 95230,
    8260: 95260,
    8300: 95300,
    8330: 95330,
    8400: 95400,
}


def _base(registry_id: str) -> dict:
    value = yaml.safe_load(ORIGINAL.read_text(encoding="utf-8"))
    value["registry_id"] = registry_id
    value["claims"]["primary_questions"] = ["RQ1", "RQ2"]
    value["claims"]["decision_rules"].extend(
        [
            "This post-review campaign preserves every original scenario, method, seed count, "
            "comparison, endpoint, practical margin and multiplicity rule.",
            "The replay is a reproducibility check and cannot become a new holdout; the second "
            "registry uses disjoint Monte Carlo seeds and is opened once after local freeze.",
            "Raw negative, null, failed and boundary cells remain in coverage and analysis.",
        ]
    )
    value["approval"] = {
        "owner": "T.-D. Tran; local prospective specification before execution",
        "date_utc": "2026-09-10",
        "meaningful_effect_statement": value["approval"]["meaningful_effect_statement"],
    }
    return value


def _write(path: Path, value: dict) -> None:
    path.write_text(
        yaml.safe_dump(value, sort_keys=False, allow_unicode=True, width=100),
        encoding="utf-8",
        newline="\n",
    )


def main() -> int:
    DESTINATION.mkdir(parents=True, exist_ok=True)
    replay = _base("POSTREVIEW_ORIGINAL_REPLAY_20260910")
    holdout = copy.deepcopy(_base("POSTREVIEW_ORIGINAL_HOLDOUT_20260910"))
    for scenario in holdout["scenarios"]:
        original_start = int(scenario["seed_start"])
        scenario["seed_start"] = HOLDOUT_SEED_STARTS[original_start]
    _write(REPLAY, replay)
    _write(HOLDOUT, holdout)
    print(REPLAY.relative_to(ROOT).as_posix())
    print(HOLDOUT.relative_to(ROOT).as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
