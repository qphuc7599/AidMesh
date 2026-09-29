"""Exact finite-horizon single-copy store-and-forward scheduling reference.

All packets, including control prerequisites, consume the same slot/link bytes and
node buffers. Delivery at slot t enables dependents only at t+1. Model scope is
explicit: fixed packets, no fragmentation/copying, reliable chosen contacts, zero
processing delay. Optimality is only for this model, not arbitrary DTN execution.
"""

from dataclasses import dataclass
from itertools import combinations


@dataclass(frozen=True)
class Packet:
    source: str
    destination: str
    size: int
    reward: int = 0
    dependencies: tuple[int, ...] = ()


@dataclass(frozen=True)
class Contact:
    source: str
    destination: str
    capacity: int


@dataclass(frozen=True)
class Instance:
    packets: tuple[Packet, ...]
    slots: tuple[tuple[Contact, ...], ...]
    buffers: dict[str, int]
    slot_bytes: int


def validate(instance):
    if type(instance.slot_bytes) is not int or instance.slot_bytes < 0:
        raise ValueError("invalid shared slot budget")
    if not instance.buffers or any(type(v) is not int or v < 0 for v in instance.buffers.values()):
        raise ValueError("invalid buffers")
    for index, p in enumerate(instance.packets):
        if (
            p.source not in instance.buffers
            or p.destination not in instance.buffers
            or p.source == p.destination
            or type(p.size) is not int
            or p.size <= 0
            or type(p.reward) is not int
            or p.reward < 0
            or any(type(d) is not int or not 0 <= d < index for d in p.dependencies)
        ):
            raise ValueError("invalid packet or non-topological dependency")
    for slot in instance.slots:
        for c in slot:
            if (
                c.source not in instance.buffers
                or c.destination not in instance.buffers
                or c.source == c.destination
                or type(c.capacity) is not int
                or c.capacity < 0
            ):
                raise ValueError("invalid contact")
    initial = tuple(p.source for p in instance.packets)
    if not _fits(instance, initial):
        raise ValueError("initial buffer overflow")
    return initial


def _fits(instance, state):
    return all(
        sum(p.size for p, location in zip(instance.packets, state, strict=True) if location == node)
        <= capacity
        for node, capacity in instance.buffers.items()
    )


def solve_exact(instance, *, max_states=100000):
    """Enumerate every feasible simultaneous action; merge identical boundary states.

    Exceeding the bound raises: a truncated search is never labelled an optimum.
    """
    initial = validate(instance)
    frontier = {initial: ()}
    explored = 0
    for contacts in instance.slots:
        following = {}
        for state, schedule in frontier.items():
            options = [
                (i, j)
                for i, p in enumerate(instance.packets)
                for j, c in enumerate(contacts)
                if state[i] == c.source and all(state[d] is None for d in p.dependencies)
            ]
            # Empty action allows waiting; every causal feasible action is a subset.
            for length in range(min(len(instance.packets), len(options)) + 1):
                for action in combinations(options, length):
                    if len({i for i, _ in action}) != len(action):
                        continue
                    if sum(instance.packets[i].size for i, _ in action) > instance.slot_bytes:
                        continue
                    if any(
                        sum(instance.packets[i].size for i, k in action if k == j) > c.capacity
                        for j, c in enumerate(contacts)
                    ):
                        continue
                    target = list(state)
                    for i, j in action:
                        destination = contacts[j].destination
                        target[i] = (
                            None if destination == instance.packets[i].destination else destination
                        )
                    target = tuple(target)
                    if _fits(instance, target):
                        following.setdefault(target, (*schedule, action))
        explored += len(following)
        if explored > max_states:
            raise RuntimeError("exact search state budget exceeded; no optimum reported")
        frontier = following

    def reward(state):
        return sum(
            p.reward
            for p, location in zip(instance.packets, state, strict=True)
            if location is None
        )

    best = max(frontier, key=reward)
    schedule = frontier[best]
    replayed = replay_schedule(instance, schedule)
    assert replayed["reward"] == reward(best)
    return dict(
        optimal_reward=reward(best),
        schedule=schedule,
        states=explored,
        replay=replayed,
        scope="exact fixed-packet single-copy model",
    )


def replay_schedule(instance, schedule):
    """Independent imperative replay; does not use the solver's transition helper."""
    validate(instance)
    if len(schedule) != len(instance.slots):
        raise ValueError("schedule horizon mismatch")
    locations = [p.source for p in instance.packets]
    delivered = set()
    total_bytes = 0
    for contacts, action in zip(instance.slots, schedule, strict=True):
        before = list(locations)
        sent = set()
        usage = [0] * len(contacts)
        new = []
        for packet_index, contact_index in action:
            if (
                type(packet_index) is not int
                or type(contact_index) is not int
                or not 0 <= packet_index < len(instance.packets)
                or not 0 <= contact_index < len(contacts)
                or packet_index in sent
            ):
                raise ValueError("invalid or duplicate transmission")
            p, c = instance.packets[packet_index], contacts[contact_index]
            if before[packet_index] != c.source or not set(p.dependencies) <= delivered:
                raise ValueError("noncausal transmission")
            sent.add(packet_index)
            usage[contact_index] += p.size
            new.append((packet_index, c.destination))
        if sum(usage) > instance.slot_bytes or any(
            u > c.capacity for u, c in zip(usage, contacts, strict=True)
        ):
            raise ValueError("shared transmission budget exceeded")
        total_bytes += sum(usage)
        for i, destination in new:
            if destination == instance.packets[i].destination:
                delivered.add(i)
                locations[i] = None
            else:
                locations[i] = destination
        for node, capacity in instance.buffers.items():
            occupancy = sum(
                instance.packets[i].size for i, location in enumerate(locations) if location == node
            )
            if occupancy > capacity:
                raise ValueError("buffer overflow")
    return dict(
        reward=sum(instance.packets[i].reward for i in delivered),
        delivered=sorted(delivered),
        transmitted_bytes=total_bytes,
    )
