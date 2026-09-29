"""Exhaustive finite abstraction for cross-path claim/debit/receipt transitions.

Two workers, one site/entitlement, one fallback unit. Process crash destroys its
capability. Does not model Byzantine signatures, power failure or DB rollback.
"""
import json
import sys
from collections import deque
from pathlib import Path


def explore(unsafe=False):
    # durable status, balance, spent, calls, volatile winner, receipt
    initial=('empty',1,0,0,-1,False)
    seen={initial:()};queue=deque([initial]);transitions=0
    while queue:
        state=queue.popleft();status,balance,spent,calls,winner,receipt=state
        if calls>1 or balance+spent!=1 or (receipt and calls!=1):
            return dict(safe=False,counterexample=seen[state],state=state,states=len(seen))
        next_states=[]
        for worker in (0,1):
            for mode in ('quorum','fallback'):
                allowed=status=='empty' or (unsafe and status=='closed' and mode=='quorum')
                if allowed and (mode=='quorum' or balance>=1):
                    charge=int(mode=='fallback')
                    next_states.append((f'{worker}:claim:{mode}',
                        ('intent',balance-charge,spent+charge,calls,worker,False)))
            if winner==worker:
                next_states.append((f'{worker}:crash',('ambiguous',balance,spent,calls,-1,receipt)))
                if status=='intent':
                    next_states.append((f'{worker}:invoke',('invoked',balance,spent,calls+1,worker,False)))
                if status=='invoked':
                    next_states.append((f'{worker}:receipt',('closed',balance,spent,calls,-1,True)))
        for action,target in next_states:
            transitions+=1
            if target not in seen:
                seen[target]=(*seen[state],action);queue.append(target)
    return dict(safe=True,states=len(seen),transitions=transitions,
                scope='bounded one-slot transition abstraction; not machine-checked code refinement')


if __name__=='__main__':
    safe=explore();negative=explore(True)
    assert safe['safe'] and not negative['safe']
    result=dict(safe_model=safe,path_reset_negative_control=negative)
    out=Path(sys.argv[1]);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(result,indent=2));print(json.dumps(result))
