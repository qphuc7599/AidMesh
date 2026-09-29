"""Validate the paper-faithful Snappy approval/claim reconstruction.

This benchmark does not claim to be the unavailable authors' contract. It executes
the published majority-BLS approval, customer final signature, Algorithms 3/4
accounting vectors, and field-binding negative controls under a connected process.
"""

from __future__ import annotations

import hashlib
import json
import platform
import statistics
import sys
import time
from dataclasses import replace
from pathlib import Path

from eth_account import Account
from py_ecc.bls import G2ProofOfPossession as BLS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aidmesh.protocol.snappy_reference import (  # noqa: E402
    ApprovedPayment,
    SnappyApprovalRoster,
    customer_claim,
    literal_statekeeper_claim,
    sign_final_transaction,
    verify_approved_payment,
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(p * len(ordered)))]


def benchmark_profile(members: int, trials: int) -> dict:
    secrets = [
        BLS.KeyGen(hashlib.sha256(f"snappy/{members}/{index}".encode()).digest())
        for index in range(members)
    ]
    public = [BLS.SkToPk(secret) for secret in secrets]
    pops = [BLS.PopProve(secret) for secret in secrets]
    roster = SnappyApprovalRoster(
        public,
        pops,
        context=hashlib.sha256(f"connected-profile/{members}".encode()).digest(),
    )
    customer = Account.create(f"snappy-customer/{members}")
    times = {name: [] for name in ("shares", "aggregate", "customerSign", "jointVerify")}
    negative_rejections = 0
    wire_bytes = []
    for trial in range(trials):
        payment = ApprovedPayment(
            customer.address,
            "0x" + "22" * 20,
            trial + 1,
            1 + trial % 5,
            f"connected-{members}-{trial}",
            frozenset(),
        )
        started = time.perf_counter_ns()
        shares = {
            index: BLS.Sign(secrets[index], roster.message(payment))
            for index in range(roster.threshold)
        }
        times["shares"].append((time.perf_counter_ns() - started) / 1e6)
        started = time.perf_counter_ns()
        approval = roster.aggregate(payment, shares)
        times["aggregate"].append((time.perf_counter_ns() - started) / 1e6)
        started = time.perf_counter_ns()
        signature = sign_final_transaction(payment, approval, bytes(customer.key))
        times["customerSign"].append((time.perf_counter_ns() - started) / 1e6)
        started = time.perf_counter_ns()
        verified = verify_approved_payment(payment, approval, signature, roster)
        times["jointVerify"].append((time.perf_counter_ns() - started) / 1e6)
        if len(verified.signers) != roster.threshold:
            raise AssertionError("verified signer threshold mismatch")
        wire_bytes.append(len(approval) + len(signature))
        for mutation in (
            replace(payment, merchant="0x" + "33" * 20),
            replace(payment, index=payment.index + 1),
            replace(payment, value=payment.value + 1),
            replace(payment, transaction_id=payment.transaction_id + "x"),
        ):
            try:
                verify_approved_payment(mutation, approval, signature, roster)
            except ValueError:
                negative_rejections += 1
            else:
                raise AssertionError("mutated Snappy payment was accepted")
    return {
        "members": members,
        "majority": roster.threshold,
        "trials": trials,
        "wireBytes": sorted(set(wire_bytes)),
        "negativeControlsRejected": negative_rejections,
        "timingMs": {
            name: {
                "median": statistics.median(values),
                "p95": percentile(values, 0.95),
            }
            for name, values in times.items()
        },
    }


def accounting_vectors() -> dict:
    base = ApprovedPayment("customer", "merchant", 1, 5, "one", frozenset({0, 1, 2}))
    pending = replace(base, index=2, transaction_id="two")
    customer_vector = customer_claim(pending, [base], set(), 8, 4)
    different = replace(
        base,
        merchant="other",
        value=3,
        transaction_id="conflict-different",
        signers=frozenset({1, 2, 3}),
    )
    equal = replace(
        base,
        merchant="other",
        transaction_id="conflict-equal",
        signers=frozenset({1, 2, 3}),
    )
    return {
        "algorithm3CustomerClaim": list(customer_vector),
        "algorithm4DifferentValue": list(
            literal_statekeeper_claim(base, 5, [(base, different)], {1: 10}, 4)
        ),
        "algorithm4EqualValue": list(
            literal_statekeeper_claim(base, 5, [(base, equal)], {1: 10}, 4)
        ),
        "equalValueBoundary": (
            "Published Algorithm 4 computes an absolute value difference, hence an "
            "equal-value conflict debits zero in the literal reconstruction. This retained "
            "boundary prevents claiming full original-contract validation."
        ),
    }


def main() -> int:
    output = ROOT / "results/baseline_validation_20260928/snappy"
    output.mkdir(parents=True, exist_ok=True)
    profiles = [benchmark_profile(members, 100) for members in (4, 7, 10)]
    summary = {
        "profile": "paper-faithful Snappy connected approval and claim reconstruction",
        "status": "component-validated",
        "profiles": profiles,
        "accountingVectors": accounting_vectors(),
        "coverage": {
            "positivePayments": sum(profile["trials"] for profile in profiles),
            "fieldMutationRejections": sum(
                profile["negativeControlsRejected"] for profile in profiles
            ),
            "connectedOnly": True,
        },
        "boundary": (
            "Not the authors' original contract and not an offline-network comparator. "
            "The literal equal-value Algorithm 4 boundary remains unresolved."
        ),
        "environment": {"python": sys.version, "platform": platform.platform()},
    }
    summary_path = output / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    sources = [
        Path(__file__).resolve(),
        ROOT / "aidmesh/protocol/snappy_reference.py",
        ROOT / "tests/unit/test_snappy_reference.py",
        summary_path,
    ]
    (output / "CHECKSUMS.sha256").write_text(
        "".join(f"{sha256(path)}  {path.relative_to(ROOT).as_posix()}\n" for path in sources),
        encoding="ascii",
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
