"""Supplemental timing diagnosis; does not replace the original H5 benchmark."""
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from py_ecc.bls import G2ProofOfPossession as BLS
from py_ecc.bls.hash_to_curve import hash_to_G2
from aidmesh.crypto.aggregate_certificate import AggregateRoster
import py_ecc
import blspy

keys = [BLS.KeyGen(hashlib.sha256(('todo'+str(i)).encode()).digest()) for i in range(5)]
pubs = [BLS.SkToPk(s) for s in keys]
pops = [BLS.PopProve(s) for s in keys]
t = time.perf_counter()
roster = AggregateRoster(pubs, pops, threshold=5, context=bytes(32))
setup = (time.perf_counter()-t)*1000
rows = []
for i in range(10):
    m = roster.message(hashlib.sha256(str(i).encode()).digest())
    row = {}
    t = time.perf_counter()
    shares = [BLS.Sign(s, m) for s in keys]
    row['five_sign_ms'] = (time.perf_counter()-t)*1000
    t = time.perf_counter()
    assert all(BLS.Verify(p, m, s) for p, s in zip(pubs, shares))
    row['five_verify_ms'] = (time.perf_counter()-t)*1000
    t = time.perf_counter()
    BLS.Aggregate(shares)
    row['aggregate_ms'] = (time.perf_counter()-t)*1000
    t = time.perf_counter()
    hash_to_G2(m, BLS.DST, BLS.xmd_hash_function)
    row['one_hash_to_curve_ms'] = (time.perf_counter()-t)*1000
    rows.append(row)
result = {'python':sys.version, 'py_ecc':py_ecc.__version__, 'blspy':blspy.__version__,
    'q':5, 'trials':10, 'roster_pop_verify_ms':setup, 'rows':rows,
    'medians':{k:statistics.median(x[k] for x in rows) for k in rows[0]},
    'scope':'Supplemental component diagnosis, not replacement for original 100-trial benchmark.'}
out = ROOT/'results/todo_T5_T14_20260929/bls_component_profile_rerun.json'
out.parent.mkdir(exist_ok=True, parents=True)
out.write_text(json.dumps(result, indent=2), encoding='utf8')
print(out)
