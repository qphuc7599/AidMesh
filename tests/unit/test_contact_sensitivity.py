from aidmesh.experiments.bow_temporal import perturb_contacts_for_sensitivity
from aidmesh.simulator.dtn import Contact


def test_contact_sensitivity_is_deterministic_and_preserves_duration() -> None:
    contacts = (
        Contact(1_000_000, 3_000_000, "a", "b", 2_000),
        Contact(4_000_000, 7_000_000, "b", "c", 4_000),
    )
    first = perturb_contacts_for_sensitivity(
        contacts,
        context="ctx",
        temporal_jitter_us=500_000,
        contact_capacity_scale=0.25,
    )
    second = perturb_contacts_for_sensitivity(
        contacts,
        context="ctx",
        temporal_jitter_us=500_000,
        contact_capacity_scale=0.25,
    )

    assert first == second
    assert sorted(row.end_us - row.start_us for row in first) == [2_000_000, 3_000_000]
    assert sorted(row.bytes_per_second for row in first) == [500, 1_000]


def test_contact_sensitivity_rejects_invalid_parameters() -> None:
    contacts = (Contact(0, 1, "a", "b", 1),)

    try:
        perturb_contacts_for_sensitivity(
            contacts,
            context="ctx",
            temporal_jitter_us=-1,
        )
    except ValueError as exc:
        assert "nonnegative" in str(exc)
    else:
        raise AssertionError("negative jitter should fail")

    try:
        perturb_contacts_for_sensitivity(
            contacts,
            context="ctx",
            contact_capacity_scale=0,
        )
    except ValueError as exc:
        assert "positive" in str(exc)
    else:
        raise AssertionError("nonpositive capacity scale should fail")
