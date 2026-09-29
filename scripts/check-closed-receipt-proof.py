"""Generate an explicit unsafe export control, run and check both Tamarin jobs."""

import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
out = ROOT / "results/baseline_formal_matrix_20260906"
source = ROOT / "formal/AidMeshClosedReceiptBridge.spthy"
safe = source.read_text()
unsafe = safe.replace(
    "theory AidMeshClosedReceiptBridge", "theory AidMeshClosedReceiptBridgeUnsafe"
)
guard = "[ !ClosedRow(sk, context, slot, intent, ack) ]"
assert unsafe.count(guard) == 1
unsafe = unsafe.replace(guard, "[ !Key(sk, context), In(<slot, intent, ack>) ]")
(out / "unsafe_export.spthy").write_text(unsafe)
records = []
for label, model, lemma in (
    ("safe", source, None),
    ("unsafe", out / "unsafe_export.spthy", "authenticated_receipt_requires_closed"),
):
    wsl_root = "/mnt/d/DoAnChuyenNganh/AirProof"
    model_path = str(model.relative_to(ROOT)).replace("\\", "/")
    output_path = str((out / f"{label}_checked.spthy").relative_to(ROOT)).replace("\\", "/")
    command = [
        "wsl",
        "--cd",
        wsl_root,
        "./external/tamarin/tamarin-prover",
        model_path,
        "--prove" + ("=" + lemma if lemma else ""),
        "--with-maude=" + wsl_root + "/tools/maude-3.5.1/bin/maude",
        "--output=" + output_path,
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    log = result.stdout + result.stderr
    (out / f"{label}_checked.log").write_text(log)
    assert result.returncode == 0 and "wellformedness check failed" not in log
    if label == "safe":
        assert log.count(": verified (") == 5 and "falsified" not in log
    else:
        assert "authenticated_receipt_requires_closed (all-traces): falsified" in log
    records.append(
        dict(
            model=model_path,
            sha256=hashlib.sha256(model.read_bytes()).hexdigest(),
            expected_outcome="5 verified" if label == "safe" else "counterexample found",
        )
    )
(out / "mechanization.json").write_text(
    json.dumps(
        dict(
            models=records,
            scope=("Closed-receipt export submodel; "
                   "no implementation refinement or view-change proof"),
            tamarin="1.12.0",
            maude="3.5.1",
        ),
        indent=2,
    )
)
print(json.dumps(records))
