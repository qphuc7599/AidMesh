from __future__ import annotations

from aidmesh.analysis.privacy import MetadataObservation, privacy_report


def test_privacy_report_computes_k_anonymity_and_linkability() -> None:
    observations = [
        MetadataObservation(1, 0, "cell-a", "food", "fallback", "tag-1"),
        MetadataObservation(1, 0, "cell-a", "food", "fallback", "tag-1"),
        MetadataObservation(1, 1, "cell-b", "water", "quorum", None),
    ]

    report = privacy_report(observations)

    assert report.observations == 3
    assert report.buckets == 2
    assert report.min_anonymity_set == 1
    assert report.unique_bucket_fraction == 0.5
    assert report.repeated_fallback_tag_fraction == 1.0
