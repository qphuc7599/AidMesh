"""Run with external/coconut_runtime/bin/python under WSL."""
from dataclasses import replace
import json
from pathlib import Path
import sys

root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root))
from aidmesh.crypto.coconut_admission import AdmissionStatement, CoconutAdmissionProfile

p=CoconutAdmissionProfile();holder=p.params[1].random()
statement=AdmissionStatement(2**253+109,123,1,7,4,3,10,2**250+57)
credential=p.issue(statement,holder,(0,2,4))
show=p.show_for_admission(credential,holder)
assert p.verify_admission(statement,show)
assert p.verify_admission(statement,p.show_for_admission(credential,holder))
checks=[]
for field in statement.__dataclass_fields__:
    changed=replace(statement,**{field:getattr(statement,field)+1})
    assert not p.verify_admission(changed,show)
    checks.append(field)
for selected in ((0,1),(0,0,1),(0,1,9)):
    try:
        p.issue(statement,holder,selected)
        raise AssertionError('accepted invalid issuer selection')
    except ValueError:
        pass
assert not p.verify_admission(statement,p.show_for_admission(credential,holder+1))
out=root/'results/original_pillars_20260906/coconut/admission.json'
out.write_text(json.dumps(dict(valid_showings=2,mutated_fields_rejected=checks,
    bad_issuer_selections_rejected=3,wrong_holder_rejected=True,
    scope='selective commitment disclosure to registry builder; P1 root binding still required'),indent=2))
print(out)
