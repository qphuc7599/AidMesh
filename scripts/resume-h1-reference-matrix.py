"""Execution-only parallel resumption; preserve completed rows and frozen model."""
import hashlib
import itertools
import json
import sys
from concurrent.futures import ProcessPoolExecutor,as_completed
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import pandas as pd
from aidmesh.config import load_scenario
from aidmesh.simulator.experiment import run_experiment


def execute(job):
    seed,partition,attack,method=job
    config=deepcopy(load_scenario(ROOT/'configs/confirmatory/core_w08_p24.yaml'))
    config['seed']=seed;config['analysis_horizon_us']=168*3600000000
    config['network']['partition_us']=partition*3600000000
    config['adversary']['beneficiary_fraction']=attack
    result=run_experiment(config,method)
    row=result.to_dict() if hasattr(result,'to_dict') else asdict(result)
    return dict(row,partition=partition,attack=attack)


if __name__=='__main__':
    out=Path(sys.argv[1]);p=json.loads((out/'protocol.json').read_text())
    if p['simulator_sha256']!=hashlib.sha256((ROOT/'aidmesh/simulator/experiment.py').read_bytes()).hexdigest():
        raise ValueError('model changed')
    frame=pd.read_csv(out/'runs.csv');rows=frame.to_dict('records')
    keys=['seed','partition','attack','method']
    done={tuple(row[k] for k in keys) for row in rows}
    jobs=[j for j in itertools.product(p['seeds'],p['partitions'],p['attacks'],p['methods']) if j not in done]
    (out/'execution_resumption.json').write_text(json.dumps(dict(preserved=len(rows),remaining=len(jobs),
        reason='execution parallelization; same model/config/seeds; interrupted incomplete jobs rerun',
        workers=6,runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()),indent=2))
    with ProcessPoolExecutor(max_workers=6) as pool:
        futures={pool.submit(execute,j):j for j in jobs}
        for n,future in enumerate(as_completed(futures),1):
            try:rows.append(future.result())
            except BaseException as error:
                (out/'worker_failure.json').write_text(json.dumps(dict(job=futures[future],error=repr(error))))
                raise
            if n%20==0 or n==len(jobs):
                pd.DataFrame(rows).sort_values(keys).to_csv(out/'runs.csv',index=False)
            if n%100==0:print(f'{len(rows)}/2400 runs persisted',flush=True)
    frame=pd.DataFrame(rows)
    frame.groupby(['partition','attack','method'])[['completion_rate','duplicate_exposure',
        'exposure_cap_ratio','settled_receipts']].mean().to_csv(out/'means.csv')
    aid=frame[frame['method'].str.startswith('aidmesh')]
    summary=dict(runs=len(frame),paired_seeds=30,aidmesh_cap_violations=int((aid['exposure_cap_ratio']>1).sum()),
        conflicting_safe_quorum_certificates=int(aid['conflicting_quorum_certificates'].sum()),
        original_H1_established=False,scope=p['scope'])
    (out/'summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary))
