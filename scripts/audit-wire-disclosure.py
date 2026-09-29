"""Audit actual received payloads for prohibited field names and observable links.

This is schema/data-minimization evidence, not a proof of unlinkability or detection
of secrets maliciously encoded into an allowed field.
"""
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

paths=[Path('results/recovery_gateway_20260906/run2_reproducible/bpv7/received.json'),
       Path('results/comprehensive_revision_20260906/multi/bpv7/received.json')]
forbidden={'secret','secretBlinding','holder_secret','name','address','gps','vulnerability',
           'eligibility_document','phone','beneficiary_id'}

def fields(value,prefix=()):
    if isinstance(value,dict):
        for key,child in value.items():
            yield (*prefix,key)
            yield from fields(child,(*prefix,key))
    elif isinstance(value,list):
        for child in value:yield from fields(child,(*prefix,'[]'))

reports=[]
for path in paths:
    raw=path.read_bytes();payload=json.loads(raw)
    names=sorted(set(fields(payload)))
    violations=[p for p in names if any(k in forbidden for k in p)]
    if violations:raise AssertionError(violations)
    reports.append(dict(path=str(path),sha256=hashlib.sha256(raw).hexdigest(),
        fields=['.'.join(p) for p in names],prohibited_fields_found=[],
        observations=len(payload),observable_links=['repeated nullifier',
        'site signer','campaign/epoch','fallback path','value','transport endpoint']))
# Negative control exercises the checker itself.
assert any('secret' in p for p in fields({'voucher':{'secret':'synthetic-negative'}}))
out=Path(sys.argv[1]);out.parent.mkdir(parents=True,exist_ok=True)
out.write_text(json.dumps(dict(reports=reports,negative_control_detected=True,
    scope='field-name minimization on actual BPv7 application payloads; timing/headers not hidden'),indent=2))
print('Payload disclosure inventory recorded; prohibited-field negative control detected.')
