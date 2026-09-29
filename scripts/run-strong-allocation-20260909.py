"""Prospectively specified strong-comparator allocation study.

This is a post-review development campaign. It never reads or alters the original
holdout. Exact rational simplex is used for both full-box comparators.
"""
import argparse
import hashlib
import itertools
import json
import platform
import sys
import time
from fractions import Fraction as F
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from aidmesh.allocator import allocation_baselines, compute_allocation_metrics, normalize_problem, progressive_fill
from aidmesh.allocator.completion import complete_fair_floor
from aidmesh.allocator.full_box import lexicographic_full_box
from aidmesh.experiments.allocation import generate_problem

def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def utility(p,amounts):
    p=normalize_problem(p)
    return sum((p.weights[g]*p.conversion_factors[k]*amounts[g][k]
                for g in p.groups for k in p.categories),F(0))

def main():
    ap=argparse.ArgumentParser();ap.add_argument('phase',choices=['freeze','run','summarize'])
    ap.add_argument('directory',type=Path);args=ap.parse_args();out=args.directory
    sources={str(p.relative_to(ROOT)):digest(p) for p in [Path(__file__).resolve(),
        ROOT/'aidmesh/allocator/full_box.py',ROOT/'aidmesh/allocator/completion.py',
        ROOT/'aidmesh/allocator/progressive_fill.py',ROOT/'aidmesh/allocator/baselines.py',
        ROOT/'aidmesh/experiments/allocation.py']}
    if args.phase=='freeze':
        out.mkdir(parents=True,exist_ok=False)
        spec=dict(profile='post-review-strong-allocation-v1',seeds=list(range(99701,99731)),
            groups=12,categories=3,skew=[0.2,1,5],supply=[0.35,0.6,0.9],
            methods=['progressive-fill','fair-floor-completion','demand-proportional',
                     'utility-only','full-box-leximin-then-weighted-utility',
                     'weighted-utility-optimum-then-leximin'],
            primary=['minimum_normalized_service','weighted_utility'],
            estimand='paired method contrasts within exact same generated problem',
            tie_break='canonical group/category order; exact SymPy simplex',
            scope='post-review prospective synthetic suite; not the original H3 holdout',
            source_sha256=sources)
        (out/'protocol.json').write_text(json.dumps(spec,indent=2))
        return
    spec=json.loads((out/'protocol.json').read_text())
    if spec['source_sha256']!=sources: raise ValueError('source drift; freeze a new campaign')
    raw=out/'runs.jsonl';done=set()
    if raw.exists(): done={json.loads(line)['key'] for line in raw.read_text().splitlines()}
    if args.phase=='run':
        with raw.open('a',encoding='utf-8') as stream:
            for seed,skew,supply in itertools.product(spec['seeds'],spec['skew'],spec['supply']):
                key=f'{seed}/{skew}/{supply}'
                if key in done: continue
                started=time.perf_counter();p=normalize_problem(generate_problem(seed,groups=spec['groups'],
                    categories=spec['categories'],demand_skew=skew,stock_sufficiency=supply))
                base=allocation_baselines(p);pf=progressive_fill(p);fec=complete_fair_floor(p,pf)
                full=lexicographic_full_box(p);fair_utility=lexicographic_full_box(p,utility_first=True)
                methods={'progressive-fill':pf,'fair-floor-completion':fec,
                    'demand-proportional':base['demand-proportional'],'utility-only':base['utility-only'],
                    'full-box-leximin-then-weighted-utility':full,
                    'weighted-utility-optimum-then-leximin':fair_utility}
                rows=[]
                for name,a in methods.items():
                    amounts=a.amounts;m=compute_allocation_metrics(a) if hasattr(a,'assert_feasible') else None
                    ratios=(a.ratios if hasattr(a,'ratios') else {
                        g:sum(p.conversion_factors[k]*amounts[g][k] for k in p.categories)/
                          (p.weights[g]*p.aggregate_demand[g]) for g in p.groups})
                    rows.append(dict(method=name,minimum=str(min(ratios.values())),
                        weighted_utility=str(utility(p,amounts)),
                        fulfillment=str(sum(amounts[g][k] for g in p.groups for k in p.categories)),
                        exact_lp_certificates=len(getattr(a,'certificates',[]))))
                record=dict(key=key,seed=seed,skew=skew,supply=supply,
                    seconds=time.perf_counter()-started,rows=rows)
                stream.write(json.dumps(record)+'\n');stream.flush()
        return
    records=[json.loads(line) for line in raw.read_text().splitlines()]
    expected=len(spec['seeds'])*len(spec['skew'])*len(spec['supply'])
    if len(records)!=expected or len({r['key'] for r in records})!=expected:
        raise ValueError(f'incomplete: {len(records)}/{expected}')
    methods=spec['methods'];flat=[{**{k:r[k] for k in ('key','seed','skew','supply')},**row}
        for r in records for row in r['rows']]
    # Preserve raw exact fractions; summary decimals are descriptive only.
    means={m:{e:sum(float(F(row[e])) for row in flat if row['method']==m)/expected
              for e in ('minimum','weighted_utility','fulfillment')} for m in methods}
    summary=dict(instances=expected,rows=len(flat),methods=methods,means=means,
        counterexample_checked_by_unit_test=True,raw_sha256=digest(raw),
        runtime_seconds=sum(r['seconds'] for r in records),environment=dict(
            python=sys.version,platform=platform.platform()),scope=spec['scope'])
    (out/'summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary,indent=2))

if __name__=='__main__':main()
