"""Fresh Coconut admission -> durable quota snapshot; run under Coconut WSL venv."""

import json
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))

from aidmesh.crypto.coconut_admission import AdmissionStatement, CoconutAdmissionProfile
from aidmesh.protocol.admission_registry import AdmissionRegistry
from aidmesh.protocol.terminal_recovery import digest

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=False)
# Enrollment-facing synthetic fixture supplies context, not an old credential.
fixture = json.loads((root / 'results/original_pillars_20260906/coconut/p1_admission_witness.json')
                     .read_text())
s = AdmissionStatement(**{k: int(v) for k, v in fixture['statement'].items()})
profile = CoconutAdmissionProfile()
holder = profile.params[1].random()
credential = profile.issue(s, holder, (0, 2, 4))
showing = profile.show_for_admission(credential, holder)
registry = AdmissionRegistry(out / 'registry.sqlite', campaign=s.campaign, epoch=s.epoch,
    deployment_context=s.deployment_context,
    allocations=[dict(cell=s.cell, group=s.group, category=s.category, quota=s.quota, value=10)],
    verify=profile.verify_admission)
admission = registry.admit(s, showing)
assert registry.admit(s, profile.show_for_admission(credential, holder)) == admission
sealed = registry.seal()
registry.close()
(out / 'sealed.json').write_text(json.dumps(sealed, indent=2))

def decimal_strings(value):
    if type(value) is int:
        return str(value)
    if isinstance(value, dict):
        return {k: decimal_strings(v) for k, v in value.items()}
    if isinstance(value, list):
        return [decimal_strings(v) for v in value]
    return value

transport = decimal_strings(sealed['snapshot'])
(out / 'root_input.json').write_text(json.dumps(dict(snapshot=transport,
    digest=digest(transport), sealedDigest=sealed['digest']), indent=2))
(out / 'manifest.json').write_text(json.dumps(dict(admission=admission,
    admitted_before_root_build=True, duplicate_issuance_prevented=True,
    entitlements=len(sealed['snapshot']['entitlements']),
    scope='fresh Coconut admission and sealed quota snapshot; synthetic allocation',
    remaining=['allocator integration', 'root endorsement', 'fresh P1 proof and gateway']), indent=2))
print('Fresh Coconut admission and durable indexed quota snapshot verified.')
