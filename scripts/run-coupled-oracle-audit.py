"""Small synthetic exact-oracle validation, not a field/confirmatory holdout."""
import json
import random
import sys
from dataclasses import asdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from aidmesh.analysis.coupled_oracle import Contact,Instance,Packet,replay_schedule,solve_exact

out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=False)
protocol=dict(seeds=list(range(97301,97421)),packets=4,slots=5,
              scope='synthetic exact-oracle and feasible baseline audit; no independent holdout claim')
(out/'protocol.json').write_text(json.dumps(protocol,indent=2))
rows=[]
for seed in protocol['seeds']:
    rng=random.Random(seed)
    packets=tuple(Packet('a','c',rng.choice((1,2)),0 if i==0 else 1,
                         (0,) if i and rng.random()<0.5 else ()) for i in range(4))
    slots=tuple(tuple(Contact(s,d,rng.randint(1,4)) for s,d in
                      rng.sample([('a','b'),('b','c'),('a','c'),('b','a'),('c','b')],2))
                for _ in range(5))
    instance=Instance(packets,slots,{'a':8,'b':3,'c':0},rng.randint(1,4))
    # Online greedy sees the current contact only. Its candidate actions are
    # validated by replay, not by the oracle transition enumerator.
    schedule=[() for _ in slots]
    for t,contacts in enumerate(slots):
        action=[]
        order=sorted(range(4),key=lambda i:(-packets[i].reward,i))
        for i in order:
            for j in sorted(range(len(contacts)),key=lambda j:contacts[j].destination!='c'):
                trial=(*action,(i,j));schedule[t]=trial
                try:
                    replay_schedule(instance,schedule)
                except ValueError:
                    schedule[t]=tuple(action)
                    continue
                action=list(trial);break
        schedule[t]=tuple(action)
    baseline=replay_schedule(instance,schedule)
    optimum=solve_exact(instance)
    assert optimum['optimal_reward']>=baseline['reward']
    rows.append(dict(seed=seed,instance=asdict(instance),baseline=baseline,
                     baseline_schedule=schedule,exact=optimum))
(out/'runs.json').write_text(json.dumps(rows,indent=2))
summary=dict(instances=len(rows),all_exact_schedules_replayed=True,
    baseline_never_exceeds_optimum=True,
    baseline_reward=sum(r['baseline']['reward'] for r in rows),
    optimal_reward=sum(r['exact']['optimal_reward'] for r in rows))
(out/'summary.json').write_text(json.dumps(summary,indent=2))
print(json.dumps(summary))
