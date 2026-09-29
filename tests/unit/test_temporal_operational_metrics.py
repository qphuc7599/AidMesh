from __future__ import annotations

import pandas as pd

from aidmesh.analysis.temporal_operational_metrics import summarize_operational_metrics


def test_operational_summary_preserves_failure_inclusive_metrics() -> None:
    frame = pd.DataFrame(
        [
            {
                "context": "c",
                "profile": "p",
                "seed": 1,
                "generated_requests": 4,
                "delivery_ratio": 0.5,
                "failure_inclusive_rmst_h": 13.0,
                "forwarded_bytes_per_request": 2048.0,
                "queue_copy_drops": 2,
                "final_duplicate_copies": 1,
                "configured_storage_bytes": 32768,
                "copy_budget": 2,
            },
            {
                "context": "c",
                "profile": "p",
                "seed": 2,
                "generated_requests": 6,
                "delivery_ratio": 1.0,
                "failure_inclusive_rmst_h": 2.0,
                "forwarded_bytes_per_request": 4096.0,
                "queue_copy_drops": 0,
                "final_duplicate_copies": 3,
                "configured_storage_bytes": 32768,
                "copy_budget": 2,
            },
        ]
    )

    summary = summarize_operational_metrics(frame).iloc[0]

    assert summary["seeds"] == 2
    assert summary["generated_requests"] == 10
    assert summary["delivery_ratio"] == 0.75
    assert summary["failure_inclusive_rmst_h"] == 7.5
    assert summary["queue_copy_drops"] == 1
