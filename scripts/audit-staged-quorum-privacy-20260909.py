"""Quorum-path public-transcript leakage audit for v3 staged admission.

The evaluation conditions on identical public leakage as required by the game.
Pair rows are descriptive and are not treated as independent replications.
"""
import hashlib
import itertools
import json
import sys
from collections import defaultdict
from pathlib import Path

directory=Path(sys.argv[1]);proofs=json.loads((directory/'fresh_proofs.json').read_text())
if not proofs or any(int(r['mode'])!=0 for r in proofs): raise ValueError('quorum-only proofs required')
def leakage(row):
    p=list(map(int,row['publicSignals']))
    # Nullifier is pseudorandom per holder/index; it is observed but never equal
    # for two valid fresh presentations. Public roots/context remain included.
    return tuple(p[:7]+p[8:])
cohorts=defaultdict(list)
for row in proofs: cohorts[leakage(row)].append(row)
pair_rows=[]
for cohort,rows in cohorts.items():
    for left,right in itertools.combinations(rows,2):
        truth=left['admission']==right['admission']
        p1=list(map(int,left['publicSignals']));p2=list(map(int,right['publicSignals']))
        pair_rows.append(dict(truth=truth,nullifier_equality=p1[7]==p2[7],
            fallback_tag_equality=p1[10]==p2[10]))
def confusion(feature):
    tp=sum(r['truth'] and r[feature] for r in pair_rows);fn=sum(r['truth'] and not r[feature] for r in pair_rows)
    fp=sum(not r['truth'] and r[feature] for r in pair_rows);tn=sum(not r['truth'] and not r[feature] for r in pair_rows)
    tpr=tp/(tp+fn);tnr=tn/(tn+fp)
    return dict(tp=tp,fp=fp,tn=tn,fn=fn,balanced_accuracy=(tpr+tnr)/2)
summary=dict(profile='v3 staged admission, quorum path',vouchers=len(proofs),holders=len({r['admission'] for r in proofs}),
    leakage_cohorts=len(cohorts),cohorts=[dict(vouchers=len(rows),holders=len({r['admission'] for r in rows}),
        maximum_uniform_holder_guess=1/len({r['admission'] for r in rows})) for rows in cohorts.values()],
    conditioned_pairs=len(pair_rows),positive_pairs=sum(r['truth'] for r in pair_rows),
    attacks={'nullifier_equality':confusion('nullifier_equality'),
             'fallback_tag_equality':confusion('fallback_tag_equality')},
    public_signal_order='17-signal P1 v3; conditioning excludes nullifier but retains all declared leakage',
    raw_proofs_sha256=hashlib.sha256((directory/'fresh_proofs.json').read_bytes()).hexdigest(),
    scope='synthetic cryptographic transcripts; no timing, route, issuer/site collusion or physical side channels; pairs dependent')
(directory/'quorum_privacy_summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary,indent=2))
