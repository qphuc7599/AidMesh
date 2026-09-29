"""Run safe value-admission proof and a reusable-serial negative control."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];out=Path(sys.argv[1]).resolve();out.mkdir(parents=True,exist_ok=False)
safe=ROOT/'formal/AidMeshValueAdmission.spthy'
unsafe_text=safe.read_text().replace('theory AidMeshValueAdmission','theory AidMeshValueAdmissionUnsafe')
assert unsafe_text.count('Available(')==2
unsafe_text=unsafe_text.replace('Available(','!Available(')
unsafe=out/'unsafe_reusable_serial.spthy';unsafe.write_text(unsafe_text,encoding='utf-8')
records=[];wsl='/mnt/d/DoAnChuyenNganh/AirProof'
for name,path,lemma in [('safe',safe,None),('unsafe',unsafe,'serial_assigned_once')]:
    rel=str(path.relative_to(ROOT)).replace('\\','/')
    cmd=['wsl','--cd',wsl,'./external/tamarin/tamarin-prover',rel,
         '--prove'+('='+lemma if lemma else ''),'--with-maude='+wsl+'/tools/maude-3.5.1/bin/maude']
    result=subprocess.run(cmd,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=120)
    log=result.stdout+result.stderr;(out/f'{name}.log').write_text(log,encoding='utf-8')
    if result.returncode!=0 or 'wellformedness check failed' in log: raise RuntimeError(log[-2000:])
    if name=='safe':
        assert log.count(': verified (')==5 and 'falsified' not in log
    else: assert 'serial_assigned_once (all-traces): falsified' in log
    records.append(dict(name=name,model=rel,sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        outcome='5 verified' if name=='safe' else 'serial reuse counterexample'))
(out/'summary.json').write_text(json.dumps(dict(models=records,tamarin='1.12.0',maude='3.5.1',
    scope='unbounded symbolic disjoint-serial/holder/value binding; no bytecode or arithmetic sums'),indent=2),encoding='utf-8')
print(json.dumps(records,indent=2))
