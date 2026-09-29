"""Immutable campaign safety domains and version/epoch transition rules."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import ClassVar

from aidmesh.common.canonical import stable_hash
from aidmesh.common.errors import ConfigurationError, ValidationError


def _normalized_ids(values: tuple[str, ...], *, label: str) -> tuple[str, ...]:
    if any(not value for value in values):
        raise ConfigurationError(f"{label} identifiers must be non-empty")
    normalized = tuple(sorted(values))
    if len(normalized) != len(set(normalized)):
        raise ConfigurationError(f"{label} identifiers must be distinct")
    return normalized


@dataclass(frozen=True, order=True)
class SafetyDomain:
    """Fields included in every safety-relevant signature and state key."""

    campaign_id: str
    epoch: int
    header_version: int
    committee_id: str

    def __post_init__(self) -> None:
        if not self.campaign_id or not self.committee_id:
            raise ConfigurationError("campaign_id and committee_id are required")
        if self.epoch < 1 or self.header_version < 1:
            raise ConfigurationError("epoch and header_version must be positive")

    @property
    def digest(self) -> str:
        return stable_hash(self, domain="aidmesh/safety-domain/v1")

    @property
    def nullifier_scope(self) -> tuple[str, int]:
        """Scope freshness across all non-safety amendments in an epoch."""

        return self.campaign_id, self.epoch


@dataclass(frozen=True)
class CommitteeConfig:
    committee_id: str
    members: tuple[str, ...]
    threshold: int
    byzantine_bound: int

    def __post_init__(self) -> None:
        if not self.committee_id:
            raise ConfigurationError("committee_id is required")
        members = _normalized_ids(self.members, label="committee member")
        object.__setattr__(self, "members", members)
        if not 1 <= self.threshold <= len(members):
            raise ConfigurationError("threshold must be in [1, committee size]")
        if not 0 <= self.byzantine_bound < len(members):
            raise ConfigurationError("invalid Byzantine bound")

    @property
    def size(self) -> int:
        return len(self.members)

    @property
    def has_safe_intersection(self) -> bool:
        return 2 * self.threshold > self.size + self.byzantine_bound

    def require_safe_intersection(self) -> None:
        if not self.has_safe_intersection:
            raise ConfigurationError(
                "unsafe quorum: require 2 * threshold > committee size + Byzantine bound"
            )


@dataclass(frozen=True)
class CampaignHeader:
    """Signed campaign state represented as immutable, canonical values.

    Safety-critical values cannot change inside an epoch.  A metadata-only
    amendment increments ``header_version`` and consequently creates another
    immutable safety domain, while witnesses still key freshness by
    ``(campaign_id, epoch, nullifier)``.
    """

    domain: SafetyDomain
    risk_cell: str
    committee: CommitteeConfig
    authorized_sites: tuple[str, ...]
    fallback_categories: tuple[str, ...]
    initial_risk_rights: tuple[tuple[str, int], ...]
    start_tick: int
    end_tick: int
    verifier_id: str
    policy_hash: str
    metadata_hash: str = ""

    SAFETY_FIELDS: ClassVar[tuple[str, ...]] = (
        "risk_cell",
        "committee",
        "authorized_sites",
        "fallback_categories",
        "initial_risk_rights",
        "start_tick",
        "end_tick",
        "verifier_id",
        "policy_hash",
    )

    def __post_init__(self) -> None:
        if not self.risk_cell or not self.verifier_id or not self.policy_hash:
            raise ConfigurationError("risk cell, verifier id, and policy hash are required")
        if self.domain.committee_id != self.committee.committee_id:
            raise ConfigurationError("safety domain is bound to a different committee")
        sites = _normalized_ids(self.authorized_sites, label="site")
        categories = _normalized_ids(self.fallback_categories, label="category")
        object.__setattr__(self, "authorized_sites", sites)
        object.__setattr__(self, "fallback_categories", categories)
        if self.start_tick < 0 or self.end_tick <= self.start_tick:
            raise ConfigurationError("invalid campaign time interval")

        rights = tuple(sorted(self.initial_risk_rights))
        if len({site for site, _ in rights}) != len(rights):
            raise ConfigurationError("risk-right site appears more than once")
        unknown = {site for site, _ in rights} - set(sites)
        if unknown:
            raise ConfigurationError(f"risk rights assigned to unknown sites: {unknown}")
        if any(value < 0 for _, value in rights):
            raise ConfigurationError("risk rights cannot be negative")
        # A zero allocation is explicit so transfers cannot create an unlisted site.
        listed = {site for site, _ in rights}
        rights += tuple((site, 0) for site in sites if site not in listed)
        object.__setattr__(self, "initial_risk_rights", tuple(sorted(rights)))

    @property
    def safety_fingerprint(self) -> str:
        values = {name: getattr(self, name) for name in self.SAFETY_FIELDS}
        return stable_hash(values, domain="aidmesh/header-safety/v1")

    @property
    def header_hash(self) -> str:
        return stable_hash(self, domain="aidmesh/campaign-header/v1")

    @property
    def risk_rights(self) -> dict[str, int]:
        return dict(self.initial_risk_rights)

    def is_active(self, tick: int, *, clock_skew: int = 0) -> bool:
        if clock_skew < 0:
            raise ValueError("clock_skew cannot be negative")
        return self.start_tick - clock_skew <= tick <= self.end_tick + clock_skew

    def transition(
        self,
        *,
        epoch: int,
        header_version: int,
        committee: CommitteeConfig | None = None,
        risk_cell: str | None = None,
        authorized_sites: tuple[str, ...] | None = None,
        fallback_categories: tuple[str, ...] | None = None,
        initial_risk_rights: tuple[tuple[str, int], ...] | None = None,
        start_tick: int | None = None,
        end_tick: int | None = None,
        verifier_id: str | None = None,
        policy_hash: str | None = None,
        metadata_hash: str | None = None,
    ) -> CampaignHeader:
        """Create a successor, rejecting safety-critical same-epoch updates."""

        if epoch < self.domain.epoch:
            raise ValidationError("campaign epoch cannot move backwards")
        if epoch == self.domain.epoch and header_version <= self.domain.header_version:
            raise ValidationError("same-epoch header version must increase")
        if epoch > self.domain.epoch and header_version < 1:
            raise ValidationError("new epoch needs a positive header version")
        next_committee = committee or self.committee
        next_domain = SafetyDomain(
            campaign_id=self.domain.campaign_id,
            epoch=epoch,
            header_version=header_version,
            committee_id=next_committee.committee_id,
        )
        candidate = replace(
            self,
            domain=next_domain,
            committee=next_committee,
            risk_cell=self.risk_cell if risk_cell is None else risk_cell,
            authorized_sites=(
                self.authorized_sites if authorized_sites is None else authorized_sites
            ),
            fallback_categories=(
                self.fallback_categories if fallback_categories is None else fallback_categories
            ),
            initial_risk_rights=(
                self.initial_risk_rights if initial_risk_rights is None else initial_risk_rights
            ),
            start_tick=self.start_tick if start_tick is None else start_tick,
            end_tick=self.end_tick if end_tick is None else end_tick,
            verifier_id=self.verifier_id if verifier_id is None else verifier_id,
            policy_hash=self.policy_hash if policy_hash is None else policy_hash,
            metadata_hash=(self.metadata_hash if metadata_hash is None else metadata_hash),
        )
        if epoch == self.domain.epoch and candidate.safety_fingerprint != self.safety_fingerprint:
            raise ValidationError("safety-critical campaign updates require a strictly newer epoch")
        return candidate

    @staticmethod
    def validate_successor(previous: CampaignHeader, candidate: CampaignHeader) -> None:
        if previous.domain.campaign_id != candidate.domain.campaign_id:
            raise ValidationError("candidate belongs to another campaign")
        if candidate.domain.epoch < previous.domain.epoch:
            raise ValidationError("campaign epoch rollback")
        if candidate.domain.epoch == previous.domain.epoch:
            if candidate.domain.header_version <= previous.domain.header_version:
                raise ValidationError("header version rollback or replay")
            if candidate.safety_fingerprint != previous.safety_fingerprint:
                raise ValidationError("same-epoch safety-critical update")
