"""Selected symbolic correspondences, non-vacuity, and explicit unsafe controls."""

import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results/q1_revision_20260906/formal_controls"
OUT.mkdir(parents=True, exist_ok=True)
base = ROOT / "formal/AidMeshV3Composition.spthy"
source = base.read_text()
fallback = source[
    source.index("rule AuthorizeFallback:") : source.index("rule PersistReleaseIntent:")
]
fallback = fallback.replace("rule AuthorizeFallback:", "rule UnsafeAuthorizeWithoutDebit:")
fallback = fallback.replace(
    "releaseid, skeleton, 'fallback'>),\n    RightOwned(domain, right, site, nonce) ]",
    "releaseid, skeleton, 'fallback'>) ]",
)
fallback = fallback.replace(
    "RightSpent(domain, right, site, nonce, releaseid),\n      "
    "FallbackDebit(domain, epoch, nullifier, site, releaseid, right),\n      ",
    "",
)
settle = """
rule UnsafeSettleBeforeHandoff:
  [ SettlementOpen(domain, epoch, nullifier),
    !Authorized(domain, epoch, nullifier, intent), In(evidence) ]
  --[ Settled(domain, epoch, nullifier,
       <'ReceiptV2', domain, epoch, nullifier, intent, evidence>) ]->
  [ !SettlementWinner(domain, epoch, nullifier,
       <'ReceiptV2', domain, epoch, nullifier, intent, evidence>) ]
"""


def wsl(path):
    value = path.resolve().as_posix()
    return "/mnt/" + value[0].lower() + value[2:]


jobs = [
    (
        base,
        [
            "fallback_authorization_has_debit",
            "settlement_has_closed_handoff",
            "executable_fallback_settlement",
        ],
        "verified",
    )
]
settle += '''
lemma unsafe_settlement_executes_without_handoff:
  exists-trace
  "Ex domain epoch nullifier receipt #i.
     Settled(domain, epoch, nullifier, receipt) @ i
     & not (Ex d e n s intent evidence #j. HandoffClosed(d,e,n,s,intent,evidence) @ j)
     & not (Ex d t r a b #k. TransferCreated(d,t,r,a,b) @ k)
     & not (Ex d e n intent #k. AuthorizationCertificate(d,e,n,intent) @ k)"
'''
for name, rule, lemma, expected in [
    ("unsafe_fallback", fallback, "fallback_authorization_has_debit", "falsified"),
    ("unsafe_settlement", settle, "unsafe_settlement_executes_without_handoff", "verified"),
]:
    path = OUT / f"{name}.spthy"
    path.write_text(source.rsplit("end", 1)[0] + rule + "\nend\n")
    jobs.append((path, [lemma], expected))

records = []
for model, lemmas, expected in jobs:
    command = [
        "wsl.exe",
        "timeout", "60",
        wsl(ROOT / "external/tamarin/tamarin-prover"),
        "+RTS",
        "-N2",
        "-RTS",
        *(f"--prove={x}" for x in lemmas),
        wsl(model),
    ]
    run = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90
    )
    log = run.stdout + run.stderr
    log_path = OUT / (model.stem + ".log")
    log_path.write_text(log, encoding="utf-8")
    for lemma in lemmas:
        match = re.search(
            r"^\s*" + lemma + r" \([^\n]+\): " + expected + r"(?: - found trace)? \((\d+) steps\)",
            log,
            re.M,
        )
        record = dict(
            model=str(model.relative_to(ROOT)),
            lemma=lemma,
            expected=expected,
            matched=match is not None,
            steps=int(match[1]) if match else None,
            returncode=run.returncode,
            model_sha256=hashlib.sha256(model.read_bytes()).hexdigest(),
            log_sha256=hashlib.sha256(log_path.read_bytes()).hexdigest(),
        )
        records.append(record)
        print(json.dumps(record), flush=True)
(OUT / "manifest.json").write_text(json.dumps(records, indent=2))
assert all(row["matched"] and row["returncode"] == 0 for row in records)
