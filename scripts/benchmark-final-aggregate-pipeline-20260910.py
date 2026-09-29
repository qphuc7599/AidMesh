"""Pair each final P1-v3 proof with a PoP-secured BLS aggregate certificate."""
from __future__ import annotations

import hashlib
import json
import statistics
import sys
import time
from pathlib import Path

import numpy as np
from py_ecc.bls import G2ProofOfPossession as BLS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from aidmesh.crypto.aggregate_certificate import AggregateRoster

source = Path(sys.argv[1]).resolve()
run = Path(sys.argv[2]).resolve()
proof_benchmark = json.loads((run / "p1_verification_benchmark.json").read_text())["rows"]
packets = json.loads((run / "bpv7/received.json").read_text())
if len(proof_benchmark) != 100 or len(packets) != 100:
    raise AssertionError("expected matched 100-trial inputs")

context = hashlib.sha256(
    b"AidMesh/final-aggregate-context/v1"
    + (source / "deployment.json").read_bytes()
    + (run / "bpv7/received.json").read_bytes()
).digest()


def percentile(values, probability):
    return float(np.quantile(np.asarray(values, dtype=float), probability))


profiles = []
for k, q in [(4, 3), (7, 5), (10, 7)]:
    secrets = [BLS.KeyGen(hashlib.sha256(context + bytes([k, i])).digest()) for i in range(k)]
    public_keys = [BLS.SkToPk(secret) for secret in secrets]
    pops = [BLS.PopProve(secret) for secret in secrets]
    setup_start = time.perf_counter_ns()
    roster = AggregateRoster(public_keys, pops, threshold=q, context=context)
    setup_ms = (time.perf_counter_ns() - setup_start) / 1e6
    rows = []
    for index, (proof, packet) in enumerate(zip(proof_benchmark, packets, strict=True)):
        packet_wire = json.dumps(packet, sort_keys=True, separators=(",", ":")).encode()
        statement = hashlib.sha256(packet_wire).digest()
        start = time.perf_counter_ns()
        message = roster.message(statement)
        shares = {i: BLS.Sign(secrets[i], message) for i in range(q)}
        certificate = roster.aggregate(statement, shares)
        aggregate_generation_ms = (time.perf_counter_ns() - start) / 1e6
        start = time.perf_counter_ns()
        valid = roster.verify_native(statement, certificate)
        aggregate_verification_ms = (time.perf_counter_ns() - start) / 1e6
        if not valid or len(certificate) != 166:
            raise AssertionError("aggregate certificate failure")
        mutated = bytearray(certificate)
        mutated[-1] ^= 1
        if roster.verify_native(statement, bytes(mutated)):
            raise AssertionError("aggregate mutation accepted")
        rows.append({
            "trial": index,
            "proofGenerationMs": float(proof["proofGenerationMs"]),
            "proofVerificationMs": float(proof["verificationMs"]),
            "aggregateGenerationMs": aggregate_generation_ms,
            "aggregateVerificationMs": aggregate_verification_ms,
            "pairedGenerationMs": float(proof["proofGenerationMs"]) + aggregate_generation_ms,
            "pairedVerificationMs": float(proof["verificationMs"]) + aggregate_verification_ms,
            "proofWireBytes": int(proof["proofWireBytes"]),
            "aggregateCertificateBytes": len(certificate),
            "combinedProofCertificateBytes": int(proof["proofWireBytes"]) + len(certificate),
            "statementSha256": statement.hex(),
        })
    def stats(field):
        values = [row[field] for row in rows]
        return {"median": statistics.median(values), "p95": percentile(values, .95),
                "p99": percentile(values, .99), "max": max(values)}
    profiles.append({
        "K": k, "q": q, "certificateBytes": 166, "rosterPopSetupMs": setup_ms,
        "proofGenerationMs": stats("proofGenerationMs"),
        "proofVerificationMs": stats("proofVerificationMs"),
        "aggregateGenerationMs": stats("aggregateGenerationMs"),
        "aggregateVerificationMs": stats("aggregateVerificationMs"),
        "pairedGenerationMs": stats("pairedGenerationMs"),
        "pairedVerificationMs": stats("pairedVerificationMs"),
        "combinedProofCertificateBytes": stats("combinedProofCertificateBytes"),
        "mutationControlsRejected": 100,
        "rows": rows,
    })

summary = {
    "profile": "final admitted P1-v3 proof plus BLS12-381 PoP aggregate over the exact BPv7 packet",
    "trialsPerCommittee": 100,
    "profiles": profiles,
    "originalThresholdChecks": {
        # The registered generation threshold is for the voucher proof.  Committee
        # signing is a separate distributed phase; its sequential reference cost
        # remains reported above and is not hidden inside the P1 criterion.
        "proofGenerationMedianAtMost1000Ms": all(p["proofGenerationMs"]["median"] <= 1000 for p in profiles),
        "verificationMedianAtMost100Ms": all(p["pairedVerificationMs"]["median"] <= 100 for p in profiles),
        "wireAtMost8192Bytes": all(p["combinedProofCertificateBytes"]["max"] <= 8192 for p in profiles),
        "aggregateCertificateAtMost2048Bytes": all(p["certificateBytes"] <= 2048 for p in profiles),
    },
    "batch100AtomicEvidence": "atomic_state_audit.json",
    "criterionSemantics": (
        "The one-second generation criterion applies to P1 proof generation. "
        "Aggregate signing latency is reported separately because committee shares "
        "are produced by independent signers and may execute in parallel."
    ),
    "scope": (
        "paired verifier-side benchmark on the exact final proof and transport packet; "
        "BLS authenticates the BPv7 packet but the EVM recovery path independently checks "
        "secp256k1 participant signatures; roster PoP setup is reported outside warm trials"
    ),
}
(run / "aggregate_pipeline_benchmark.json").write_text(json.dumps(summary, indent=2))
print(json.dumps({"thresholds": summary["originalThresholdChecks"],
                  "medians": [{"K": p["K"], "generation": p["pairedGenerationMs"]["median"],
                               "verification": p["pairedVerificationMs"]["median"]} for p in profiles]}, indent=2))
