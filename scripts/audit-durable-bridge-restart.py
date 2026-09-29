"""Re-attest retained closed receipts using read-only reopened journals.

No new goods release or settlement. Checks the new durable-CLOSED export guard
against the exact journals and signatures from the integrated 100-voucher run.
"""
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from aidmesh.experiments.semantic_e2e_campaign import _Harness
from aidmesh.protocol.recovery_evm_bridge import attest_closed_receipt

directory=Path(sys.argv[1]).resolve();h=_Harness(ROOT)
public=json.loads((directory/'public_keys.json').read_text())
def verify(m,d,s):
    try:Ed25519PublicKey.from_public_bytes(bytes.fromhex(public[m])).verify(bytes.fromhex(s),bytes.fromhex(d));return True
    except (InvalidSignature,ValueError):return False
def wire(v):
    if isinstance(v,bytes):return '0x'+v.hex()
    if isinstance(v,list):return [wire(x) for x in v]
    return v
packets=json.loads((directory/'bpv7/received.json').read_text())
for i,packet in enumerate(packets):
    item=packet['item'];cert=item[5]
    cert[0]=bytes.fromhex(cert[0][2:]);cert[1]=bytes.fromhex(cert[1][2:])
    cert[8]=bytes.fromhex(cert[8][2:]);cert[9]=[bytes.fromhex(s[2:]) for s in cert[9]]
    site=item[4][1]
    path=directory/f'transfer-{i:03d}'/f'{site}.sqlite'
    connection=sqlite3.connect(f'file:{path.as_posix()}?mode=ro',uri=True)
    local=SimpleNamespace(site=site,db=connection)
    voucher=dict(zip(('a','b','c','public'),item[:4]))
    bound=attest_closed_receipt(packet['closed_receipt'],voucher,cert,release_site=local,
        context=(h.web3.eth.chain_id,h.gateway.address,h.settlement.address),verify_ed=verify,sign_evm=h.sign)
    assert wire(bound)==item[4]
    connection.close()
(directory/'closed_guard_restart_audit.json').write_text(json.dumps(dict(
    reopened_journals=len(packets),identical_evm_receipts=len(packets),new_actuator_calls=0,
    scope='post-run read-only restart conformance for durable CLOSED export guard'),indent=2))
print(f'{len(packets)} persisted receipts re-attested identically after read-only restart')
