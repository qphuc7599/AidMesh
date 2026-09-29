"""Bind the SQLite authority and fresh-Geth settlement ablation reports."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    if len(sys.argv) != 5:
        raise SystemExit("usage: script NO_LEDGER GETH_REPLAY FINAL_GETH OUTPUT")
    no_ledger_path, geth_path, final_path, output = (
        Path(value).resolve() for value in sys.argv[1:]
    )
    no_ledger = load(no_ledger_path)
    geth = load(geth_path)
    final = load(final_path)

    input_hash = no_ledger["inputsSha256"]["received"]
    same_input = input_hash == no_ledger["inputsSha256"]["packets"] == geth["inputSha256"]
    same_geth_receipts = (
        geth["totalGas"] == final["gasUsed"] == 85_734_940
        and geth["finalize"]["gasUsed"] == final["finalizeGasUsed"] == 14_203_542
        and [row["gasUsed"] for row in geth["stageTransactions"]]
        == [row["gasUsed"] for row in final["chunks"]]
    )
    same_credits = (
        sorted(value for _, value in no_ledger["positive"]["credits"])
        == sorted(geth["credits"])
        == sorted(final["credits"])
        == [50, 100]
    )
    result = {
        "profile": "matched settlement-layer ablation over exact final signed evidence",
        "records": 100,
        "sameInputSha256": same_input,
        "sameCredits": same_credits,
        "sameGethExecutionReceipts": same_geth_receipts,
        "sqliteAuthority": {
            "trials": no_ledger["trials"],
            "componentSumP50Ms": no_ledger["latencyMs"]["trialwiseComponentSum"]["p50"],
            "componentSumP95Ms": no_ledger["latencyMs"]["trialwiseComponentSum"]["p95"],
            "authorityDatabaseBytes": no_ledger["storageAndLedgerCost"][
                "sqliteBytesAfterCheckpoint"
            ],
            "gas": 0,
            "atomicBatch": no_ledger["allChecksPassed"],
            "replayRejected": no_ledger["negativeControls"]["replayRejected"],
            "corruptBatchRolledBack": (
                no_ledger["negativeControls"]["corruptBatchRowsAfterRollback"] == 0
                and no_ledger["negativeControls"]["corruptBatchCreditsAfterRollback"] == 0
                and no_ledger["negativeControls"]["corruptBatchConsumedRightsAfterRollback"] == 0
            ),
            "trust": no_ledger["trustBoundary"]["noLedger"],
        },
        "geth": {
            "freshExecutions": 1,
            "settlementWallMs": geth["settlementWallMs"],
            "blockPeriodSeconds": 1,
            "submittedCalldataBytes": geth["totalCalldataBytes"],
            "totalGas": geth["totalGas"],
            "finalizerGas": geth["finalize"]["gasUsed"],
            "feeFormula": "85734940 * feeRate",
            "trust": no_ledger["trustBoundary"]["geth"],
        },
        "measurementBoundary": (
            "SQLite reports 30 trialwise sums of separate Node P1 and Python semantic/"
            "commit components; Geth reports one fresh 11-transaction execution with a "
            "one-second block period. Values characterize the two configured profiles "
            "and are not an inferential speedup estimate."
        ),
        "physicalFactBoundary": no_ledger["trustBoundary"]["physicalFact"],
        "allChecksPassed": (
            no_ledger["allChecksPassed"]
            and geth["allChecksPassed"]
            and same_input
            and same_geth_receipts
            and same_credits
        ),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if result["allChecksPassed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
