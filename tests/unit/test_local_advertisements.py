import pytest

from aidmesh.protocol.local_advertisements import LocalAdvertisementCache
from aidmesh.protocol.terminal_recovery import digest


def advertisement(sequence=1, balance=2):
    body = dict(
        profile="aidmesh/local-advertisement/v1",
        domain="D",
        epoch=1,
        site="a",
        sequence=sequence,
        balance=balance,
        reserve=1,
    )
    return dict(body=body, signature=digest(body))


@pytest.mark.parametrize("ttl", [1, 5, 20])
@pytest.mark.parametrize("replays", [0, 1, 10, 100])
def test_replays_cannot_refresh_stale_information(ttl, replays):
    cache = LocalAdvertisementCache(
        site="b", domain="D", epoch=1, members=("a", "b"), ttl=ttl, verify=lambda _, d, s: d == s
    )
    packet = advertisement()
    assert cache.ingest(packet, now=0)
    assert cache.select(now=0) == "a"
    initial_bytes = cache.control_bytes
    for _ in range(replays):
        assert not cache.ingest(packet, now=ttl)
    assert cache.select(now=ttl) is None
    assert cache.control_bytes == initial_bytes * (replays + 1)
    assert cache.ingest(advertisement(sequence=2, balance=1), now=ttl)
    assert cache.select(now=ttl) is None


def test_unreceived_remote_updates_do_not_change_local_decisions():
    def cache():
        return LocalAdvertisementCache(
            site="b", domain="D", epoch=1, members=("a", "b"), ttl=10, verify=lambda _, d, s: d == s
        )

    first, second = cache(), cache()
    first.ingest(advertisement(), now=0)
    second.ingest(advertisement(), now=0)
    pending_update = advertisement(sequence=2, balance=0)
    assert first.select(now=2) == second.select(now=2) == "a"
    # Only the run with a delivered update can change its decision.
    second.ingest(pending_update, now=2)
    assert first.select(now=3) == "a" and second.select(now=3) is None
    bad = advertisement(sequence=3)
    bad["body"]["balance"] = 100
    with pytest.raises(ValueError):
        second.ingest(bad, now=4)
