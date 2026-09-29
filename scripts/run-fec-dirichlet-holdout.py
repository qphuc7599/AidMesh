"""Locally frozen prospective synthetic FEC extension, original H3 margin retained.

Two commands: freeze records source hashes and design without generating demand;
run refuses source drift or existing outcomes. Not an externally preregistered study
or the full original RQ3 suite (Gini/A-DREAM baselines remain separate obligations).
"""
import hashlib
import itertools
import json
import sys
from fractions import Fraction
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import numpy as np
import pandas as pd
from aidmesh.allocator import (PackingProblem,progressive_fill,round_allocation,
    allocation_baselines,compute_allocation_metrics)
from aidmesh.allocator.completion import complete_integer_fair_floor

phase=sys.argv[1];out=Path(sys.argv[2])
files=sorted((ROOT/'aidmesh/allocator').glob('*.py'))+[Path(__file__).resolve()]
hashes={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
if phase=='freeze':
    out.mkdir(parents=True,exist_ok=False)
    protocol=dict(profile='fec-dirichlet-extension-v1',seeds=list(range(98401,98431)),
        concentrations=[0.2,1,5],supply=[0.35,0.6,0.9],weights=['neutral','cyclic','priority'],
        groups=12,categories=3,category_demand=600,utility_margin=0.03,
        primary=['minimum service vs utility-only','minimum service vs demand-proportional',
                 'utility loss vs utility-only'],bootstrap=10000,bootstrap_seed=98399,
        alpha=0.05,primary_rule='simultaneous seed-bootstrap bounds: both service gains >0 and utility loss <=0.03',
        scope='locally frozen fresh synthetic FEC extension; original allocator reported separately',
        missing_original_baselines=['Gini-penalized','validated A-DREAM-style'],source_sha256=hashes)
    (out/'protocol.json').write_text(json.dumps(protocol,indent=2))
    print('Frozen before demand generation; no outcomes inspected.')
elif phase=='run':
    protocol=json.loads((out/'protocol.json').read_text())
    if protocol['source_sha256']!=hashes:raise RuntimeError('frozen source changed')
    if (out/'runs.csv').exists() or (out/'RUN_STARTED').exists():raise RuntimeError('run already started')
    (out/'RUN_STARTED').write_text('Do not overwrite; retain all failures.\n')
    rows=[]
    for seed,alpha,supply,weight in itertools.product(protocol['seeds'],protocol['concentrations'],
            protocol['supply'],protocol['weights']):
        rng=np.random.default_rng(seed)
        groups=[f'g{i:02d}' for i in range(protocol['groups'])];categories=['k0','k1','k2']
        # Fixed category totals; multinomial realizes the Dirichlet demand mix.
        columns=[rng.multinomial(protocol['category_demand'],rng.dirichlet([alpha]*len(groups)))
                 for _ in categories]
        demand={g:{k:int(columns[j][i]) for j,k in enumerate(categories)} for i,g in enumerate(groups)}
        directions={g:{k:(Fraction(demand[g][k],sum(demand[g].values())) if sum(demand[g].values())
                         else Fraction(1,len(categories))) for k in categories} for g in groups}
        weights={g:1 if weight=='neutral' else (1+i%3 if weight=='cyclic' else (3 if i<3 else 1))
                 for i,g in enumerate(groups)}
        p=PackingProblem('dirichlet',demand,{k:int(supply*protocol['category_demand']) for k in categories},
            weights,{k:1 for k in categories},directions,{k:1 for k in categories})
        fair=progressive_fill(p);integer=round_allocation(fair,seed=f'fec:{seed}')
        methods=dict(allocation_baselines(p));methods.update({'original-progressive':fair,
            'original-integer':integer,'fec-integer':complete_integer_fair_floor(p,fair,integer)})
        for name,allocation in methods.items():
            metrics=compute_allocation_metrics(allocation)
            rows.append(dict(seed=seed,alpha=alpha,supply=supply,weight=weight,method=name,
                minimum=float(metrics.minimum_service_ratio),utility=float(metrics.equivalent_fulfilled)))
        pd.DataFrame(rows).to_csv(out/'runs.csv',index=False)
    frame=pd.DataFrame(rows)
    seed_metrics=frame.groupby(['seed','method'])[['minimum','utility']].mean().unstack('method')
    contrasts=np.column_stack([
        seed_metrics['minimum']['fec-integer']-seed_metrics['minimum']['utility-only'],
        seed_metrics['minimum']['fec-integer']-seed_metrics['minimum']['demand-proportional'],
        1-seed_metrics['utility']['fec-integer']/seed_metrics['utility']['utility-only']])
    rng=np.random.default_rng(protocol['bootstrap_seed'])
    boot=contrasts[rng.integers(0,len(contrasts),size=(protocol['bootstrap'],len(contrasts)))].mean(axis=1)
    # Bonferroni percentile intervals across three predeclared endpoints.
    tail=protocol['alpha']/(2*contrasts.shape[1])
    low,high=np.quantile(boot,[tail,1-tail],axis=0)
    result=dict(instances=len(rows)//len(methods),paired_seeds=len(contrasts),
        means=contrasts.mean(axis=0).tolist(),simultaneous_low=low.tolist(),simultaneous_high=high.tolist(),
        fec_extension_supported=bool(low[0]>0 and low[1]>0 and high[2]<=protocol['utility_margin']),
        original_H3_confirmed=False,scope=protocol['scope'],
        inference='seed bootstrap; Bonferroni percentile intervals, approximate, not mixed-effects/Holm full original suite')
    (out/'summary.json').write_text(json.dumps(result,indent=2))
    frame.groupby('method')[['minimum','utility']].mean().to_csv(out/'means.csv')
    print(json.dumps(result))
else:raise ValueError('freeze or run required')
