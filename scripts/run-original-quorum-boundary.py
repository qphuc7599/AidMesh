"""Exhaustive K=4,7,10 quorum intersection audit with signed counterexamples.

This is the original H2 combinatorial boundary, not a simulated adversarial workload.
"""
import json
import sys
from itertools import combinations
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

rows=[]
for size in (4,7,10):
    assumed=(size-1)//3
    for faults in range(assumed+2):
        faulty=set(range(faults))
        keys=[Ed25519PrivateKey.generate() for _ in range(size)]
        for threshold in range(1,size+1):
            committees=list(combinations(range(size),threshold))
            counterexample=None;pairs=0
            for i,left in enumerate(committees):
                for right in committees[i:]:
                    pairs+=1
                    if set(left)&set(right)<=faulty:
                        counterexample=(left,right);break
                if counterexample:break
            safe=2*threshold>size+faults
            assert (counterexample is None)==safe
            evidence=None
            if counterexample:
                certificate=[]
                for side,signers in enumerate(counterexample):
                    message=f'original-H2:K={size}:q={threshold}:f={faults}:site={side}'.encode()
                    shares=[]
                    for signer in signers:
                        signature=keys[signer].sign(message)
                        keys[signer].public_key().verify(signature,message)
                        shares.append(dict(signer=signer,signature=signature.hex()))
                    certificate.append(dict(message=message.decode(),shares=shares))
                evidence=certificate
            rows.append(dict(K=size,q=threshold,f=faults,safe_region=safe,
                conflicting_valid_subsets=counterexample is not None,pairs_searched=pairs,
                signed_counterexample=evidence))
            rows[-1]['public_keys']=[key.public_key().public_bytes(
                serialization.Encoding.Raw,serialization.PublicFormat.Raw).hex() for key in keys]
out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=False)
(out/'rows.json').write_text(json.dumps(rows,indent=2))
summary=dict(configurations=len(rows),safe_configurations=sum(r['safe_region'] for r in rows),
    all_safe_zero_conflict=True,all_unsafe_signed_counterexamples=True,
    scope='exhaustive quorum-set boundary; no crash, mobility or workload inference')
(out/'summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary))
