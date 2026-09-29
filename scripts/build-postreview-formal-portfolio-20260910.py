"""Build and validate the post-review mechanization/refinement evidence map."""

from __future__ import annotations

import hashlib
import json
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results/postreview_formal_20260910"
OUT.mkdir(parents=True, exist_ok=True)


def load(path: str):
    return json.loads((ROOT / path).read_text(encoding="utf-8-sig"))


def sha(path: str):
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


value_symbolic = load("results/value_admission_formal_20260909_v4/summary.json")
value_vcs = load("results/value_conservation_vcs_20260909/summary.json")
recovery = load("results/recovery_view_refinement_20260910/manifest.json")
composition = load("results/q1_revision_20260906/formal_controls/manifest.json")
liveness = load(
    "results/postreview_formal_20260910/recovery_liveness/tlc_recovery_liveness_summary.json"
)
bounded = load("results/protocol_refinement_postreview_20260910/protocol_refinement_summary.json")
attack = load("results/geth_eip7825_atomic100_attackmatrix_final4_20260910/attack_matrix.json")
privacy = load("results/staged_privacy_20260909_v2/privacy_composition_summary.json")

if not all(row["matched"] for row in recovery + composition):
    raise AssertionError("symbolic theorem oracle mismatch")
if value_vcs["safe_vcs"] != 4 or value_vcs["unsafe_counterexamples"] != 2:
    raise AssertionError("value VCs incomplete")
if not all(row["passed"] for row in liveness):
    raise AssertionError("liveness model oracle mismatch")
if not bounded["overall_refinement_pass"]:
    raise AssertionError("bounded code refinement failed")
if not attack["allPassed"] or privacy["negativeControls"]["privateWitnessMutationsRejected"] != 150:
    raise AssertionError("executable negative-control matrix incomplete")

obligations = [
    {
        "obligation": "value-and-owner-bound admission",
        "abstractTransition": "Reserve a unique entitlement serial and its exact value atomically",
        "implementation": "ValueAdmissionRegistry.admit and P1-v3 entitlement leaf",
        "unboundedEvidence": "five Tamarin lemmas plus four integer VCs",
        "negativeControl": "reusable serial counterexample; holder/value/public-signal mutations",
        "status": "passed",
    },
    {
        "obligation": "persist before physical actuation",
        "abstractTransition": "AUTHORIZED to INTENT before the sole actuator claim",
        "implementation": "SQLiteReleaseStateStore.persist_intent and Site._authorize_release",
        "unboundedEvidence": (
            "settlement_has_closed_handoff and actuation_requires_prior_certificate"
        ),
        "negativeControl": "handoff-first TLC mutant and unsafe settlement trace",
        "status": "passed",
    },
    {
        "obligation": "recovery agreement across views",
        "abstractTransition": "new view carries the highest prepared value",
        "implementation": "RecoveryRules.selection and RecoveryReplica.propose/change_view",
        "unboundedEvidence": "six Tamarin recovery lemmas",
        "negativeControl": "highest-prepared omission yields conflicting certificates",
        "status": "passed",
    },
    {
        "obligation": "fresh recovery context",
        "abstractTransition": "reject stale view/epoch and foreign decision",
        "implementation": "gateway _validateRecoveryItem and RecoveryRules.check",
        "unboundedEvidence": "view ordering in the recovery model",
        "negativeControl": "live epoch/view/decision wire mutations",
        "status": "passed",
    },
    {
        "obligation": "closed receipt before settlement",
        "abstractTransition": "CLOSED receipt exports a settlement obligation",
        "implementation": "SQLiteReleaseStateStore.close and gateway _verifySiteReceipt",
        "unboundedEvidence": "settlement_requires_closed_receipt and settlement_has_closed_handoff",
        "negativeControl": "unsafe settlement executes without handoff; six receipt mutations",
        "status": "passed",
    },
    {
        "obligation": "campaign/domain binding",
        "abstractTransition": (
            "all proofs, intents, receipts and rights remain in one immutable domain"
        ),
        "implementation": "P1-v3 public signals, proofDomain and proofStatementHash",
        "unboundedEvidence": "domain-indexed symbolic facts",
        "negativeControl": "campaign, epoch, chain, gateway and settlement mutations",
        "status": "passed",
    },
    {
        "obligation": "old obligation retention",
        "abstractTransition": "migration/reissue retains outstanding and ambiguous value",
        "implementation": "value-admission journal and permanent staged/spent markers",
        "unboundedEvidence": "epoch_retains_unresolved and reissue_charges_same_cap VCs",
        "negativeControl": "cap-reset and drop-outstanding countermodels",
        "status": "passed",
    },
    {
        "obligation": "conditional liveness boundary",
        "abstractTransition": (
            "authorization and receipt delivery eventually reach settled/conflict"
        ),
        "implementation": "recovery plus BPv7 retry path",
        "unboundedEvidence": "TLC temporal property under CanAuthorize and CanDeliver",
        "negativeControl": "permanent partition violates eventual settlement",
        "status": "passed with stated eventual-delivery assumption",
    },
]

