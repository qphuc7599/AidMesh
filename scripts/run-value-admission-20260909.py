"""Fresh two-holder unequal-value Coconut admission; run with Coconut runtime."""
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from aidmesh.crypto.coconut_admission import AdmissionStatement,CoconutAdmissionProfile
from aidmesh.protocol.value_admission_registry import ValueAdmissionRegistry
from aidmesh.protocol.terminal_recovery import digest
out=Path(sys.argv[1]);enrollment=json.loads((out/'enrollment_public.json').read_text())
configuration=json.loads((out/'configuration.json').read_text())
if (out/'registry.sqlite').exists(): raise RuntimeError('fresh directory required')
assert len(enrollment)>=2 and all(int(s['quota'])>0 for s in enrollment)
p=CoconutAdmissionProfile();s0=enrollment[0]
total_quota=sum(int(s['quota']) for s in enrollment)
values=configuration.get('values',[100,1] if total_quota==2 else
    [1+(i%2) for i in range(total_quota)])
assert len(values)==total_quota and all(type(v) is int and v>0 for v in values)
allocation=[dict(cell=int(s0['cell']),group=int(s0['group']),category=int(s0['category']),
                entitlements=[dict(serial=i+1,value=v) for i,v in enumerate(values)])]
r=ValueAdmissionRegistry(out/'registry.sqlite',campaign=int(s0['campaign']),epoch=int(s0['epoch']),
    deployment_context=int(s0['deployment_context']),allocations=allocation,verify=p.verify_admission)
statements=[]; credentials=[];holders=[]
for fields in enrollment:
    s=AdmissionStatement(**{k:int(v) for k,v in fields.items()});h=p.params[1].random()
    statements.append(s);holders.append(h);credentials.append(p.issue(s,h,(0,2,4)))
cursor=1; assignments=[]
for statement in statements:
    assigned=list(range(cursor,cursor+statement.quota));assignments.append(assigned);cursor+=statement.quota
r.admit(statements[0],p.show_for_admission(credentials[0],holders[0]),serials=assignments[0])
try:
    r.admit(statements[1],p.show_for_admission(credentials[1],holders[1]),serials=assignments[0])
except ValueError as e:
    assert str(e)=='serial already assigned'
else: raise AssertionError('denomination counterexample accepted')
for i in range(1,len(statements)):
    r.admit(statements[i],p.show_for_admission(credentials[i],holders[i]),serials=assignments[i])
publication=r.seal();r.close()
def strings(v):
    if type(v) is int:return str(v)
    if isinstance(v,list):return [strings(x) for x in v]
    if isinstance(v,dict):return {k:strings(x) for k,x in v.items()}
    return v
snapshot=strings(publication['snapshot'])
(out/'sealed.json').write_text(json.dumps(publication,indent=2))
(out/'root_input.json').write_text(json.dumps(dict(snapshot=snapshot,digest=digest(snapshot),
    sealedDigest=publication['digest']),indent=2))
(out/'admission_summary.json').write_text(json.dumps(dict(coconut_credentials=len(statements),approved_value=sum(values),
    admitted_value=sum(e['value'] for e in publication['snapshot']['entitlements']),
    duplicate_high_value_rejected=True,profile=r.PROFILE),indent=2))
print(f'Fresh Coconut admission: duplicate serial rejected; {sum(values)} value reserved.')
