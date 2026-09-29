import pytest

from aidmesh.analysis.prefix_exposure import audit_prefix_exposure


def history():
    return [
        dict(kind="cap", cap_id="epoch1", amount=10),
        dict(kind="authorize", release_id="q", entitlement="e", value=10, mode="quorum"),
        dict(kind="intent", release_id="q"),
        dict(kind="ambiguous", release_id="q"),
        dict(kind="epoch_boundary"),
        dict(
            kind="authorize",
            release_id="f",
            entitlement="e",
            value=10,
            mode="fallback",
            debit_id="d",
            cap_id="epoch1",
            charged=10,
        ),
        dict(kind="override", release_id="f"),
        dict(kind="delivered", release_id="f"),
        dict(kind="delivered", release_id="q"),
    ]


def audit(events):
    return audit_prefix_exposure([dict(e, event_id=str(i)) for i, e in enumerate(events)])


def test_missing_receipt_ambiguity_and_epoch_do_not_erase_obligation():
    rows = audit(history())
    assert rows[6]["conservative_duplicate_value"] == 10
    assert rows[6]["physical_duplicate_value"] == 0
    assert rows[-1]["physical_duplicate_value"] == rows[-1]["charged"] == 10


@pytest.mark.parametrize(
    "mutation", ["undercharge", "second_actuation", "uncharged_override", "remint", "value_change"]
)
def test_unsafe_histories_are_rejected(mutation):
    events = history()
    if mutation == "undercharge":
        events[5]["charged"] = 9
    elif mutation == "second_actuation":
        events.append(dict(kind="delivered", release_id="f"))
    elif mutation == "uncharged_override":
        events[5]["mode"] = "quorum"
    elif mutation == "remint":
        events.append(dict(kind="cap", cap_id="epoch1", amount=10))
    else:
        events[5]["value"] = 9
    with pytest.raises(ValueError):
        audit(events)
