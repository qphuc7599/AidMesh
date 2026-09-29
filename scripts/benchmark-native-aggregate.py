"""Cross-verify retained py_ecc vectors with optional native BLS backend."""
import json
import statistics
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from aidmesh.crypto.aggregate_certificate import AggregateRoster

out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=False)
rows=[]
for k in (4,7,10):
    d=json.loads((ROOT/f'results/aggregate_certificate_20260906/k{k}.json').read_text())
    roster=AggregateRoster([bytes.fromhex(p) for p in d['public_keys']],
        [bytes.fromhex(p) for p in d['possession_proofs']],threshold=2*((k-1)//3)+1,context=bytes(32))
    wire=bytes.fromhex(d['wire']); statement=wire[36:68]
    assert roster.verify(statement,wire) and roster.verify_native(statement,wire)
    measured=[]
    for i in range(103):
        start=time.perf_counter_ns();assert roster.verify_native(statement,wire)
        elapsed=(time.perf_counter_ns()-start)/1e6
        if i>=3:measured.append(elapsed)
    for i in range(len(wire)):
        bad=bytearray(wire);bad[i]^=1
        assert not roster.verify_native(statement,bytes(bad))
    rows.append(dict(K=k,bytes=len(wire),median_ms=statistics.median(measured),
        runs_ms=measured,mutations_rejected=len(wire),backend='blspy==2.0.3',
        full_H5=False,scope='same retained aggregate vector; certificate verification only'))
(out/'summary.json').write_text(json.dumps(rows,indent=2))
print(json.dumps([{k:v for k,v in r.items() if k!='runs_ms'} for r in rows]))
