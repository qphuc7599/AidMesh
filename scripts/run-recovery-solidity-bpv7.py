"""Source-signed EVM recovery profile through BPv7 to real P1 settlement.

Retained admitted proofs, synthetic local participants; no Ed25519 translation.
The original retained committee has K=3,q=2, so only f=0 is claimed here.
"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from web3 import Web3
from aidmesh.experiments.semantic_e2e_campaign import _Harness, _register_domain

out = Path(sys.argv[1]).resolve()
out.mkdir(parents=True, exist_ok=False)
proof_path = ROOT/'results/comprehensive_revision_20260906/multi/fresh_proofs.json'
proofs = [p for p in json.loads(proof_path.read_text()) if p['mode'] == 1][:2]
h = _Harness(ROOT)
public = list(map(int, proofs[0]['publicSignals']))
domain = _register_domain(h, public=public, threshold=2, cap=8)
g = h.gateway.functions
def tx(fn):
    r = h.web3.eth.wait_for_transaction_receipt(fn.transact({'from':h.owner,'gas':8000000}))
    assert r.status == 1
    return int(r.gasUsed)
tx(h.settlement.functions.fundCampaign(domain, 8))
tx(g.enableRecovery(domain, 0))
messages = []
for number, row in enumerate(proofs):
    accept = number == 0
    right = Web3.keccak(text=f'evm-recovery-right-{number}')
    tx(g.issueRight(domain, right, h.sites[0], 1))
    cert = [right, Web3.keccak(text=f'evm-transfer-{number}'), *h.sites[:2],
            1, 1, 1, accept, b'', []]
    cert[8] = h.sign(h.sites[0], g.recoveryTransferHash(domain, cert).call())
    digest = g.recoveryCommitHash(domain, cert).call()
    cert[9] = [h.sign(w, digest) for w in h.witnesses[:2]]
    site = h.sites[1] if accept else h.sites[0]
    signals = list(map(int, row['publicSignals']))
    release = Web3.keccak(text=f'evm-release-{number}')
    receipt = [Web3.keccak(text=f'evm-receipt-{number}'), site, release,
               g.receiptSkeleton(signals, release, site, 1).call(), bytes(32), b'']
    receipt[4] = g.recoveryAuthorizationHash(signals, receipt, cert).call()
    payload = g.receiptPayload(domain, g.proofStatementHash(signals).call(), *receipt[:5]).call()
    receipt[5] = h.sign(site, payload)
    p = row['proof']
    a = list(map(int,p['pi_a'][:2])); c = list(map(int,p['pi_c'][:2]))
    b = [[int(p['pi_b'][i][1]),int(p['pi_b'][i][0])] for i in range(2)]
    fn = g.verifyRecoveryAndSettle(a,b,c,signals,receipt,cert)
    assert fn.call({'from':h.relayer})
    messages.append(dict(decision='accept' if accept else 'reclaim',
                         calldata=fn._encode_transaction_data()))
sent = out/'signed_calldata.json'
sent.write_text(json.dumps(messages, sort_keys=True))
def linux(p): return '/mnt/'+p.drive[0].lower()+str(p)[2:].replace('\\','/')
subprocess.run(['wsl','--exec','bash',linux(ROOT/'testbed/run-signed-evidence.sh'),
    linux(ROOT/'tmp/dtn7rs-v0.21.0/dtn7-0.21.0/bin'),linux(sent),linux(out/'bpv7')],
    check=True, timeout=90)
received = out/'bpv7/received.json'
assert received.read_bytes() == sent.read_bytes()
results = []
for m in json.loads(received.read_text()):
    kwargs = dict(to=h.gateway.address, data=m['calldata'], gas=8000000, **{'from':h.relayer})
    r = h.web3.eth.wait_for_transaction_receipt(h.web3.eth.send_transaction(kwargs))
    assert r.status == 1
    replay = h.web3.eth.wait_for_transaction_receipt(h.web3.eth.send_transaction(kwargs))
    assert replay.status == 0
    results.append(dict(decision=m['decision'],status=r.status,gas=int(r.gasUsed),replay_status=replay.status))
credits = [h.settlement.functions.credits(domain, s).call() for s in h.sites[:2]]
assert credits == [1,1]
(out/'summary.json').write_text(json.dumps(dict(results=results,credits=credits,
    real_groth16=True,bpv7_unchanged=True,solidity_settlement=True,
    committee=dict(K=3,q=2,f=0),
    proof_source_sha256=hashlib.sha256(proof_path.read_bytes()).hexdigest(),
    scope='retained proofs; synthetic EVM-profile signers; no actuator or full recovery-view execution'),indent=2))
print(json.dumps(results))
