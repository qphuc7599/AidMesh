"""Independent conservative prefix audit of release history (integer value units).

An intent is a possible irreversible delivery forever, including AMBIGUOUS and
missing receipts. A new epoch does not erase old obligations. This checks exported
history; authenticity/completeness of actuator observations is an external premise.
"""

from collections import Counter


def audit_prefix_exposure(events):
    caps, debits, authorizations, values = {}, {}, {}, {}
    intents, deliveries, ids, normal = set(), set(), set(), set()
    possible, actual = Counter(), Counter()
    rows = []

    def positive(value):
        if type(value) is not int or value <= 0:
            raise ValueError("values must be positive integer units")
        return value

    for position, event in enumerate(events):
        identity = event["event_id"]
        if not isinstance(identity, str) or not identity or identity in ids:
            raise ValueError("event IDs must be unique; deduplicate transport before audit")
        ids.add(identity)
        kind = event["kind"]
        if kind == "cap":
            cap = event["cap_id"]
            if cap in caps:
                raise ValueError("cap reminted/reset")
            caps[cap] = positive(event["amount"])
        elif kind == "authorize":
            release, entitlement = event["release_id"], event["entitlement"]
            value = positive(event["value"])
            if release in authorizations:
                raise ValueError("release authorization reused")
            if entitlement in values and values[entitlement] != value:
                raise ValueError("entitlement value changed across history")
            values[entitlement] = value
            mode = event["mode"]
            if mode == "fallback":
                debit, cap = event["debit_id"], event["cap_id"]
                if debit in debits or cap not in caps:
                    raise ValueError("missing cap or reused debit")
                debits[debit] = (cap, positive(event["charged"]))
                if debits[debit][1] < value:
                    raise ValueError("fallback undercharged")
            elif mode != "quorum":
                raise ValueError("unknown authorization path")
            authorizations[release] = (entitlement, value, mode)
        elif kind in ("intent", "override"):
            release = event["release_id"]
            if release not in authorizations or release in intents:
                raise ValueError("unbacked or repeated actuator invocation")
            entitlement, value, mode = authorizations[release]
            if kind == "override" and mode != "fallback":
                raise ValueError("override/reissue must retain a charged obligation")
            if mode == "quorum":
                if entitlement in normal:
                    raise ValueError("multiple uncharged deliveries for one entitlement")
                normal.add(entitlement)
            intents.add(release)
            possible[entitlement] += 1
        elif kind == "delivered":
            release = event["release_id"]
            if release not in intents or release in deliveries:
                raise ValueError("physical delivery without unique actuator intent")
            deliveries.add(release)
            actual[authorizations[release][0]] += 1
        elif kind in ("ambiguous", "receipt", "epoch_boundary"):
            if kind != "epoch_boundary" and event["release_id"] not in intents:
                raise ValueError("status without release intent")
            # Deliberately do not release any budget or forget a possible delivery.
        else:
            raise ValueError("unknown history event")
        spent_by_cap = Counter()
        for cap, charge in debits.values():
            spent_by_cap[cap] += charge
        if any(spent_by_cap[cap] > budget for cap, budget in caps.items()):
            raise ValueError("cap exceeded at prefix")
        worst = sum(values[e] * max(count - 1, 0) for e, count in possible.items())
        observed = sum(values[e] * max(count - 1, 0) for e, count in actual.items())
        spent = sum(spent_by_cap.values())
        if not observed <= worst <= spent <= sum(caps.values()):
            raise ValueError("prefix exposure invariant violated")
        rows.append(
            dict(
                prefix=position + 1,
                physical_duplicate_value=observed,
                conservative_duplicate_value=worst,
                charged=spent,
                issued_cap=sum(caps.values()),
                unresolved_intents=len(intents - deliveries),
            )
        )
    return rows
