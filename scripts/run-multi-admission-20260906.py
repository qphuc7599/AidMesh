"""Phased synthetic allocator/Coconut/root endorsement integration.

allocate and endorse run on Windows; admit runs in the isolated Coconut Linux venv.
New output directories are required; historical DBs are not reset.
"""
import hashlib
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from aidmesh.protocol.terminal_recovery import digest

phase, directory = sys.argv[1:3]
circuit_path=Path(sys.argv[3]) if len(sys.argv)>3 else ROOT/'zk/build/aidmesh_voucher.r1cs'
out = Path(directory)
enrollment = json.loads((out/'enrollment_public.json').read_text())

def strings(x):
    if type(x) is int:
        return str(x)
    if isinstance(x,list):
        return [strings(v) for v in x]
    if isinstance(x,dict):
        return {k:strings(v) for k,v in x.items()}
    return x

if phase == 'allocate':
    from aidmesh.allocator import PackingProblem, progressive_fill, round_allocation
    from aidmesh.allocator.certificate import certify_max_min, certify_rounding
    groups=sorted({s['group'] for s in enrollment})
    category=enrollment[0]['category']
    total=sum(int(s['quota']) for s in enrollment)
    problem=PackingProblem(cell=enrollment[0]['cell'],
        demand={g:{category:total} for g in groups},stock={category:total},
        weights={g:1 for g in groups},conversion_factors={category:1},
        directions={g:{category:1} for g in groups},units={category:1})
    fair=progressive_fill(problem)
    integer=round_allocation(fair,seed='multi-admission-v2-frozen')
    certify_max_min(problem,fair);certify_rounding(problem,fair,integer)
    allocation=[dict(cell=int(problem.cell),group=int(g),category=int(category),
                     quota=integer.quota(g,category),value=1) for g in groups]
    assert sum(a['quota'] for a in allocation)==total
    (out/'allocation.json').write_text(json.dumps(allocation,indent=2))
elif phase == 'admit':
    from aidmesh.crypto.coconut_admission import AdmissionStatement,CoconutAdmissionProfile
    from aidmesh.protocol.admission_registry import AdmissionRegistry
    if (out/'registry.sqlite').exists():
        raise RuntimeError('choose a fresh directory; registry already exists')
    profile=CoconutAdmissionProfile()
    first=enrollment[0]
    registry=AdmissionRegistry(out/'registry.sqlite',campaign=int(first['campaign']),
        epoch=int(first['epoch']),deployment_context=int(first['deployment_context']),
        allocations=json.loads((out/'allocation.json').read_text()),
        verify=profile.verify_admission,multi_beneficiary=True)
    for fields in enrollment:
        statement=AdmissionStatement(**{k:int(v) for k,v in fields.items()})
        holder=profile.params[1].random()
        credential=profile.issue(statement,holder,(0,2,4))
        identifier=registry.admit(statement,profile.show_for_admission(credential,holder))
        assert registry.admit(statement,profile.show_for_admission(credential,holder))==identifier
    publication=registry.seal();registry.close()
    (out/'sealed.json').write_text(json.dumps(publication,indent=2))
    transport=strings(publication['snapshot'])
    (out/'root_input.json').write_text(json.dumps(dict(snapshot=transport,
        digest=digest(transport),sealedDigest=publication['digest']),indent=2))
elif phase == 'endorse':
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    from aidmesh.protocol.root_endorsement import (
        RootStatement,RootEndorser,committee_digest,verify_root_endorsement)
    from aidmesh.protocol.witness import SQLiteWitnessStore
    roots=json.loads((out/'roots.json').read_text())
    publication=json.loads((out/'root_input.json').read_text())
    if roots['snapshotDigest'] != publication['digest'] or digest(publication['snapshot'])!=publication['digest']:
        raise ValueError('snapshot/root mismatch')
    members=tuple(f'issuer{i}' for i in range(4))
    keys={m:Ed25519PrivateKey.generate() for m in members}
    def sign(m,d):
        return keys[m].sign(bytes.fromhex(d)).hex()
    def verify(m,d,s):
        try:
            keys[m].public_key().verify(bytes.fromhex(s),bytes.fromhex(d));return True
        except (ValueError,InvalidSignature):
            return False
    statement=RootStatement(int(enrollment[0]['campaign']),int(enrollment[0]['epoch']),
        int(enrollment[0]['deployment_context']),int(roots['credential']['root']),
        int(roots['entitlement']['root']),digest(publication['snapshot']['policy']),
        publication['digest'],hashlib.sha256(circuit_path.read_bytes()).hexdigest(),
        committee_digest(members,3,1))
    shares=[]
    for member in members:
        store=SQLiteWitnessStore(out/f'{member}.sqlite',signer_id=member)
        shares.append(RootEndorser(member,statement,store,sign).endorse(statement));store.close()
    assert verify_root_endorsement(statement,shares,members=members,threshold=3,faults=1,verify=verify)
    (out/'endorsement.json').write_text(json.dumps(dict(statement=asdict(statement),shares=shares,
        members=members,threshold=3,faults=1,public_keys={m:keys[m].public_key().public_bytes(
        serialization.Encoding.Raw,serialization.PublicFormat.Raw).hex() for m in members},
        scope='local four-key endorsement of one approved reconstruction; not independent issuer deployment'),indent=2))
else:
    raise ValueError('unknown phase')
print(phase+' verified')
