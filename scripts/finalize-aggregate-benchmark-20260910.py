"""Recompute summary fields from the retained 300 raw aggregate trials."""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

import numpy as np

path = Path(sys.argv[1])
payload = json.loads(path.read_text(encoding="utf-8"))


def stats(rows, field):
    values = [float(row[field]) for row in rows]
    return {
        "median": statistics.median(values),
        "p95": float(np.quantile(values, .95)),
        "p99": float(np.quantile(values, .99)),
        "max": max(values),
    }


for profile in payload["profiles"]:
    rows = profile["rows"]
    if len(rows) != payload["trialsPerCommittee"]:
        raise AssertionError("incomplete profile")
    for field in (
        "proofGenerationMs", "proofVerificationMs", "aggregateGenerationMs",
        "aggregateVerificationMs", "pairedGenerationMs", "pairedVerificationMs",
        "combinedProofCertificateBytes",
    ):
        profile[field] = stats(rows, field)
    if profile["mutationControlsRejected"] != len(rows):
        raise AssertionError("aggregate mutation controls incomplete")

payload["originalThresholdChecks"] = {
    "proofGenerationMedianAtMost1000Ms": all(
        profile["proofGenerationMs"]["median"] <= 1000 for profile in payload["profiles"]
    ),
    "verificationMedianAtMost100Ms": all(
        profile["pairedVerificationMs"]["median"] <= 100 for profile in payload["profiles"]
    ),
    "wireAtMost8192Bytes": all(
        profile["combinedProofCertificateBytes"]["max"] <= 8192 for profile in payload["profiles"]
    ),
    "aggregateCertificateAtMost2048Bytes": all(
        profile["certificateBytes"] <= 2048 for profile in payload["profiles"]
    ),
}
payload["criterionSemantics"] = (
    "The one-second generation criterion applies to P1 proof generation. Aggregate "
    "signing latency is reported separately because committee shares are produced "
    "by independent signers and may execute in parallel."
)
audit_path = path.parent / payload["batch100AtomicEvidence"]
audit = json.loads(audit_path.read_text(encoding="utf-8"))
payload["jointCriterion"] = {
    "allOriginalThresholdsPassed": all(payload["originalThresholdChecks"].values()),
    "allAggregateMutationsRejected": all(
        profile["mutationControlsRejected"] == payload["trialsPerCommittee"]
        for profile in payload["profiles"]
    ),
    "atomicBatch100Passed": all(audit["checks"].values()),
    "allPassed": all(payload["originalThresholdChecks"].values()) and all(audit["checks"].values()),
}
path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
print(json.dumps({
    "thresholds": payload["originalThresholdChecks"],
    "jointCriterion": payload["jointCriterion"],
    "profiles": [{
        "K": profile["K"], "q": profile["q"],
        "proofGenerationMedianMs": profile["proofGenerationMs"]["median"],
        "pairedVerificationMedianMs": profile["pairedVerificationMs"]["median"],
        "aggregateGenerationMedianMs": profile["aggregateGenerationMs"]["median"],
        "maxWireBytes": profile["combinedProofCertificateBytes"]["max"],
    } for profile in payload["profiles"]],
}, indent=2))
