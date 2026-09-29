"""Prospective issued-to-delivered group-service experiment with common randomness."""
import argparse
import hashlib
import itertools
import json
import math
import platform
import sys
from fractions import Fraction as F
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from aidmesh.allocator import normalize_problem, progressive_fill
from aidmesh.allocator.full_box import lexicographic_full_box
from aidmesh.experiments.allocation import generate_problem

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def uniform(*parts):return int.from_bytes(hashlib.sha256('|'.join(map(str,parts)).encode()).digest()[:8],'big')/2**64
def integerize(p,amounts):
    result={g:{k:int(amounts[g][k]//p.units[k]) for k in p.categories} for g in p.groups}
    for k in p.categories:
        capacity=int(p.stock[k]//p.units[k]);left=capacity-sum(result[g][k] for g in p.groups)
        order=sorted(p.groups,key=lambda g:(-(amounts[g][k]/p.units[k]-result[g][k]),g))
        for g in order:
            if left<=0:break
            if (result[g][k]+1)*p.units[k]<=p.demand[g][k]:result[g][k]+=1;left-=1
    return result
def minimum(p,state):
    return min(sum(p.conversion_factors[k]*state[g][k]*p.units[k] for k in p.categories)/
        (p.weights[g]*p.aggregate_demand[g]) for g in p.groups)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('phase',choices=['freeze','run','summarize']);ap.add_argument('directory',type=Path)
    a=ap.parse_args();out=a.directory;sources={str(p.relative_to(ROOT)):sha(p) for p in
        [Path(__file__).resolve(),ROOT/'aidmesh/allocator/full_box.py',ROOT/'aidmesh/experiments/allocation.py']}
    if a.phase=='freeze':
        out.mkdir(parents=True,exist_ok=False)
        spec=dict(profile='coupled-delivered-fairness-v1',seeds=list(range(99801,99831)),
            partitions=[0,6,24,72],groups=6,categories=3,demand_skew=1,stock_sufficiency=.6,
            base_reachability=['1/4','2/5','11/20','7/10','17/20','1'],
            partition_multiplier={'0':'1','6':'9/10','24':'7/10','72':'2/5'},
            ambiguous_probability='1/25',settlement_given_delivered='19/20',
            methods=['progressive-fill','full-box-leximin','delivery-aware-leximin'],
            endpoint=['issued','delivered','ambiguous','settled','worst_group_normalized_service'],
            scope='synthetic semantic service coupling; no claim of a measured radio channel',source_sha256=sources)
        (out/'protocol.json').write_text(json.dumps(spec,indent=2));return
    spec=json.loads((out/'protocol.json').read_text())
    if spec['source_sha256']!=sources:raise ValueError('source drift')
    raw=out/'runs.jsonl';done=set()
    if raw.exists():done={json.loads(line)['key'] for line in raw.read_text().splitlines()}
    if a.phase=='run':
        with raw.open('a') as f:
            for seed,partition in itertools.product(spec['seeds'],spec['partitions']):
                key=f'{seed}/{partition}'
                if key in done:continue
                p=normalize_problem(generate_problem(seed,groups=spec['groups'],categories=spec['categories'],
                    demand_skew=spec['demand_skew'],stock_sufficiency=spec['stock_sufficiency']))
                reach={g:F(spec['base_reachability'][i])*F(spec['partition_multiplier'][str(partition)])
                       for i,g in enumerate(p.groups)}
                allocations={'progressive-fill':progressive_fill(p).amounts,
                    'full-box-leximin':lexicographic_full_box(p).amounts,
                    'delivery-aware-leximin':lexicographic_full_box(p,service_factors=reach).amounts}
                rows=[]
                for method,amounts in allocations.items():
                    issued=integerize(p,amounts);delivered={g:{k:0 for k in p.categories} for g in p.groups}
                    ambiguous={g:{k:0 for k in p.categories} for g in p.groups};settled={g:{k:0 for k in p.categories} for g in p.groups}
                    for g in p.groups:
                        for k in p.categories:
                            for index in range(issued[g][k]):
                                u=uniform(seed,partition,g,k,index,'delivery')
                                if u<float(reach[g]):
                                    delivered[g][k]+=1
                                    if uniform(seed,partition,g,k,index,'settlement')<F(spec['settlement_given_delivered']):settled[g][k]+=1
                                elif u<float(reach[g]+F(spec['ambiguous_probability'])):ambiguous[g][k]+=1
                    rows.append(dict(method=method,issued=sum(map(sum,(v.values() for v in issued.values()))),
                        delivered=sum(map(sum,(v.values() for v in delivered.values()))),
                        ambiguous=sum(map(sum,(v.values() for v in ambiguous.values()))),
                        settled=sum(map(sum,(v.values() for v in settled.values()))),
                        issued_min=str(minimum(p,issued)),delivered_min=str(minimum(p,delivered)),
                        settled_min=str(minimum(p,settled)),group_issued=issued,group_delivered=delivered,
                        group_ambiguous=ambiguous,group_settled=settled))
                f.write(json.dumps(dict(key=key,seed=seed,partition=partition,rows=rows))+'\n');f.flush()
        return
    records=[json.loads(line) for line in raw.read_text().splitlines()];expected=len(spec['seeds'])*len(spec['partitions'])
    if len(records)!=expected or len({r['key'] for r in records})!=expected:raise ValueError('incomplete')
    flat=[dict(seed=r['seed'],partition=r['partition'],**row) for r in records for row in r['rows']]
    means={m:{e:sum(float(F(str(r[e]))) for r in flat if r['method']==m)/expected
              for e in ('issued','delivered','ambiguous','settled','issued_min','delivered_min','settled_min')}
           for m in spec['methods']}
    (out/'summary.json').write_text(json.dumps(dict(instances=expected,rows=len(flat),means=means,
        raw_sha256=sha(raw),platform=platform.platform(),scope=spec['scope']),indent=2))
    print(json.dumps(means,indent=2))
if __name__=='__main__':main()
