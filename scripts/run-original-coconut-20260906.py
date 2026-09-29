"""Real upstream Coconut component audit; run in isolated Linux venv.

This does not claim a composed Coconut-to-P1 proof. Hidden attributes use fresh
random scalars; public attributes bind campaign/epoch/cell/category/quota.
"""
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import time

from coconut.scheme import (setup, ttp_keygen, agg_key, prepare_blind_sign,
                            blind_sign, unblind, agg_cred, prove_cred, verify_cred)
from coconut.utils import elgamal_keygen

root=Path(__file__).resolve().parents[1]
out=root/'results/original_pillars_20260906/coconut'
out.mkdir(parents=True,exist_ok=True)
protocol=dict(trials=20,threshold=3,issuers=5,private_attributes=2,
              public_attributes=['campaign','epoch','cell','category','quota'],
              scope='credential component; no composed P1 or deployment claim')
(out/'protocol.json').write_text(json.dumps(protocol,indent=2))
params=setup(7);sks,vks=ttp_keygen(params,3,5);vk=agg_key(params,vks)
rows=[]
for trial in range(20):
    hidden=[params[1].random(),params[1].random()]
    public=[96206,1,7,3,10+trial]
    d,gamma=elgamal_keygen(params)
    started=time.perf_counter_ns()
    request=prepare_blind_sign(params,gamma,hidden,public_m=public)
    shares=[unblind(params,blind_sign(params,sk,gamma,request,public_m=public),d) for sk in sks]
    selected=[s if i in (0,2,4) else None for i,s in enumerate(shares)]
    credential=agg_cred(params,selected)
    issue_ms=(time.perf_counter_ns()-started)/1e6
    started=time.perf_counter_ns();show=prove_cred(params,vk,credential,hidden)
    show_ms=(time.perf_counter_ns()-started)/1e6
    started=time.perf_counter_ns();valid=verify_cred(params,vk,show,public_m=public)
    verify_ms=(time.perf_counter_ns()-started)/1e6
    assert valid
    second=prove_cred(params,vk,credential,hidden)
    assert verify_cred(params,vk,second,public_m=public)
    # Rerandomized signature components differ; this is a sanity check, not
    # an empirical proof of computational unlinkability.
    randomized=show!=second
    assert randomized
    mutated=[]
    for i in range(5):
        bad=public.copy();bad[i]+=1
        mutated.append(not verify_cred(params,vk,show,public_m=bad))
    assert all(mutated)
    short=agg_cred(params,[shares[0],shares[1],None,None,None])
    short_show=prove_cred(params,vk,short,hidden)
    insufficient=not verify_cred(params,vk,short_show,public_m=public)
    assert insufficient
    wrong=hidden.copy();wrong[0]=(wrong[0]+1)%params[1]
    wrong_show=prove_cred(params,vk,credential,wrong)
    hidden_rejected=not verify_cred(params,vk,wrong_show,public_m=public)
    assert hidden_rejected
    rows.append(dict(trial=trial,issue_ms=issue_ms,show_ms=show_ms,verify_ms=verify_ms,
                     valid=True,rerandomized_valid=True,mutations_rejected=5,
                     insufficient_rejected=insufficient,wrong_hidden_rejected=hidden_rejected))
(out/'runs.json').write_text(json.dumps(rows,indent=2))
manifest=dict(protocol=protocol,valid_showings=40,negative_checks=140,
              median_issue_ms=statistics.median(x['issue_ms'] for x in rows),
              median_show_ms=statistics.median(x['show_ms'] for x in rows),
              median_verify_ms=statistics.median(x['verify_ms'] for x in rows),
              upstream_commit=subprocess.check_output(['git','-C',str(root/'external/coconut_original'),
                                                       'rev-parse','HEAD'],text=True).strip(),
              bplib_commit=subprocess.check_output(['git','-C',str(root/'external/bplib_compat'),
                                                    'rev-parse','HEAD'],text=True).strip(),
              compatibility_patch='FP2_zero: checked BN_set_word(x,0) replaces void BN_zero',
              script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
(out/'manifest.json').write_text(json.dumps(manifest,indent=2));print(json.dumps(manifest,indent=2))
