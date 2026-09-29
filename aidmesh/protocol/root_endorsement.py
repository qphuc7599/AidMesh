"""Threshold endorsement of a locally approved registry/root statement.

Endorsers must obtain `expected` by validating their own approved snapshot and
rebuilding roots. Merely receiving an untrusted root is not approval. This profile
authenticates off-chain publication; it does not replace EVM header registration.
"""

from dataclasses import asdict, dataclass

from aidmesh.crypto.coconut_admission import P1_FIELD
from aidmesh.protocol.terminal_recovery import digest
from aidmesh.protocol.witness import PersistOutcome


@dataclass(frozen=True)
class RootStatement:
    campaign: int
    epoch: int
    deployment_context: int
    credential_root: int
    entitlement_root: int
    allocation_digest: str
    snapshot_digest: str
    circuit_digest: str
    committee_digest: str

    def __post_init__(self):
        for name in (
            "campaign",
            "epoch",
            "deployment_context",
            "credential_root",
            "entitlement_root",
        ):
            value = getattr(self, name)
            if type(value) is not int or not 0 <= value < P1_FIELD:
                raise ValueError("noncanonical root field")
        if self.epoch == 0:
            raise ValueError("epoch must be positive")
        for name in ("allocation_digest", "snapshot_digest", "circuit_digest", "committee_digest"):
            value = getattr(self, name)
            if not isinstance(value, str) or len(value) != 64:
                raise ValueError("expected SHA-256 digest")
            try:
                if bytes.fromhex(value).hex() != value:
                    raise ValueError("noncanonical digest")
            except ValueError as error:
                raise ValueError("invalid digest") from error

    @property
    def digest(self):
        return digest(dict(profile="aidmesh/root-endorsement/v1", statement=asdict(self)))


def committee_digest(members, threshold, faults):
    if (
        not members
        or len(set(members)) != len(members)
        or type(faults) is not int
        or faults < 0
        or type(threshold) is not int
        or not 1 <= threshold <= len(members) - faults
        or 2 * threshold <= len(members) + faults
    ):
        raise ValueError("unsafe root committee")
    return digest(dict(members=sorted(members), threshold=threshold, faults=faults))


class RootEndorser:
    def __init__(self, member, expected: RootStatement, store, sign):
        self.member, self.expected, self.store, self.sign = member, expected, store, sign

    def endorse(self, candidate: RootStatement):
        if candidate != self.expected:
            raise ValueError("root differs from locally approved reconstruction")
        slot = (
            f"root:{candidate.campaign}:{candidate.deployment_context}",
            candidate.epoch,
            "root-endorsement/v1",
        )
        if self.store.persist_once(slot, candidate.digest) is PersistOutcome.CONFLICT:
            raise ValueError("root already endorsed differently for this epoch")
        signature = self.sign(self.member, candidate.digest)
        if not signature:
            raise ValueError("empty root signature")
        return dict(member=self.member, digest=candidate.digest, signature=signature)


def verify_root_endorsement(statement, shares, *, members, threshold, faults, verify):
    if statement.committee_digest != committee_digest(members, threshold, faults):
        raise ValueError("wrong root committee")
    if len(shares) < threshold:
        raise ValueError("insufficient root endorsements")
    seen = set()
    for share in shares:
        if (
            set(share) != {"member", "digest", "signature"}
            or share["member"] not in members
            or share["member"] in seen
            or share["digest"] != statement.digest
            or not verify(share["member"], share["digest"], share["signature"])
        ):
            raise ValueError("invalid root endorsement")
        seen.add(share["member"])
    return True
