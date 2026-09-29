"""Unbounded arithmetic VCs for value-aware admission and epoch migration.

The transition is abstract. These VCs complement, rather than replace, the
SQLite race tests, P1 witness tests and symbolic unique-serial proof.
"""
import json
import sys
from pathlib import Path
from z3 import And, Ints, Not, Solver, get_version_string, sat, unsat

out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=False);rows=[]
def check(name,pre,post,expected=unsat):
    solver=Solver();solver.add(pre,Not(post));result=solver.check()
    (out/f'{name}.smt2').write_text(solver.to_smt2())
    assert result==expected,(name,result)
    rows.append(dict(name=name,result=str(result),
        countermodel=str(solver.model()) if result==sat else None))

u,r,s,o,cap,v=Ints('unassigned reserved spent outstanding cap value')
inv=And(u>=0,r>=0,s>=0,o>=0,cap>=0,u+r+s+o==cap)
def valid(a,b,c,d):return And(a>=0,b>=0,c>=0,d>=0,a+b+c+d==cap)
check('admission_reserve',And(inv,v>0,u>=v),valid(u-v,r+v,s,o))
check('authorization_spend',And(inv,v>0,r>=v),valid(u,r-v,s+v,o))
check('epoch_retains_unresolved',And(inv,v>0,r>=v),valid(u,r-v,s,o+v))
check('reissue_charges_same_cap',And(inv,v>0,o>=v,u>=v),valid(u-v,r+v,s,o))
check('unsafe_reissue_after_cap_reset',And(inv,v>0,o>=v),
      And(u+r+v+s+o==cap),sat)
check('unsafe_drop_outstanding',And(inv,v>0,o>=v),valid(u,r,s,0),sat)

(out/'summary.json').write_text(json.dumps(dict(solver=get_version_string(),obligations=rows,
    safe_vcs=4,unsafe_counterexamples=2,
    scope='unbounded integer transition algebra; assumes atomic unique-serial assignment'),indent=2))
print(json.dumps(rows,indent=2))
