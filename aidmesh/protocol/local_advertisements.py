"""Signed, expiring local observations for selecting a donor to ask.

An advertisement is never permission to spend remote rights. The receiver may
request at most one unit, then the donor must atomically check its current reserve.
Freshness uses local receipt time; sender timestamps cannot extend this TTL.
"""

from dataclasses import dataclass

from aidmesh.protocol.terminal_recovery import canonical, digest


@dataclass(frozen=True)
class Observation:
    sequence: int
    balance: int
    reserve: int
    received_at: int


class LocalAdvertisementCache:
    def __init__(self, *, site, domain, epoch, members, ttl, verify):
        if type(ttl) is not int or ttl <= 0 or site not in members:
            raise ValueError("invalid local cache configuration")
        self.site, self.domain, self.epoch, self.members = site, domain, epoch, tuple(members)
        self.ttl, self.verify = ttl, verify
        self.observations = {}
        self.control_bytes = 0
        self.last_time = -1

    def ingest(self, envelope, *, now):
        self._clock(now)
        self.control_bytes += len(canonical(envelope).encode())  # include invalid/replayed traffic
        if set(envelope) != {"body", "signature"}:
            raise ValueError("advertisement schema")
        body = envelope["body"]
        if set(body) != {"profile", "domain", "epoch", "site", "sequence", "balance", "reserve"}:
            raise ValueError("advertisement body")
        if (
            body["profile"] != "aidmesh/local-advertisement/v1"
            or body["domain"] != self.domain
            or type(body["epoch"]) is not int
            or body["epoch"] != self.epoch
            or body["site"] not in self.members
            or body["site"] == self.site
            or any(
                type(body[k]) is not int or body[k] < 0 for k in ("sequence", "balance", "reserve")
            )
            or not self.verify(body["site"], digest(body), envelope["signature"])
        ):
            raise ValueError("unauthenticated advertisement")
        previous = self.observations.get(body["site"])
        if previous and body["sequence"] <= previous.sequence:
            return False  # duplicate delivery MUST NOT refresh age
        self.observations[body["site"]] = Observation(
            body["sequence"], body["balance"], body["reserve"], now
        )
        return True

    def _clock(self, now):
        if type(now) is not int or now < self.last_time:
            raise ValueError("nonmonotone local clock")
        self.last_time = now

    def select(self, *, now):
        self._clock(now)
        candidates = [
            (o.balance - o.reserve, -(now - o.received_at), site)
            for site, o in self.observations.items()
            if now - o.received_at < self.ttl and o.balance > o.reserve
        ]
        if not candidates:
            return None
        return max(candidates)[2]

    def export_observations(self):
        return {
            site: dict(
                sequence=o.sequence, balance=o.balance, reserve=o.reserve, received_at=o.received_at
            )
            for site, o in sorted(self.observations.items())
        }
