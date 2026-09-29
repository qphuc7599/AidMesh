from __future__ import annotations

import pytest

from aidmesh.simulator.dtn import load_bundles_csv, load_contacts_csv


def test_load_contacts_and_bundles_csv(tmp_path) -> None:
    contacts = tmp_path / "contacts.csv"
    contacts.write_text(
        "start_us,end_us,node_a,node_b,bytes_per_second\n"
        "0,1000000,site-1,relay-1,12000\n",
        encoding="utf-8",
    )
    bundles = tmp_path / "bundles.csv"
    bundles.write_text(
        "bundle_id,source,destination,created_us,ttl_us,size_bytes,risk_score\n"
        "b1,site-1,contract,0,3600000000,1024,0.7\n",
        encoding="utf-8",
    )

    loaded_contacts = load_contacts_csv(contacts)
    loaded_bundles = load_bundles_csv(bundles)

    assert loaded_contacts[0].capacity_bytes == 12000
    assert loaded_bundles[0].risk_score == 0.7


def test_load_contacts_rejects_missing_columns(tmp_path) -> None:
    contacts = tmp_path / "contacts.csv"
    contacts.write_text("start_us,end_us,node_a,node_b\n", encoding="utf-8")

    with pytest.raises(ValueError, match="missing columns"):
        load_contacts_csv(contacts)


def test_load_bundles_rejects_duplicate_ids(tmp_path) -> None:
    bundles = tmp_path / "bundles.csv"
    bundles.write_text(
        "bundle_id,source,destination,created_us,ttl_us,size_bytes,risk_score\n"
        "b1,site-1,contract,0,3600000000,1024,0.7\n"
        "b1,site-2,contract,0,3600000000,1024,0.6\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate"):
        load_bundles_csv(bundles)
