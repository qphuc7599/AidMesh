"""Metadata privacy metrics for AidMesh redemption traces."""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class MetadataObservation:
    epoch: int
    time_bucket: int
    risk_cell: str
    category: str
    path: str
    fallback_tag: str | None = None
    size_class: str = "unspecified"
    vantage: str = "ledger"
    is_dummy: bool = False


@dataclass(frozen=True)
class PrivacyReport:
    observations: int
    buckets: int
    min_anonymity_set: int
    mean_anonymity_set: float
    unique_bucket_fraction: float
    shannon_entropy_bits: float
    posterior_entropy_bits: float
    attacker_top1_success: float
    attacker_advantage_over_global: float
    fallback_observations: int
    repeated_fallback_tag_fraction: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def privacy_report(observations: list[MetadataObservation]) -> PrivacyReport:
    if not observations:
        return PrivacyReport(0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0.0)
    buckets = Counter(
        (
            item.epoch,
            item.time_bucket,
            item.risk_cell,
            item.category,
            item.path,
            item.fallback_tag,
            item.size_class,
        )
        for item in observations
    )
    counts = list(buckets.values())
    total = len(observations)
    entropy = -sum((count / total) * math.log2(count / total) for count in counts)
    real_observations = [item for item in observations if not item.is_dummy]
    target_observations = real_observations or observations
    posterior_sizes = [
        buckets[
            (
                item.epoch,
                item.time_bucket,
                item.risk_cell,
                item.category,
                item.path,
                item.fallback_tag,
                item.size_class,
            )
        ]
        for item in target_observations
    ]
    posterior_entropy = sum(math.log2(size) for size in posterior_sizes) / len(posterior_sizes)
    top1_success = sum(1 / size for size in posterior_sizes) / len(posterior_sizes)
    fallback_tags = [
        item.fallback_tag
        for item in target_observations
        if item.path == "fallback" and item.fallback_tag is not None
    ]
    tag_counts = Counter(fallback_tags)
    repeated = sum(count for count in tag_counts.values() if count > 1)
    return PrivacyReport(
        observations=total,
        buckets=len(buckets),
        min_anonymity_set=min(counts),
        mean_anonymity_set=total / len(buckets),
        unique_bucket_fraction=sum(1 for count in counts if count == 1) / len(buckets),
        shannon_entropy_bits=entropy,
        posterior_entropy_bits=posterior_entropy,
        attacker_top1_success=top1_success,
        attacker_advantage_over_global=max(0.0, top1_success - 1 / total),
        fallback_observations=len(fallback_tags),
        repeated_fallback_tag_fraction=(repeated / len(fallback_tags) if fallback_tags else 0.0),
    )


def bucketed_observation(
    *,
    created_us: int,
    epoch: int,
    risk_cell: str,
    category: str,
    path: str,
    bucket_us: int,
    fallback_tag: str | None = None,
    size_class: str = "unspecified",
    vantage: str = "ledger",
    is_dummy: bool = False,
) -> MetadataObservation:
    if bucket_us <= 0:
        raise ValueError("bucket_us must be positive")
    return MetadataObservation(
        epoch=epoch,
        time_bucket=created_us // bucket_us,
        risk_cell=risk_cell,
        category=category,
        path=path,
        fallback_tag=fallback_tag,
        size_class=size_class,
        vantage=vantage,
        is_dummy=is_dummy,
    )
