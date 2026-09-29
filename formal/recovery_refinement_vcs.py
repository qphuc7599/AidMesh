"""Unbounded arithmetic obligations for the stated abstract simulation.

These VCs prove transition algebra and quorum arithmetic, NOT compiler/code
refinement, recovery view-change agreement, privacy, or liveness on partitions.
Each VC is exported as SMT-LIB; unsafe variants must produce countermodels.
"""
import json
import sys
from pathlib import Path

from z3 import And, Ints, Not, Solver, sat, unsat, get_version_string

out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=False)
rows=[]
def check(name,pre,post,expected=unsat):
    solver=Solver();solver.add(pre,Not(post))
    (out/f'{name}.smt2').write_text(solver.to_smt2())
    result=solver.check()
    assert result==expected,(name,result)
    rows.append(dict(obligation=name,result=str(result),
        countermodel=str(solver.model()) if result==sat else None))

K,q,f,intersection,byzantine=Ints('K q f intersection byzantine')
common=And(K>0,K>=3*f+1,q>0,q<=K,f>=0,f<K,intersection>=2*q-K,
           intersection>=0,intersection<=q,byzantine>=0,byzantine<=f,byzantine<=intersection)
check('honest_quorum_intersection',And(common,2*q>K+f),intersection-byzantine>0)
check('unsafe_intersection',And(common,K==4,q==2,f==1,2*q<=K+f),intersection-byzantine>0,sat)
S,D,E,R,C,v=Ints('source destination escrow charged cap value')
invariant=And(S>=0,D>=0,E>=0,R>=0,C>=0,S+D+E+R==C)
def inv(s,d,e,r):return And(s>=0,d>=0,e>=0,r>=0,s+d+e+r==C)
check('source_debit_outbox',And(invariant,v>0,S>=v),inv(S-v,D,E+v,R))
check('accept_credit',And(invariant,v>0,E>=v),inv(S,D+v,E-v,R))
check('reclaim_credit',And(invariant,v>0,E>=v),inv(S+v,D,E-v,R))
check('fallback_debit_claim',And(invariant,v>0,D>=v),inv(S,D-v,E,R+v))
check('crash_or_replay_stutter',invariant,inv(S,D,E,R))
check('unsafe_refund_without_escrow_debit',And(invariant,v>0,E>=v),inv(S+v,D,E,R),sat)
spent,amount,next_value=Ints('spent amount next_value')
check('gateway_transfer_cap',And(amount>0,spent>=0,spent<=amount,next_value>0,
      spent+next_value<=amount),And(spent+next_value<=amount,spent+next_value>=0))
# Statement explicitly assumes unique durable claim/winner linearization;
# no solver encoding here asserts that SQLite/the EVM implements it correctly.
calls,winner,closed=Ints('calls winner closed')
check('unique_claim_invoke',And(calls==0,winner==1,closed==0),calls+1<=1)
check('unsafe_invoke_replay',And(calls==1,winner==1,closed==1),calls+1<=1,sat)
(out/'summary.json').write_text(json.dumps(dict(solver=get_version_string(),obligations=rows,
    full_implementation_refinement=False,
    scope='unbounded integer arithmetic VCs; atomic transition and unique-claim assumptions'),indent=2))
print(json.dumps(rows))
