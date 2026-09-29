import itertools
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import pandas as pd
from aidmesh.allocator import progressive_fill, compute_allocation_metrics, round_allocation
from aidmesh.allocator.completion import complete_fair_floor, complete_integer_fair_floor
from aidmesh.allocator.certificate import certify_rounding
from aidmesh.experiments.allocation import generate_problem

out=ROOT/'results/original_pillars_20260906/completion_v2'
out.mkdir(parents=True,exist_ok=True)
registry=json.loads((out.parent/'fairness/protocol.json').read_text())
registry['scope']='post-audit extension, same instances; not confirmatory original H3'
(out/'protocol.json').write_text(json.dumps(registry,indent=2))
rows=[]
for groups,categories,skew,supply,seed in itertools.product(
    registry['groups'],registry['categories'],registry['skew'],registry['supply'],registry['seeds']):
    p=generate_problem(seed,groups=groups,categories=categories,demand_skew=skew,
                       stock_sufficiency=supply)
    floor=progressive_fill(p);completed=complete_fair_floor(p,floor)
    rounded_floor=round_allocation(floor,seed=f'original:{seed}')
    integer=complete_integer_fair_floor(p,floor,rounded_floor)
    certify_rounding(p,floor,integer)
    assert all(integer.amounts[g][k]>=rounded_floor.amounts[g][k]
               for g in p.demand for k in p.stock)
    m=compute_allocation_metrics(completed);base=compute_allocation_metrics(floor)
    assert m.minimum_service_ratio >= base.minimum_service_ratio
    optimum=sum(min(p.stock[k],sum(p.demand[g][k] for g in p.demand)) for k in p.stock)
    assert m.equivalent_fulfilled==optimum
    for name,a in [('fair-floor-completion',completed),('completed-integer',integer)]:
        v=compute_allocation_metrics(a)
        rows.append(dict(seed=seed,groups=groups,categories=categories,skew=skew,supply=supply,
            method=name,minimum=float(v.minimum_service_ratio),gini=float(v.gini_service_ratio),
            fulfilled=float(v.equivalent_fulfilled),utility_ratio=float(v.equivalent_fulfilled/optimum),
            floor_minimum=float(base.minimum_service_ratio)))
d=pd.DataFrame(rows);d.to_csv(out/'runs.csv',index=False)
print(d.groupby('method')[['minimum','gini','utility_ratio']].mean().to_string())
