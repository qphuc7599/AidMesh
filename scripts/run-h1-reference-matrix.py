"""Original-factor reference safety/availability audit; NOT Coconut baseline evidence.

The existing reference simulator uses model crypto and stochastic settlement.
Retain this label: it cannot establish the full original H1 end-to-end claim.
"""
import json
import hashlib
import sys
from copy import deepcopy
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import pandas as pd
from aidmesh.config import load_scenario
from aidmesh.simulator.experiment import run_experiment

out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=False)
base=load_scenario(ROOT/'configs/confirmatory/core_w08_p24.yaml')
protocol=dict(seeds=list(range(99601,99631)),partitions=[0,1,6,24,72],attacks=[0,0.01,0.05,0.15],
    methods=['online_only','first_seen_dtn','aidmesh_quorum_only','aidmesh_static_rights'],
    scope='reference model audit, no Coconut/real proof/matched full transport baseline claim',
    simulator_sha256=hashlib.sha256((ROOT/'aidmesh/simulator/experiment.py').read_bytes()).hexdigest())
(out/'protocol.json').write_text(json.dumps(protocol,indent=2))
rows=[]
for seed in protocol['seeds']:
    for partition in protocol['partitions']:
        for attack in protocol['attacks']:
            config=deepcopy(base);config['seed']=seed
            config['analysis_horizon_us']=168*3600000000
            config['network']['partition_us']=partition*3600000000
            config['adversary']['beneficiary_fraction']=attack
            for method in protocol['methods']:
                result=run_experiment(config,method)
                row=result.to_dict() if hasattr(result,'to_dict') else __import__('dataclasses').asdict(result)
                rows.append(dict(row,partition=partition,attack=attack))
        pd.DataFrame(rows).to_csv(out/'runs.csv',index=False)
    print(f'seed {seed} complete',flush=True)
frame=pd.DataFrame(rows)
frame.groupby(['partition','attack','method'])[['completion_rate','duplicate_exposure',
    'exposure_cap_ratio','settled_receipts']].mean().to_csv(out/'means.csv')
aid=frame[frame['method'].str.startswith('aidmesh')]
summary=dict(runs=len(frame),paired_seeds=30,aidmesh_cap_violations=int((aid['exposure_cap_ratio']>1).sum()),
    conflicting_safe_quorum_certificates=int(aid['conflicting_quorum_certificates'].sum()),
    original_H1_established=False,scope=protocol['scope'])
(out/'summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary))
