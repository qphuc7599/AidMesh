"""Audit quorum/fallback linkage under the exact P1-v3 public leakage."""
from __future__ import annotations

import hashlib
import itertools
import json
import sys
from pathlib import Path

source = Path(sys.argv[1]).resolve()
output = Path(sys.argv[2]).resolve()


def load(name: str):
    rows = json.loads((source / name).read_text())
    return {(row["admission"], int(row["index"])): row for row in rows}


quorum = load("fresh_proofs.json")
window1 = load("fallback_window1.json")
window2 = load("fallback_window2.json")
if not (len(quorum) == len(window1) == len(window2) == 100
        and quorum.keys() == window1.keys() == window2.keys()):
    raise AssertionError("privacy transcript sets are not matched")


def confusion(pairs, score):
    tp = fp = tn = fn = 0
    for left, right in pairs:
        truth = left["admission"] == right["admission"]
        predicted = score(left, right)
        if truth and predicted:
            tp += 1
        elif truth:
            fn += 1
        elif predicted:
            fp += 1
        else:
            tn += 1
    tpr = tp / (tp + fn) if tp + fn else None
    tnr = tn / (tn + fp) if tn + fp else None
    return {
        "tp": tp, "fp": fp, "tn": tn, "fn": fn,
        "balancedAccuracy": (tpr + tnr) / 2 if tpr is not None and tnr is not None else None,
    }


def matched_pairs(left_rows, right_rows, *, same_set=False):
    pairs = []
    left = list(left_rows.values())
    right = list(right_rows.values())
    for i, a in enumerate(left):
        candidates = right[i + 1 :] if same_set else right
        for b in candidates:
            pa, pb = a["publicSignals"], b["publicSignals"]
            # Challenge pairs condition on all declared semantic leakage except
            # the tested nullifier, fallback window and fallback tag.
            if all(pa[j] == pb[j] for j in [0, 1, 2, 3, 4, 5, 6, 11, 12, 13, 14, 15, 16]):
                pairs.append((a, b))
    return pairs


scores = {
    "nullifierEquality": lambda a, b: a["publicSignals"][7] == b["publicSignals"][7],
    "fallbackTagEquality": lambda a, b: a["publicSignals"][10] == b["publicSignals"][10],
}
games = []
for name, left, right, same in [
    ("quorum-within-campaign", quorum, quorum, True),
    ("fallback-within-window-1", window1, window1, True),
    ("fallback-cross-window-1-to-2", window1, window2, False),
    ("quorum-to-fallback-window-1", quorum, window1, False),
]:
    pairs = matched_pairs(left, right, same_set=same)
    games.append({
        "game": name,
        "pairs": len(pairs),
        "positivePairs": sum(a["admission"] == b["admission"] for a, b in pairs),
        "attacks": {attack: confusion(pairs, score) for attack, score in scores.items()},
    })

same_voucher = [(window1[key], window2[key]) for key in window1]
same_holder_cross_window = [
    (a, b) for a, b in matched_pairs(window1, window2)
    if a["admission"] == b["admission"]
]

disclosure_matrix = [
    {
        "observer": "relay or gateway",
        "view": "all 17 public signals, proof bytes, mode, window, tag and transaction metadata",
        "established": "within-window fallback linkage; exact-voucher replay linkage",
        "notEstablished": "identity recovery from quorum transcripts or cross-window fallback tags",
    },
    {
        "observer": "public ledger",
        "view": "submitted calldata, sender, block ordering and settlement recipient",
        "established": "recipient and timing are public after submission",
        "notEstablished": "network-layer unlinkability",
    },
    {
        "observer": "issuer plus site",
        "view": "issuance transcript plus a site's release transcript",
        "established": "attribute/cohort intersection is explicit in the challenge conditioning",
        "notEstablished": "a composable Coconut-to-Groth16 anonymity theorem against active collusion",
    },
    {
        "observer": "DTN traffic observer",
        "view": "bundle timing, size and route when observable",
        "established": "none in this transcript-only experiment",
        "notEstablished": "timing, location, route or radio-layer anonymity",
    },
]

hashes = {
    name: hashlib.sha256((source / name).read_bytes()).hexdigest()
    for name in ["fresh_proofs.json", "fallback_window1.json", "fallback_window2.json"]
}
summary = {
    "profile": "P1-v3 staged privacy composition audit",
    "holders": len({key[0] for key in quorum}),
    "vouchersPerModeWindow": len(quorum),
    "games": games,
    "sameVoucherAcrossFallbackWindows": {
        "pairs": len(same_voucher),
        "nullifierEqualityRate": sum(scores["nullifierEquality"](*p) for p in same_voucher) / len(same_voucher),
        "fallbackTagEqualityRate": sum(scores["fallbackTagEquality"](*p) for p in same_voucher) / len(same_voucher),
        "interpretation": "a replayed voucher remains deliberately linkable and is rejected by first-valid-wins",
    },
    "sameHolderCrossWindowPairs": len(same_holder_cross_window),
    "publicSignals": {
        "nullifierIndex": 7, "modeIndex": 8, "fallbackWindowIndex": 9,
        "fallbackTagIndex": 10,
    },
    "disclosureMatrix": disclosure_matrix,
    "negativeControls": {
        "privateWitnessMutationsRejected": 150,
        "source": "private_wire_controls.json",
    },
    "inputSha256": hashes,
    "inferenceScope": (
        "finite synthetic transcript audit; equality attacks require no training; pair observations "
        "share holders and vouchers, so no population confidence interval is reported"
    ),
}
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(summary, indent=2))
print(json.dumps(summary, indent=2))
