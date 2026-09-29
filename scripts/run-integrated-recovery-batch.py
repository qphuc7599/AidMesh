"""100 fresh admitted vouchers through durable recovery, dual attestation and EVM.

Synthetic single-host actuator; explicit local block gas budget, recorded even
when the atomic batch fails. No rejection is swallowed to claim batch success.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey,Ed25519PublicKey
from web3 import Web3
from aidmesh.experiments.semantic_e2e_campaign import _Harness,_register_domain
from aidmesh.crypto.evm_wire import proof_domain
from aidmesh.protocol.terminal_recovery import DurableTerminalReplica,canonical,digest
from aidmesh.protocol.recovered_release import RecoveredReleaseSite
from aidmesh.protocol.release import ReleaseSlotKey
from aidmesh.protocol.recovery_evm_bridge import attest_terminal,attest_closed_receipt,transfer_hash
from aidmesh.protocol.root_endorsement import RootStatement,verify_root_endorsement

source=Path(sys.argv[1]).resolve(); out=Path(sys.argv[2]).resolve()
gas_limit=int(sys.argv[3]) if len(sys.argv)>3 and sys.argv[3] not in ('-','none') else None
rpc_url=sys.argv[4] if len(sys.argv)>4 and sys.argv[4] not in ('-','none') else None
verifier_source=Path(sys.argv[5]).resolve() if len(sys.argv)>5 and sys.argv[5] not in ('-','none') else None
chunk_size=int(sys.argv[6]) if len(sys.argv)>6 else 100
settlement_profile=sys.argv[7] if len(sys.argv)>7 else 'direct'
if not 1<=chunk_size<=100: raise ValueError('chunk size must be 1..100')
if settlement_profile not in ('direct','staged-atomic'):
    raise ValueError('settlement profile must be direct or staged-atomic')
out.mkdir(parents=True,exist_ok=False)
rows=json.loads((source/'fresh_proofs.json').read_text())
assert len(rows)==100 and len({r['publicSignals'][7] for r in rows})==100
endorsement=json.loads((source/'endorsement.json').read_text());root_statement=RootStatement(**endorsement['statement'])
def verify_root(member,digest_value,signature):
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(endorsement['public_keys'][member])).verify(
            bytes.fromhex(signature),bytes.fromhex(digest_value));return True
    except (InvalidSignature,ValueError,KeyError):return False
assert verify_root_endorsement(root_statement,endorsement['shares'],members=endorsement['members'],
    threshold=endorsement['threshold'],faults=endorsement['faults'],verify=verify_root)
deployment=json.loads((source/'deployment.json').read_text()) if (source/'deployment.json').exists() else None
h=_Harness(ROOT,block_gas_limit=gas_limit,verifier_source=verifier_source,rpc_url=rpc_url,
    deployment=deployment)
h.witnesses=(*h.accounts[1:4],h.accounts[9])
g=h.gateway.functions;members=tuple(sorted(h.witnesses));sites=h.sites[:2]
keys={m:Ed25519PrivateKey.generate() for m in (*members,*sites)}
def sign(m,d):return keys[m].sign(bytes.fromhex(d)).hex()
def verify(m,d,s):
    try:keys[m].public_key().verify(bytes.fromhex(s),bytes.fromhex(d));return True
    except (InvalidSignature,ValueError,KeyError):return False
(out/'public_keys.json').write_text(json.dumps({m:k.public_key().public_bytes(
    serialization.Encoding.Raw,serialization.PublicFormat.Raw).hex() for m,k in keys.items()},indent=2))
values=[int(row['publicSignals'][4]) for row in rows]
total_value=sum(values)
public=list(map(int,rows[0]['publicSignals']));domain=_register_domain(
    h,public=public,threshold=3,cap=total_value,initial_funding=0)
assert public[5]==root_statement.credential_root and public[6]==root_statement.entitlement_root
context=(h.web3.eth.chain_id,h.gateway.address,h.settlement.address)
def tx(fn):
    r=h.web3.eth.wait_for_transaction_receipt(fn.transact({'from':h.owner,'gas':8000000}))
    assert r.status==1
tx(h.settlement.functions.fundCampaign(domain,total_value));tx(g.enableRecovery(domain,1))
block_budget=int(h.web3.eth.get_block('latest').gasLimit)
(out/'protocol.json').write_text(json.dumps(dict(vouchers=100,K=4,q=3,f=1,
    blockGasLimit=block_budget,requestedBlockGasLimit=gas_limit,batchSize=chunk_size,
    atomicBatch=(chunk_size==100),rpc=rpc_url,
    singleHost=True,actuator='synthetic fsync journal',proofs='fresh admitted fixtures'),indent=2))
def check_voucher(statement,voucher):
    p=voucher['public'];s=statement['slot']
    return (proof_domain(p)==domain and p[7]==int(s['nullifier']) and p[4]==statement['value']
        and p[1]==s['epoch'] and p[8]==1 and s['domain_id']==domain.hex()
        and p[11:14]==[context[0],int(context[1],16),int(context[2],16)]
        and h.verifier.functions.verifyProof(voucher['a'],voucher['b'],voucher['c'],p).call())
packets=[]
for i,row in enumerate(rows):
    directory=out/f'transfer-{i:03d}';directory.mkdir()
    right=Web3.keccak(text=f'integrated-right-{i}')
    value=int(row['publicSignals'][4])
    tx(g.issueRight(domain,right,sites[0],value))
    def site(name):return RecoveredReleaseSite(directory/f'{name}.sqlite',site=name,
        domain=domain.hex(),epoch=1,initial=value*int(name==sites[0]),sites=sites,
        witnesses=members,faults=1,sign=sign,verify=verify)
    a,b=site(sites[0]),site(sites[1]);payload=a.begin(right.hex(),sites[1],value)
    transfer=json.loads(payload);rules=b.recovery_rules(payload)
    replicas=[DurableTerminalReplica(directory/f'{m}.sqlite',rules,m,sign) for m in members]
    reports=[r.change_view(1) for r in replicas]
    decision='accept' if i%2==0 else 'reclaim'
    proposal=replicas[1].propose(1,decision,reports)
    prepared=[r.prepare(proposal) for r in replicas];commits=[r.commit(prepared) for r in replicas]
    certificates=[attest_terminal(r,transfer,commits,context=context,sign_evm=h.sign) for r in replicas]
    cert=certificates[0][0];assert all(c==cert for c,_ in certificates)
    cert[8]=h.sign(sites[0],transfer_hash(domain,cert,context))
    cert[9]=[s for _,s in certificates[:3]]
    assert transfer_hash(domain,cert,context)==g.recoveryTransferHash(domain,cert).call()
    a.finalize(payload,commits);b.finalize(payload,commits)
    target=b if decision=='accept' else a
    p=row['proof'];voucher=dict(a=list(map(int,p['pi_a'][:2])),
        b=[[int(p['pi_b'][j][1]),int(p['pi_b'][j][0])] for j in range(2)],
        c=list(map(int,p['pi_c'][:2])),public=list(map(int,row['publicSignals'])))
    slot=ReleaseSlotKey(domain.hex(),1,str(voucher['public'][7]),target.site)
    def actuator(release,value):
        with (out/'actuator.jsonl').open('a') as f:
            f.write(canonical(dict(release=release,value=value))+'\n');f.flush();os.fsync(f.fileno())
        return digest(dict(release=release,value=value,device='synthetic'))
    result=target.release(slot,value=value,mode='fallback',authorization=voucher,
        verify_authorization=check_voucher,actuator=actuator)
    assert result['invoked'] and target.balance()==0
    receipt=attest_closed_receipt(result['receipt'],voucher,cert,release_site=target,context=context,verify_ed=verify,sign_evm=h.sign)
    assert receipt[4]==g.recoveryAuthorizationHash(voucher['public'],receipt,cert).call()
    item=[voucher[k] for k in ('a','b','c','public')]+[receipt,cert]
    packets.append(dict(item=item,transfer=transfer,commits=commits,closed_receipt=result['receipt']))
    a.close();b.close()
    for r in replicas:r.close()
    if (i+1)%10==0:print(f'{i+1}/100 durable recovery/releases attested',flush=True)
def wire(v):
    if isinstance(v,bytes):return '0x'+v.hex()
    if isinstance(v,(list,tuple)):return [wire(x) for x in v]
    if isinstance(v,dict):return {k:wire(x) for k,x in v.items()}
    return v
sent=out/'packets.json';sent.write_text(json.dumps(wire(packets),sort_keys=True,separators=(',',':')))
def linux(p):return '/mnt/'+p.drive[0].lower()+str(p)[2:].replace('\\','/')
subprocess.run(['wsl','--exec','bash',linux(ROOT/'testbed/run-signed-evidence.sh'),
    linux(ROOT/'tmp/dtn7rs-v0.21.0/dtn7-0.21.0/bin'),linux(sent),linux(out/'bpv7')],check=True,timeout=90)
received=(out/'bpv7/received.json').read_bytes();assert received==sent.read_bytes()
items=[p['item'] for p in json.loads(received)]
compact_records=[('0x'+domain.hex(),item[5][0],item[5][1],
    int(item[3][7]).to_bytes(32,'big'),item[4][0],item[4][1],item[3][4],
    item[4][4],item[5][5],item[5][6],item[5][7])
    for item in items]
eip7825_cap=2**24; oversized_rejection=None; full_at_cap_status=None
if rpc_url:
    try:
        h.web3.eth.send_transaction({'from':h.owner,'to':h.owner,'value':0,
            'gas':eip7825_cap+1})
    except Exception as error:
        oversized_rejection=str(error)
    else: raise AssertionError('EIP-7825 oversized transaction accepted')
receipts=[]; finalize_receipt=None; incomplete_finalize_status=None
if settlement_profile=='staged-atomic':
    batch_id=Web3.keccak(b'aidmesh/staged-atomic/v1'+received)
    tx(g.openRecoveryBatch(batch_id,domain,100,total_value,
        int(h.web3.eth.block_number)+1000))
    if rpc_url:
        subprocess.run([sys.executable,str(ROOT/'scripts/audit-v3-attack-matrix-20260910.py'),
            str(source),str(out),str(out/'attack_matrix.json'),rpc_url,str(verifier_source),'live'],
            cwd=ROOT,check=True,timeout=300)
    for offset in range(0,len(items),chunk_size):
        r=h.web3.eth.wait_for_transaction_receipt(g.stageRecoveryBatch(
            batch_id,items[offset:offset+chunk_size]).transact({'from':h.relayer,
            'gas':min(block_budget-10000,eip7825_cap)}))
        assert r.status==1
        receipts.append(dict(offset=offset,count=len(items[offset:offset+chunk_size]),
            gasUsed=int(r.gasUsed),status=int(r.status),transactionHash=r.transactionHash.hex()))
        assert all(h.settlement.functions.credits(domain,s).call()==0 for s in sites)
        if offset==0:
            incomplete=h.web3.eth.wait_for_transaction_receipt(g.finalizeRecoveryBatch(
                batch_id,compact_records).transact(
                {'from':h.relayer,'gas':min(block_budget-10000,eip7825_cap)}))
            incomplete_finalize_status=int(incomplete.status)
            assert incomplete.status==0
    finalize_receipt=h.web3.eth.wait_for_transaction_receipt(g.finalizeRecoveryBatch(
        batch_id,compact_records).transact(
        {'from':h.relayer,'gas':min(block_budget-10000,eip7825_cap)}))
    assert finalize_receipt.status==1
else:
    if rpc_url:
        try:
            r=h.web3.eth.wait_for_transaction_receipt(g.verifyRecoveryBatchAndSettle(items).transact(
                {'from':h.relayer,'gas':eip7825_cap}))
            full_at_cap_status=int(r.status)
        except Exception:
            full_at_cap_status='rejected_pre_execution'
        assert all(h.settlement.functions.credits(domain,s).call()==0 for s in sites)
    for offset in range(0,len(items),chunk_size):
        r=h.web3.eth.wait_for_transaction_receipt(g.verifyRecoveryBatchAndSettle(
            items[offset:offset+chunk_size]).transact({'from':h.relayer,
            'gas':min(block_budget-10000,eip7825_cap)}))
        assert r.status==1
        receipts.append(dict(offset=offset,count=len(items[offset:offset+chunk_size]),
            gasUsed=int(r.gasUsed),status=int(r.status),transactionHash=r.transactionHash.hex()))
credits=[h.settlement.functions.credits(domain,s).call() for s in sites]
expected=[sum(values[i] for i in range(len(values)) if (i%2==0)==(j==1)) for j in range(2)]
summary=dict(profile=settlement_profile,batchStatus=int(all(r['status']==1 for r in receipts)),
    gasUsed=sum(r['gasUsed'] for r in receipts)+(int(finalize_receipt.gasUsed) if finalize_receipt else 0),
    maxTransactionGas=max([r['gasUsed'] for r in receipts]+([int(finalize_receipt.gasUsed)] if finalize_receipt else [])),
    chunks=receipts,finalizeGasUsed=int(finalize_receipt.gasUsed) if finalize_receipt else None,
    incompleteFinalizeStatus=incomplete_finalize_status,blockGasLimit=block_budget,
    credits=credits,uniqueVouchers=100,actuatorInvocations=len((out/'actuator.jsonl').read_text().splitlines()),
    bpv7Unchanged=True,committee=dict(K=4,q=3,f=1),
    rootEndorsementVerifiedBeforeRegistration=True,
    gethClient=h.web3.client_version if rpc_url else None,eip7825Cap=eip7825_cap,
    oversizedTransactionRejected=oversized_rejection is not None,fullBatchAtCapStatus=full_at_cap_status,
    scope='single host; fresh Coconut/P1; durable Ed25519 recovery plus participant EVM attestations; synthetic actuator')
if all(r['status']==1 for r in receipts):
    assert credits==expected
    before=list(credits)
    replay_call=(g.finalizeRecoveryBatch(batch_id,compact_records) if settlement_profile=='staged-atomic'
        else g.verifyRecoveryBatchAndSettle(items[:chunk_size]))
    replay=h.web3.eth.wait_for_transaction_receipt(replay_call.transact(
        {'from':h.relayer,'gas':min(8000000,eip7825_cap)}))
    assert replay.status==0
    assert [h.settlement.functions.credits(domain,s).call() for s in sites]==before
    summary['replayStatus']=int(replay.status)
else:
    assert credits==[0,0]
    summary['batchCriterionMet']=False
(out/'summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary))
