"""Evaluate an explicit within-window linking attack on actual received calldata.

Ground truth is private evaluator data from the proof manifest. The attacker uses
only a public fallback tag, not admission IDs. This is not an unlinkability proof.
"""
import json
import sys
from pathlib import Path

proofs=json.loads(Path(sys.argv[1]).read_text())
packets=json.loads(Path(sys.argv[2]).read_text())
out=Path(sys.argv[3]);out.mkdir(parents=True,exist_ok=False)
truth={str(p['publicSignals'][7]):p['admission'] for p in proofs}
rows=[]
for packet in packets:
    p=packet['item'][3]
    rows.append(dict(nullifier=str(p[7]),fallback_mode=p[8],window=p[9],tag=str(p[10]),
                     admission=truth[str(p[7])]))
assert len(rows)==len(truth)==100
confusion=dict(tp=0,fp=0,tn=0,fn=0)
for i,a in enumerate(rows):
    for b in rows[i+1:]:
        same=a['admission']==b['admission']
        predicted=(a['fallback_mode']==b['fallback_mode']==1 and a['window']==b['window']
                   and a['tag']==b['tag'])
        confusion[('t' if predicted==same else 'f')+('p' if predicted else 'n')]+=1
tp,fp,tn,fn=(confusion[k] for k in ('tp','fp','tn','fn'))
result=dict(observer='relay or gateway with plaintext calldata',attack='equal public fallbackTag within window',
    vouchers=100,beneficiaries=len(set(truth.values())),pairs=tp+fp+tn+fn,confusion=confusion,
    precision=tp/(tp+fp),recall=tp/(tp+fn),balanced_accuracy=(tp/(tp+fn)+tn/(tn+fp))/2,
    scope='one campaign/window, four synthetic holders; no cross-window or population-level inference claim',
    privacy_claim_established=False,
    interpretation='within-window fallback linkability is observable and must remain explicit leakage')
(out/'summary.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
