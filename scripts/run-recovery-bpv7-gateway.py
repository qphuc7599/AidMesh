"""Recovery -> real synthetic actuator log -> receipt -> BPv7 -> receiver.

P1 verification uses the real local EVM verifier and retained admitted proofs.
Reconciliation is in the off-chain gateway SQLite; no Solidity settlement claim.
"""
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from aidmesh.crypto.evm_wire import proof_domain
from aidmesh.experiments.semantic_e2e_campaign import _Harness
from aidmesh.protocol.recovered_release import RecoveredReleaseSite
from aidmesh.protocol.recovery_gateway import RecoveryGateway
from aidmesh.protocol.release import ReleaseSlotKey
from aidmesh.protocol.terminal_recovery import DurableTerminalReplica,canonical,digest

out=Path(sys.argv[1]).resolve();out.mkdir(parents=True,exist_ok=False)
proofs=json.loads((ROOT/'results/comprehensive_revision_20260906/multi/fresh_proofs.json').read_text())
rows=[r for r in proofs if r['mode']==1][:2]
h=_Harness(ROOT)
members=tuple(f'w{i}' for i in range(4));keys={m:Ed25519PrivateKey.generate() for m in (*members,'a','b')}
(out/'public_keys.json').write_text(json.dumps({m:k.public_key().public_bytes(
    serialization.Encoding.Raw,serialization.PublicFormat.Raw).hex() for m,k in keys.items()},indent=2))
def sign(m,d):return keys[m].sign(bytes.fromhex(d)).hex()
def verify(m,d,s):
    try:keys[m].public_key().verify(bytes.fromhex(s),bytes.fromhex(d));return True
    except (ValueError,InvalidSignature,KeyError):return False
public=list(map(int,rows[0]['publicSignals']));domain=proof_domain(public).hex()
def voucher_verify(statement,v):
    try:
        if set(v)!={'a','b','c','public'}:return False
        p=v['public'];slot=statement['slot']
        if (proof_domain(p).hex()!=domain or p[7]!=int(slot['nullifier']) or p[4]!=statement['value']
                or p[1]!=slot['epoch'] or p[8]!=1 or slot['domain_id']!=domain
                or p[11:14]!=[h.web3.eth.chain_id,int(h.gateway.address,16),int(h.settlement.address,16)]):return False
        return bool(h.verifier.functions.verifyProof(v['a'],v['b'],v['c'],p).call())
    except (ValueError,TypeError,KeyError):return False
packets=[]
for index,(row,decision) in enumerate(zip(rows,('accept','reclaim'),strict=True)):
    directory=out/decision;directory.mkdir()
    def site(name):
        return RecoveredReleaseSite(directory/f'{name}.sqlite',site=name,domain=domain,epoch=1,
            initial=1 if name=='a' else 0,sites=('a','b'),witnesses=members,faults=1,sign=sign,verify=verify)
    a,b=site('a'),site('b');payload=a.begin(f't{index}','b',1);rules=b.recovery_rules(payload)
    replicas=[DurableTerminalReplica(directory/f'{m}.sqlite',rules,m,sign) for m in members]
    reports=[r.change_view(1) for r in replicas]
    proposal=replicas[1].propose(1,decision,reports)
    prepared=[r.prepare(proposal) for r in replicas];commits=[r.commit(prepared) for r in replicas]
    a.finalize(payload,commits);b.finalize(payload,commits)
    target=b if decision=='accept' else a
    p=row['proof'];voucher=dict(a=list(map(int,p['pi_a'][:2])),
        b=[[int(p['pi_b'][i][1]),int(p['pi_b'][i][0])] for i in range(2)],
        c=list(map(int,p['pi_c'][:2])),public=list(map(int,row['publicSignals'])))
    slot=ReleaseSlotKey(domain,1,str(voucher['public'][7]),target.site)
    def actuator(release,value):
        with (directory/'actuator.jsonl').open('a') as stream:
            stream.write(canonical(dict(release=release,value=value))+'\n');stream.flush()
            import os
            os.fsync(stream.fileno())
        return digest(dict(release=release,value=value,device='synthetic-counter'))
    result=target.release(slot,value=1,mode='fallback',authorization=voucher,
                          verify_authorization=voucher_verify,actuator=actuator)
    assert result['invoked'] and target.balance()==0
    packets.append(dict(transfer=json.loads(payload),commits=commits,
                        receipt=result['receipt'],voucher=voucher))
    a.close();b.close()
    for r in replicas:r.close()
sent=out/'signed_packets.json';sent.write_text(canonical(packets))
def linux(path):return '/mnt/'+path.drive[0].lower()+str(path)[2:].replace('\\','/')
subprocess.run(['wsl','--exec','bash',linux(ROOT/'testbed/run-signed-evidence.sh'),
    linux(ROOT/'tmp/dtn7rs-v0.21.0/dtn7-0.21.0/bin'),linux(sent),linux(out/'bpv7')],check=True,timeout=90)
received=(out/'bpv7/received.json').read_bytes();assert received==sent.read_bytes()
gateway=RecoveryGateway(out/'gateway.sqlite',domain=domain,epoch=1,sites=('a','b'),
    witnesses=members,faults=1,verify=verify,verify_voucher=voucher_verify)
checks=0;accepted=[]
for packet in json.loads(received):
    # Every receipt field, transfer field, voucher public input and commit value.
    paths=[('transfer','body',k) for k in packet['transfer']['body']]
    paths += [('receipt','body','statement','slot',k) for k in packet['receipt']['body']['statement']['slot']]
    paths += [('receipt','body',k) for k in ('release_id','authorization_digest','acknowledgment')]
    paths += [('receipt','body','statement',k) for k in ('mode','value')]
    paths += [('voucher','public',i) for i in range(17)]
    paths += [('commits',0,'message','value')]
    for path in paths:
        changed=deepcopy(packet);parent=changed
        for key in path[:-1]:parent=parent[key]
        value=parent[path[-1]];parent[path[-1]]=value+1 if type(value) is int else value+'x'
        try:gateway.accept(canonical(changed))
        except (ValueError,TypeError):checks+=1
        else:raise AssertionError(f'mutation accepted: {path}')
    accepted.append(gateway.accept(canonical(packet)))
    assert gateway.accept(canonical(packet))['credit']==0
gateway.close()
gateway=RecoveryGateway(out/'gateway.sqlite',domain=domain,epoch=1,sites=('a','b'),
    witnesses=members,faults=1,verify=verify,verify_voucher=voucher_verify)
for packet in packets:assert gateway.accept(canonical(packet))['credit']==0
gateway.close()
(out/'summary.json').write_text(json.dumps(dict(accepted=accepted,mutations_rejected=checks,
    bpv7_exact=True,recovery_outcomes=['accept','reclaim'],real_evm_P1_verification=True,
    solidity_settlement=False,scope='single host, real BPv7 processes, off-chain recovery gateway'),indent=2))
print(f'2 recovery/receipt packets reconciled; {checks} field mutations rejected.')
