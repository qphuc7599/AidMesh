"""Run in Coconut Linux venv. Functional validation, not a matched H1 campaign."""
import hashlib
import json
import secrets
import statistics
import sys
import time
from dataclasses import replace
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from aidmesh.crypto.coconut_admission import AdmissionStatement,CoconutAdmissionProfile
from aidmesh.protocol.coconut_online import CoconutOnlineLedger

out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=False)
profile=CoconutAdmissionProfile();sites=('a','b')
def ledger():return CoconutOnlineLedger(out/'ledger.sqlite',campaign=96206,epoch=1,
    deployment_context=1,sites=sites,verify=profile.verify_admission)
db=ledger();rows=[];negative=0
for i in range(100):
    statement=AdmissionStatement(commitment=secrets.randbits(240),campaign=96206,epoch=1,
        cell=1,group=i//25,category=1,quota=1,deployment_context=1)
    holder=profile.params[1].random()
    start=time.perf_counter_ns();credential=profile.issue(statement,holder,(0,2,4))
    issuance=(time.perf_counter_ns()-start)/1e6
    showing=profile.show_for_admission(credential,holder)
    assert not db.redeem(statement,showing,site='a',reachable=False)['accepted']
    if i<4:
        for name in statement.__dataclass_fields__:
            bad=replace(statement,**{name:getattr(statement,name)+1})
            try:db.redeem(bad,showing,site='a')
            except ValueError:negative+=1
            else:raise AssertionError('mutation accepted: '+name)
        try:
            short=profile.issue(statement,holder,(0,2))
            db.redeem(statement,profile.show_for_admission(short,holder),site='a')
        except ValueError:negative+=1
        else:raise AssertionError('insufficient issuer threshold accepted')
    start=time.perf_counter_ns();assert db.redeem(statement,showing,site='a')['credit']==1
    redeem=(time.perf_counter_ns()-start)/1e6
    db.close();db=ledger()
    assert db.redeem(statement,profile.show_for_admission(credential,holder),site='b')['credit']==0
    rows.append(dict(token=i,issuance_ms=issuance,online_redeem_ms=redeem))
    if (i+1)%20==0:print(f'{i+1}/100 real Coconut tokens redeemed',flush=True)
assert db.db.execute('SELECT COUNT(*) FROM redeemed').fetchone()[0]==100;db.close()
(out/'runs.json').write_text(json.dumps(rows,indent=2))
summary=dict(valid_unique_tokens=100,duplicate_credits=0,field_and_threshold_rejections=negative,
    issuance_median_ms=statistics.median(r['issuance_ms'] for r in rows),
    redemption_median_ms=statistics.median(r['online_redeem_ms'] for r in rows),
    scope='unit-token Coconut-Online adaptation; true Coconut; single online ledger; no matched network campaign',
    original_H1_established=False,
    adapter_sha256=hashlib.sha256((ROOT/'aidmesh/protocol/coconut_online.py').read_bytes()).hexdigest())
(out/'summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary))
