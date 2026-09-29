"""Real BLS aggregate component benchmark; do not label as full original H5."""
import hashlib
import json
import secrets
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from py_ecc.bls import G2ProofOfPossession as BLS
from aidmesh.crypto.aggregate_certificate import AggregateRoster

out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=False)
(out/'protocol.json').write_text(json.dumps(dict(K=[4,7,10],trials=10,
    scope='BLS PoP component; no P1, no Solidity, not complete H5',
    source_sha256=hashlib.sha256((ROOT/'aidmesh/crypto/aggregate_certificate.py').read_bytes()).hexdigest()),indent=2))
results=[]
for k in (4,7,10):
    sk = [BLS.KeyGen(secrets.token_bytes(32)) for _ in range(k)]
    pk = [BLS.SkToPk(s) for s in sk]
    pops = [BLS.PopProve(s) for s in sk]
    q = 2*((k-1)//3)+1
    roster = AggregateRoster(pk,pops,threshold=q,context=bytes(32))
    statement = hashlib.sha256(b'aggregate-component-statement').digest()
    shares = {i:BLS.Sign(sk[i],roster.message(statement)) for i in range(q)}
    wire = roster.aggregate(statement,shares)
    assert roster.verify(statement,wire)
    times=[]
    for _ in range(10):
        start=time.perf_counter_ns(); assert roster.verify(statement,wire)
        times.append((time.perf_counter_ns()-start)/1e6)
    negative=0
    for index in (0,4,36,68,69,70,165):
        bad=bytearray(wire); bad[index]^=1
        assert not roster.verify(statement,bytes(bad)); negative+=1
    assert not roster.verify(hashlib.sha256(b'wrong').digest(),wire)
    (out/f'k{k}.json').write_text(json.dumps(dict(public_keys=[p.hex() for p in pk],
        possession_proofs=[p.hex() for p in pops],wire=wire.hex(),verify_ms=times),indent=2))
    results.append(dict(K=k,q=q,bytes=len(wire),verify_median_ms=statistics.median(times),
                        mutated_fields_rejected=negative,full_H5=False))
    print(json.dumps(results[-1]),flush=True)
(out/'summary.json').write_text(json.dumps(results,indent=2))
