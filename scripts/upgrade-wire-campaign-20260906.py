from pathlib import Path
import re

p = Path("aidmesh/experiments/semantic_e2e_campaign.py")
s = p.read_text()
if "from aidmesh.crypto import evm_wire" not in s:
    s = s.replace("from aidmesh.common.canonical import stable_hash", "from aidmesh.common.canonical import stable_hash\nfrom aidmesh.crypto import evm_wire")
for old, new in {"proofDomain": "proof_domain", "proofStatementHash": "proof_statement_hash",
                 "receiptPayload": "receipt_payload", "receiptSkeleton": "receipt_skeleton",
                 "authorizationIntent": "authorization_intent",
                 "rightTransitionStatement": "right_transition_statement",
                 "rightAuthorizationHash": "right_authorization_hash"}.items():
    s = re.sub(r"harness\.gateway\.functions\." + old + r"\((.*?)\)\.call\(\)",
               lambda m: "evm_wire." + new + "(" + m.group(1) + ")", s, flags=re.S)
p.write_text(s)

p = Path("tests/evm/test_semantic_authorization_gateway.py")
s = p.read_text()
for old, new in {"proofStatementHash": "proof_statement_hash",
                 "receiptPayload": "receipt_payload", "receiptSkeleton": "receipt_skeleton",
                 "rightTransitionStatement": "right_transition_statement",
                 "rightAuthorizationHash": "right_authorization_hash"}.items():
    s = re.sub(r"system\.gateway\.functions\." + old + r"\((.*?)\)\.call\(\)",
               lambda m: "evm_wire." + new + "(" + m.group(1) + ")", s, flags=re.S)
p.write_text(s)
