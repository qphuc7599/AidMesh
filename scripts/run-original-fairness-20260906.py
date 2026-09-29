"""Locked original-pillar audit with exact certificates and independent LP probes."""
import itertools
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
import pandas as pd
from scipy.optimize import linprog
from aidmesh.allocator import allocation_baselines, compute_allocation_metrics
from aidmesh.allocator import progressive_fill, round_allocation
from aidmesh.allocator.certificate import certify_max_min, certify_rounding
from aidmesh.experiments.allocation import generate_problem

out = ROOT / 'results/original_pillars_20260906/fairness'
out.mkdir(parents=True, exist_ok=True)
protocol = dict(seeds=list(range(96201,96211)), groups=[5,20], categories=[2,5],
                skew=[0.2,1,5], supply=[0.35,0.6,0.9],
                scope='synthetic corrective audit; skew uses existing heavy-tail generator, not Dirichlet',
                primary='minimum normalized service', tuning='none')
(out/'protocol.json').write_text(json.dumps(protocol,indent=2))
rows=[]; probes=0
for groups,categories,skew,supply,seed in itertools.product(
    protocol['groups'],protocol['categories'],protocol['skew'],protocol['supply'],protocol['seeds']):
    p=generate_problem(seed,groups=groups,categories=categories,demand_skew=skew,
                       stock_sufficiency=supply)
    fair=progressive_fill(p); certify_max_min(p,fair)
    integer=round_allocation(fair,seed=f'original:{seed}')
    certify_rounding(p,fair,integer)
    # For each group, independently maximize its ratio while protecting all
    # groups whose current ratio is no larger. Any strict gain refutes max-min.
    a=np.array([[float(fair.coefficients[g][k]) for g in fair.groups] for k in fair.categories])
    upper=[min(float(p.demand[g][k])/float(fair.coefficients[g][k])
               for k in fair.categories if fair.coefficients[g][k]>0) for g in fair.groups]
    for i,g in enumerate(fair.groups):
        c=np.zeros(groups);c[i]=-1
        bounds=[(float(fair.ratios[h]) if fair.ratios[h]<=fair.ratios[g] else 0,upper[j])
                for j,h in enumerate(fair.groups)]
        solved=linprog(c,A_ub=a,b_ub=[float(p.stock[k]) for k in fair.categories],
                       bounds=bounds,method='highs')
        assert solved.success and -solved.fun <= float(fair.ratios[g])+1e-8
        probes+=1
    methods=dict(allocation_baselines(p));methods['progressive-fill']=fair
    methods['progressive-fill-integer']=integer
    for name,allocation in methods.items():
        m=compute_allocation_metrics(allocation)
        rows.append(dict(seed=seed,groups=groups,categories=categories,skew=skew,supply=supply,
                         method=name,minimum=float(m.minimum_service_ratio),
                         gini=float(m.gini_service_ratio),fulfilled=float(m.equivalent_fulfilled)))
data=pd.DataFrame(rows);data.to_csv(out/'runs.csv',index=False)
data.groupby('method')[['minimum','gini','fulfilled']].mean().to_csv(out/'means.csv')
(out/'verification.json').write_text(json.dumps(dict(instances=len(rows)//5,
    exact_fairness_certificates=len(rows)//5,exact_rounding_certificates=len(rows)//5,
    independent_lp_probes=probes,lp_tolerance=1e-8,all_passed=True),indent=2))
print(data.groupby('method')[['minimum','gini','fulfilled']].mean().to_string())
print('LP probes:',probes)
