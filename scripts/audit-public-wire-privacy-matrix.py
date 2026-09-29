"""Fixed equality attacks, no fitting, on actual 100-voucher BPv7 calldata.

Ground truth is isolated from attack features. Cross-window and traffic-analysis
cells remain unmeasured because this dataset does not contain those observations.
"""

import hashlib
import itertools
import json
import sys
from pathlib import Path

proof_path, wire_path, output_path = map(Path, sys.argv[1:])
proofs = json.loads(proof_path.read_text())
packets = json.loads(wire_path.read_text())
output_path.mkdir(parents=True, exist_ok=False)
truth = {str(p["publicSignals"][7]): p["admission"] for p in proofs}
observed = [p["item"][3] for p in packets]
assert len(observed) == len(truth) == 100
# Features selected before examining truth; no admission ID enters prediction.
attacks = {
    "nullifier_equality": lambda a, b: str(a[7]) == str(b[7]),
    "window_equality": lambda a, b: str(a[9]) == str(b[9]),
    "fallback_tag_within_window": lambda a, b: (
        str(a[8]) == str(b[8]) == "1" and str(a[9]) == str(b[9]) and str(a[10]) == str(b[10])
    ),
    "always_same_control": lambda a, b: True,
    "always_different_control": lambda a, b: False,
}
rows = []
for name, predict in attacks.items():
    counts = dict(tp=0, fp=0, tn=0, fn=0)
    for a, b in itertools.combinations(observed, 2):
        prediction = predict(a, b)
        same = truth[str(a[7])] == truth[str(b[7])]
        counts[("t" if prediction == same else "f") + ("p" if prediction else "n")] += 1
    tp, fp, tn, fn = (counts[k] for k in ("tp", "fp", "tn", "fn"))
    rows.append(
        dict(
            attack=name,
            observer="plaintext relay/gateway/chain calldata",
            confusion=counts,
            precision=tp / (tp + fp) if tp + fp else None,
            recall=tp / (tp + fn),
            balanced_accuracy=(tp / (tp + fn) + tn / (tn + fp)) / 2,
        )
    )
result = dict(
    measured=rows,
    input_sha256={
        str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (proof_path, wire_path)
    },
    vouchers=100,
    holders=len(set(truth.values())),
    windows=len({str(p[9]) for p in observed}),
    independent_pairs=False,
    scope=("Descriptive finite-fixture audit; pairs share vouchers, "
           "no population confidence interval."),
    unmeasured=[
        "cross-window unlinkability",
        "issuer-observer collusion",
        "timing/location correlation",
        "encrypted transport observer",
        "membership inference",
        "attribute inference",
        "network-size/holder-count generalization",
        "independent confirmatory holdout",
    ],
    full_privacy_matrix_complete=False,
)
(output_path / "matrix.json").write_text(json.dumps(result, indent=2))
print(json.dumps(result))
