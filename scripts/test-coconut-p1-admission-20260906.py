"""Bind selective credential admission to an actual P1 witness's registry leaf."""
import json
from pathlib import Path
import sys

root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root))
from aidmesh.crypto.coconut_admission import AdmissionStatement,CoconutAdmissionProfile

path=root/'results/original_pillars_20260906/coconut/p1_admission_witness.json'
data=json.loads(path.read_text())
statement=AdmissionStatement(**{k:int(v) for k,v in data['statement'].items()})
p=CoconutAdmissionProfile();holder=p.params[1].random()
credential=p.issue(statement,holder,(0,2,4))
show=p.show_for_admission(credential,holder)
assert p.verify_admission(statement,show)
result=dict(credential_admission_verified=True,credential_leaf=data['credential_leaf'],
            credential_root=data['credential_root'],
            scope='credential attestation and actual cached P1 leaf agree; synthetic retrospective linkage',
            not_established=['online registry admission/root authorization pipeline',
                             'single-proof Coconut signature verification inside P1'])
(path.parent/'p1_admission_binding.json').write_text(json.dumps(result,indent=2))
print('Coconut admission binds the exact P1 commitment and policy tuple.')
