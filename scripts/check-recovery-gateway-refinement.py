"""Finite safety abstraction, not a proof of the Python/SQLite implementation.

One escrowed unit, two sites, one release per site, gateway restart/replay.
Recovery agreement is an explicit assumption, exercised by a negative control.
The durable gateway transaction is atomic; no Byzantine rollback is modelled.
"""
import json
import sys
from collections import deque
from pathlib import Path


def explore(broken=None):
    # Decision mask; credited mask; balances; release phases (0/intent/call/receipt);
    # invocation counts; received mask; settlement credits.
    initial = (0, 0, (0, 0), (0, 0), (0, 0), 0, 0)
    traces = {initial: ()}
    queue = deque([initial])
    edges = 0
    while queue:
        state = queue.popleft()
        decision, credited, balance, phase, calls, received, settled = state
        charged = sum(p > 0 for p in phase)
        invariant = (sum(balance) + charged <= 1 and sum(calls) <= 1
                     and settled <= 1 and settled <= sum(p == 3 for p in phase))
        if not invariant:
            return dict(safe=False, states=len(traces), counterexample=traces[state], state=state)
        targets = []
        for site in range(2):
            bit = 1 << site
            if not decision or broken == 'recovery_agreement':
                targets.append((f'decide:{site}', (decision | bit, credited, balance, phase, calls, received, settled)))
            if decision & bit and not credited & bit:
                b = list(balance); b[site] += 1
                targets.append((f'credit:{site}', (decision, credited | bit, tuple(b), phase, calls, received, settled)))
            if balance[site] and phase[site] == 0:
                b = list(balance); b[site] -= 1
                p = list(phase); p[site] = 1
                targets.append((f'claim-debit:{site}', (decision, credited, tuple(b), tuple(p), calls, received, settled)))
            if phase[site] == 1:
                p = list(phase); p[site] = 2
                c = list(calls); c[site] += 1
                targets.append((f'invoke:{site}', (decision, credited, balance, tuple(p), tuple(c), received, settled)))
            if phase[site] == 2:
                p = list(phase); p[site] = 3
                targets.append((f'close-receipt:{site}', (decision, credited, balance, tuple(p), calls, received, settled)))
            if phase[site] == 3:
                increment = int(not received & bit)
                targets.append((f'deliver-or-replay:{site}', (decision, credited, balance, phase, calls, received | bit, settled + increment)))
        # Safe restart is a stutter. Unsafe restart loses deduplication only.
        targets.append(('gateway-restart', (decision, credited, balance, phase, calls,
                                            0 if broken == 'volatile_gateway' else received, settled)))
        for action, target in targets:
            edges += 1
            if target not in traces:
                traces[target] = (*traces[state], action)
                queue.append(target)
    return dict(safe=True, states=len(traces), transitions=edges)


if __name__ == '__main__':
    result = dict(scope='finite atomic-transition abstraction; recovery agreement assumed; no liveness or code refinement proof',
                  safe=explore(), agreement_negative=explore('recovery_agreement'),
                  durability_negative=explore('volatile_gateway'))
    assert result['safe']['safe']
    assert not result['agreement_negative']['safe'] and not result['durability_negative']['safe']
    path = Path(sys.argv[1]); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2))
    print(json.dumps(result))
