"""Locally frozen H4-oriented matched synthetic routing study.

No DPDRM/CSAR equivalence claimed. Failures remain in the restricted-time endpoint;
the original median criterion is reported separately and may be unreached.
"""
import hashlib
import json
import sys
from dataclasses import asdict,replace
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np
import pandas as pd
from aidmesh.simulator.dtn import (HOUR_US,RoutingPolicy,TemporalContactSimulator,
    generate_receipt_bundles,generate_synthetic_contacts)

phase,directory=sys.argv[1:];out=Path(directory)
files=[Path(__file__).resolve(),ROOT/'aidmesh/simulator/dtn.py']
hashes={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
if phase=='freeze':
    out.mkdir(parents=True,exist_ok=False)
    (out/'protocol.json').write_text(json.dumps(dict(seeds=list(range(99501,99531)),
        partitions=[0,1,6,24,72],horizon=168,ttl=72,bundles=240,sites=8,relays=3,
        copies=4,buffer=10240,bytes_per_second=8,
        endpoint='fallback/conflict failure-inclusive median, capped mean at TTL secondary',
        scope='fresh synthetic H4-oriented stress study, not full original bandwidth registry',
        sha256=hashes),indent=2))
    print('H4 design frozen before generating contacts.')
elif phase=='run':
    p=json.loads((out/'protocol.json').read_text())
    if p['sha256']!=hashes or (out/'STARTED').exists():raise ValueError('source drift or prior run')
    (out/'STARTED').write_text('retain outcomes and failures')
    rows=[]
    for seed in p['seeds']:
        for partition in p['partitions']:
            contacts=generate_synthetic_contacts(seed=seed,sites=p['sites'],relays=p['relays'],
                horizon_us=p['horizon']*HOUR_US,partition_us=partition*HOUR_US,
                bytes_per_second=p['bytes_per_second'])
            contacts=tuple(c for c in contacts if 'contract' not in (c.node_a,c.node_b)
                           or c.start_us>=partition*HOUR_US)
            bundles=generate_receipt_bundles(seed=seed,count=p['bundles'],sites=p['sites'],
                horizon_us=48*HOUR_US,ttl_us=p['ttl']*HOUR_US)
            # Deterministic classes committed before outcomes; fixed equal payload sizes.
            classes={b.bundle_id:('fallback' if i%3==0 else 'conflict' if i%3==1 else 'normal')
                     for i,b in enumerate(bundles)}
            bundles=tuple(replace(b,risk_score=1.0 if classes[b.bundle_id]!='normal' else 0.1)
                          for b in bundles)
            trace_hash=hashlib.sha256(json.dumps([asdict(c) for c in contacts],sort_keys=True).encode()).hexdigest()
            for policy in (RoutingPolicy.SPRAY_WAIT,RoutingPolicy.RISK_PRIORITY):
                result=TemporalContactSimulator(contacts=contacts,bundles=bundles,policy=policy,
                    buffer_bytes=p['buffer'],copy_budget=p['copies']).run()
                delivered={d.bundle_id:d.delay_us/HOUR_US for d in result.deliveries}
                target=[b for b in bundles if classes[b.bundle_id]!='normal']
                times=[min(delivered.get(b.bundle_id,float('inf')),p['ttl']) for b in target]
                observed=sorted(delivered[b.bundle_id] for b in target
                                if b.bundle_id in delivered and delivered[b.bundle_id]<=p['ttl'])
                median=observed[(len(target)-1)//2] if len(observed)>=(len(target)+1)//2 else None
                rows.append(dict(seed=seed,partition=partition,policy=policy.value,
                    median_hours=median,median_reached=median is not None,
                    failure_inclusive_capped_mean=float(np.mean(times)),
                    delivered=len(observed),target_bundles=len(target),
                    forwarded_bytes=result.forwarded_bytes,contact_sha256=trace_hash,
                    shared_capacity=sum(c.capacity_bytes for c in contacts)))
            pd.DataFrame(rows).to_csv(out/'runs.csv',index=False)
    frame=pd.DataFrame(rows)
    frame.groupby(['partition','policy']).agg(capped_mean=('failure_inclusive_capped_mean','mean'),
        median_reached_fraction=('median_reached','mean'),delivery=('delivered','mean'),
        bytes=('forwarded_bytes','mean')).to_csv(out/'summary.csv')
    print(frame.groupby('policy')[['failure_inclusive_capped_mean','delivered']].mean().to_string())
else:raise ValueError('freeze or run')
