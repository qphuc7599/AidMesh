"""Fresh admitted P1 proofs + source-signed evidence -> BPv7 -> real local EVM.

Gateway consumes received proof/signature fields unchanged, with no resigning.
Single-host integration; actuator and recovery-v1 certificates are not exercised.
"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from web3 import Web3
from aidmesh.crypto import evm_wire
from aidmesh.experiments.semantic_e2e_campaign import _Harness,_register_domain
from aidmesh.protocol.root_endorsement import RootStatement,verify_root_endorsement
from aidmesh.protocol.witness import SQLiteWitnessStore,PersistOutcome

out=Path(sys.argv[1]).resolve()
endorsement=json.loads((out/'endorsement.json').read_text())
statement=RootStatement(**endorsement['statement'])
def verify(member,d,s):
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(endorsement['public_keys'][member])).verify(
            bytes.fromhex(s),bytes.fromhex(d));return True
    except (ValueError,InvalidSignature):
        return False
assert verify_root_endorsement(statement,endorsement['shares'],members=endorsement['members'],
    threshold=endorsement['threshold'],faults=endorsement['faults'],verify=verify)
proofs=json.loads((out/'fresh_proofs.json').read_text())
h=_Harness(ROOT,verifier_source=Path(sys.argv[2]).resolve() if len(sys.argv)>2 else None)
public=list(map(int,proofs[0]['publicSignals']))
assert public[5]==statement.credential_root and public[6]==statement.entitlement_root
assert public[11:14]==[h.web3.eth.chain_id,int(h.gateway.address,16),int(h.settlement.address,16)]
assert h.gateway.functions.semanticCommitteeHash(1,2,8,list(h.witnesses)).call()==public[16]
domain=_register_domain(h,public=public,threshold=2,cap=8)
issued_value=sum(int(p['publicSignals'][4]) for p in proofs if p['mode']==0)
tx=h.settlement.functions.fundCampaign(domain,issued_value).transact({'from':h.owner})
assert h.web3.eth.wait_for_transaction_receipt(tx).status==1
stores={w:SQLiteWitnessStore(out/f'evm-witness-{i}.sqlite',signer_id=w)
        for i,w in enumerate(h.witnesses)}
def witness_sign(w,key,value):
    if stores[w].persist_once((domain.hex(),1,key),value.hex()) is PersistOutcome.CONFLICT:
        raise ValueError('witness conflict')
    return h.sign(w,value)
messages=[]
quorum=[p for p in proofs if p['mode']==0]
for number,q in enumerate(quorum):
    mode=0 if number<5 else 1
    row=next(p for p in proofs if p['admission']==q['admission'] and p['index']==q['index'] and p['mode']==mode)
    signals=list(map(int,row['publicSignals']))
    site=h.sites[0]
    release=Web3.keccak(text=f'admitted-release-{number}')
    receipt_id=Web3.keccak(text=f'admitted-receipt-{number}')
    skeleton=evm_wire.receipt_skeleton(signals,release,site,1)
    right=Web3.keccak(text=f'admitted-right-{number}')
    if mode==0:
        authorization=evm_wire.authorization_intent(signals,release,skeleton,site,1)
        signatures=[witness_sign(w,str(signals[7]),authorization) for w in h.witnesses[:2]]
    else:
        tx=h.gateway.functions.issueRight(domain,right,site,signals[4]).transact({'from':h.owner})
        assert h.web3.eth.wait_for_transaction_receipt(tx).status==1
        authorization=evm_wire.right_authorization_hash(signals,release,skeleton,site,right,1,[])
        signatures=[]
    payload=evm_wire.receipt_payload(domain,evm_wire.proof_statement_hash(signals),receipt_id,
                                    site,release,skeleton,authorization)
    receipt=[receipt_id,site,release,skeleton,authorization,h.sign(site,payload)]
    proof=row['proof']
    a=list(map(int,proof['pi_a'][:2]));c=list(map(int,proof['pi_c'][:2]))
    b=[[int(proof['pi_b'][i][1]),int(proof['pi_b'][i][0])] for i in range(2)]
    assert h.verifier.functions.verifyProof(a,b,c,signals).call()
    messages.append(dict(mode=mode,a=a,b=b,c=c,signals=signals,receipt=receipt,
                         signatures=signatures,right=right,epoch=1))
for store in stores.values():store.close()
def wire(value):
    if isinstance(value,bytes):return '0x'+value.hex()
    if isinstance(value,list):return [wire(v) for v in value]
    if isinstance(value,dict):return {k:wire(v) for k,v in value.items()}
    return value
sent=out/'signed_evidence.json'
sent.write_text(json.dumps(wire(messages),sort_keys=True,separators=(',',':')))
def linux(path):
    path=Path(path).resolve()
    return '/mnt/'+path.drive[0].lower()+str(path)[2:].replace('\\','/')
subprocess.run(['wsl','--exec','bash',linux(ROOT/'testbed/run-signed-evidence.sh'),
    linux(ROOT/'tmp/dtn7rs-v0.21.0/dtn7-0.21.0/bin'),linux(sent),linux(out/'bpv7')],
    check=True,timeout=90)
received=out/'bpv7/received.json'
assert sent.read_bytes()==received.read_bytes()
rows=[]
for message in json.loads(received.read_text()):
    args=[message[k] for k in ('a','b','c','signals','receipt')]
    if message['mode']==0:
        call=h.gateway.functions.verifyQuorumAndSettle(*args,message['epoch'],message['signatures'])
    else:
        call=h.gateway.functions.verifyRightAndSettle(*args,message['right'],message['epoch'],[])
    before=h.settlement.functions.credits(domain,message['receipt'][1]).call()
    tx=call.transact({'from':h.relayer,'gas':8000000})
    result=h.web3.eth.wait_for_transaction_receipt(tx)
    after=h.settlement.functions.credits(domain,message['receipt'][1]).call()
    assert result.status==1 and after-before==message['signals'][4]
    tx=call.transact({'from':h.relayer,'gas':8000000})
    h.web3.eth.wait_for_transaction_receipt(tx)
    assert h.settlement.functions.credits(domain,message['receipt'][1]).call()==after
    rows.append(dict(mode=message['mode'],credit_delta=after-before,gas=result.gasUsed))
(out/'evm_bpv7_summary.json').write_text(json.dumps(dict(settlements=len(rows),rows=rows,
    unchanged_signed_payload_sha256=hashlib.sha256(sent.read_bytes()).hexdigest(),
    duplicate_credit_prevented=True,root_endorsement_verified_before_registration=True,
    scope='three BPv7 processes and real local EVM; source signatures persisted; no actuator or recovery integration'),indent=2))
print(f'{len(rows)} admitted vouchers settled from unchanged BPv7 payload; duplicates credited zero.')
