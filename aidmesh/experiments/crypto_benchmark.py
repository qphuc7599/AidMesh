"""Microbenchmark for the executable P0 disclosure reference profile."""

from __future__ import annotations

import statistics
import time
from pathlib import Path

import pandas as pd

from aidmesh.crypto.merkle import EntitlementLeaf, EntitlementTree
from aidmesh.crypto.primitives import BeneficiarySecrets, Ed25519Keypair
from aidmesh.crypto.reference import ReferenceDisclosureProfile, ReferenceVerificationContext


def _percentile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    position = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * probability))))
    return ordered[position]


def run_reference_crypto_benchmark(iterations: int = 1000, warmup: int = 100) -> pd.DataFrame:
    if iterations <= 0 or warmup < 0:
        raise ValueError("invalid benchmark iteration count")
    issuers = [Ed25519Keypair.from_seed(bytes([index]) * 32) for index in (1, 2, 3)]
    beneficiary = BeneficiarySecrets.from_seed(b"b" * 32)
    profile = ReferenceDisclosureProfile([issuer.public_key for issuer in issuers], 2)
    campaign_id = b"c" * 32
    credential = profile.issue(
        beneficiary=beneficiary,
        campaign_id=campaign_id,
        epoch=1,
        cell="z1",
        group="g1",
        quotas=(("food", 1),),
        signing_issuers=issuers[:2],
    )
    leaf = EntitlementLeaf(1, "z1", "g1", "food", 1, 2)
    tree = EntitlementTree([leaf])
    context = ReferenceVerificationContext(campaign_id, tree.root, b"v" * 32, b"n" * 32)
    membership = tree.proof_for(leaf)

    for _ in range(warmup):
        presentation = profile.present(
            beneficiary=beneficiary,
            credential=credential,
            entitlement=leaf,
            membership=membership,
            context=context,
        )
        assert profile.verify(presentation, context).accepted

    generation: list[float] = []
    verification: list[float] = []
    presentation = None
    for _ in range(iterations):
        start = time.perf_counter_ns()
        presentation = profile.present(
            beneficiary=beneficiary,
            credential=credential,
            entitlement=leaf,
            membership=membership,
            context=context,
        )
        generation.append((time.perf_counter_ns() - start) / 1_000_000)
        start = time.perf_counter_ns()
        decision = profile.verify(presentation, context)
        verification.append((time.perf_counter_ns() - start) / 1_000_000)
        if not decision.accepted:
            raise AssertionError("valid reference presentation was rejected")
    assert presentation is not None
    serialized_bytes = len(presentation.unsigned_encode()) + len(presentation.possession_signature)
    rows = []
    for operation, values in (("generation", generation), ("verification", verification)):
        rows.append(
            {
                "profile": "aidmesh-reference-disclosure-v1",
                "privacy_claim_supported": False,
                "zero_knowledge": False,
                "operation": operation,
                "iterations": iterations,
                "median_ms": statistics.median(values),
                "p95_ms": _percentile(values, 0.95),
                "p99_ms": _percentile(values, 0.99),
                "presentation_bytes": serialized_bytes,
            }
        )
    return pd.DataFrame(rows)


def write_crypto_benchmark(frame: pd.DataFrame, output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output, index=False)
    return output