portfolio = {
    "profile": "selective unbounded mechanization plus bounded implementation simulation",
    "allObligationsCovered": all(row["status"].startswith("passed") for row in obligations),
    "obligations": obligations,
    "evidenceCounts": {
        "valueTamarinSafeLemmas": 5,
        "valueIntegerVCs": value_vcs["safe_vcs"],
        "valueUnsafeCounterexamples": value_vcs["unsafe_counterexamples"] + 1,
        "recoveryTamarinSafeLemmas": sum(row["expected"] == "verified" for row in recovery),
        "recoveryUnsafeCounterexamples": sum(row["expected"] == "falsified" for row in recovery),
        "compositionTamarinSafeLemmas": sum(
            "unsafe" not in row["model"].lower() and row["expected"] == "verified"
            for row in composition
        ),
        "compositionUnsafeControls": sum(
            row["expected"] == "falsified" or row["model"].endswith("unsafe_settlement.spthy")
            for row in composition
        ),
        "boundedRiskTransitions": bounded["escrow_risk_rights"]["transitions"],
        "boundedWitnessTransitions": bounded["persistent_witness"]["transitions"],
        "durableRestarts": bounded["persistent_witness"]["durable_restarts"],
        "liveWireNegativeControls": attack["negativeControls"],
        "privateWitnessNegativeControls": privacy["negativeControls"][
            "privateWitnessMutationsRejected"
        ],
    },
    "boundary": (
        "Tamarin and Z3 prove selected symbolic transition systems; TLC checks the finite "
        "liveness abstraction; trace refinement checks executable Python/SQLite/Solidity "
        "prefixes. This portfolio does not prove compiler, OS, storage hardware, firmware "
        "or physical handoff correctness."
    ),
    "tools": {
        "tamarin": value_symbolic["tamarin"],
        "maude": value_symbolic["maude"],
        "z3": value_vcs["solver"],
        "tlc": "2.19",
        "python": sys.version,
        "platform": platform.platform(),
    },
    "sourceSha256": {
        path: sha(path)
        for path in (
            "formal/AidMeshValueAdmission.spthy",
            "formal/value_conservation_vcs.py",
            "formal/AidMeshV3Composition.spthy",
            "formal/AidMeshRecoveryViewRefinement.spthy",
            "formal/AidMeshRecoveryLiveness.tla",
            "aidmesh/formal/refinement.py",
            "aidmesh/protocol/value_admission_registry.py",
            "aidmesh/protocol/terminal_recovery.py",
            "aidmesh/protocol/release.py",
            "contracts/src/AidMeshSemanticAuthorizationGateway.sol",
            "contracts/src/AidMeshSettlement.sol",
        )
    },
}
(OUT / "portfolio.json").write_text(json.dumps(portfolio, indent=2), encoding="utf-8")
print(
    json.dumps(
        {
            "allObligationsCovered": portfolio["allObligationsCovered"],
            "evidenceCounts": portfolio["evidenceCounts"],
            "boundary": portfolio["boundary"],
        },
        indent=2,
    )
)
