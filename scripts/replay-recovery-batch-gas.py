"""Reconcile retained signed batch on a fresh local chain with explicit gas limit.

Does not perform new issuance, recovery or physical release. Reuses byte-identical
signed evidence to isolate the capacity constraint from the failed default run.
"""
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from aidmesh.experiments.semantic_e2e_campaign import _Harness,_register_domain

received=Path(sys.argv[1]);out=Path(sys.argv[2]);budget=int(sys.argv[3])
out.mkdir(parents=True,exist_ok=False)
items=[p['item'] for p in json.loads(received.read_text())]
h=_Harness(ROOT,block_gas_limit=budget);h.witnesses=(*h.accounts[1:4],h.accounts[9])
domain=_register_domain(h,public=items[0][3],threshold=3,cap=100);g=h.gateway.functions
def tx(fn,gas=8000000):
    return h.web3.eth.wait_for_transaction_receipt(fn.transact({'from':h.owner,'gas':gas}))
assert tx(g.enableRecovery(domain,1)).status==1
assert tx(h.settlement.functions.fundCampaign(domain,100)).status==1
for item in items:
    cert=item[5]
    assert tx(g.issueRight(domain,cert[0],cert[2],item[3][4])).status==1
fn=g.verifyRecoveryBatchAndSettle(items)
r=tx(fn,budget-10000)
credits=[h.settlement.functions.credits(domain,s).call() for s in h.sites[:2]]
summary=dict(batchStatus=int(r.status),gasUsed=int(r.gasUsed),blockGasLimit=budget,
    credits=credits,vouchers=len(items),freshPhysicalReleases=0,
    scope='fresh local chain replay of identical source-signed evidence; changed capacity only')
if r.status==1:
    assert credits==[50,50]
    replay=tx(fn);assert replay.status==0
    summary['replayStatus']=int(replay.status)
(out/'summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary))
