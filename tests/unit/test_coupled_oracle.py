import pytest

from aidmesh.analysis.coupled_oracle import Contact, Instance, Packet, replay_schedule, solve_exact


def test_control_cost_and_causality_are_inside_oracle():
    packets = (Packet("b", "a", 1), Packet("a", "b", 1, 1, (0,)))
    slot = (Contact("a", "b", 2), Contact("b", "a", 2))
    one = Instance(packets, (slot,), {"a": 2, "b": 2}, 2)
    assert solve_exact(one)["optimal_reward"] == 0
    with pytest.raises(ValueError, match="noncausal"):
        replay_schedule(one, (((0, 1), (1, 0)),))
    two = Instance(packets, (slot, slot), {"a": 2, "b": 2}, 1)
    optimum = solve_exact(two)
    assert optimum["optimal_reward"] == 1
    assert optimum["replay"]["transmitted_bytes"] == 2


def test_shared_budget_buffer_and_relay_schedule():
    packets = (Packet("a", "c", 2, 1), Packet("a", "c", 2, 1))
    contacts = ((Contact("a", "b", 4),), (Contact("b", "c", 4),))
    problem = Instance(packets, contacts, {"a": 4, "b": 2, "c": 0}, 4)
    assert solve_exact(problem)["optimal_reward"] == 1
    with pytest.raises(ValueError, match="buffer"):
        replay_schedule(problem, (((0, 0), (1, 0)), ()))
    problem = Instance(packets, contacts, {"a": 4, "b": 4, "c": 0}, 2)
    assert solve_exact(problem)["optimal_reward"] == 1
    with pytest.raises(ValueError, match="budget"):
        replay_schedule(problem, (((0, 0), (1, 0)), ()))


def test_search_limit_never_returns_false_optimality():
    problem = Instance((Packet("a", "b", 1, 1),), ((Contact("a", "b", 1),),), {"a": 1, "b": 0}, 1)
    with pytest.raises(RuntimeError):
        solve_exact(problem, max_states=0)
