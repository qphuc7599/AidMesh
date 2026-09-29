from __future__ import annotations

import csv
from pathlib import Path

import pytest

from aidmesh.experiments.the_one_crosscheck import (
    export_the_one_scenario,
    parse_the_one_deliveries,
    parse_the_one_reports,
)


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_export_preserves_slot_order_and_hashes(tmp_path: Path) -> None:
    contacts = tmp_path / "contacts.csv"
    bundles = tmp_path / "bundles.csv"
    _write_csv(
        contacts,
        ["start_us", "end_us", "node_a", "node_b", "bytes_per_second"],
        [
            {
                "start_us": 10_000_000,
                "end_us": 30_000_000,
                "node_a": "a",
                "node_b": "b",
                "bytes_per_second": 256000,
            }
        ],
    )
    _write_csv(
        bundles,
        ["bundle_id", "source", "destination", "created_us", "ttl_us", "size_bytes", "risk_score"],
        [
            {
                "bundle_id": "m1",
                "source": "a",
                "destination": "b",
                "created_us": 10_000_000,
                "ttl_us": 86_400_000_000,
                "size_bytes": 2048,
                "risk_score": 0.5,
            }
        ],
    )

    assets = export_the_one_scenario(contacts, bundles, tmp_path / "out")

    lines = assets["events"].read_text(encoding="utf-8").splitlines()
    assert lines[0] == "0.000000 C m1 n0 n1 2048"
    assert lines[1] == "0.000000 CONN n0 n1 up"
    assert lines[2] == "20.000000 CONN n0 n1 down"
    assert "Scenario.simulateConnections = false" in assets["settings"].read_text()
    assert "Report.report3 = DeliveredMessagesReport" in assets["settings"].read_text()


def test_rejects_mixed_contact_speeds(tmp_path: Path) -> None:
    contacts = tmp_path / "contacts.csv"
    bundles = tmp_path / "bundles.csv"
    _write_csv(
        contacts,
        ["start_us", "end_us", "node_a", "node_b", "bytes_per_second"],
        [
            {
                "start_us": 0,
                "end_us": 1_000_000,
                "node_a": "a",
                "node_b": "b",
                "bytes_per_second": 1,
            },
            {
                "start_us": 2_000_000,
                "end_us": 3_000_000,
                "node_a": "a",
                "node_b": "b",
                "bytes_per_second": 2,
            },
        ],
    )
    _write_csv(
        bundles,
        ["bundle_id", "source", "destination", "created_us", "ttl_us", "size_bytes", "risk_score"],
        [
            {
                "bundle_id": "m1",
                "source": "a",
                "destination": "b",
                "created_us": 0,
                "ttl_us": 10_000_000,
                "size_bytes": 1,
                "risk_score": 1,
            }
        ],
    )
    with pytest.raises(ValueError, match="common contact speed"):
        export_the_one_scenario(contacts, bundles, tmp_path / "out")


def test_parse_reports(tmp_path: Path) -> None:
    (tmp_path / "x_MessageStatsReport.txt").write_text(
        "created: 4\ndelivered: 2\nrelayed: 5\ndropped: 1\n",
        encoding="utf-8",
    )
    (tmp_path / "x_MessageDelayReport.txt").write_text(
        "# messageDelay cumulativeProbability\n3600 0.25\n7200 0.50\n",
        encoding="utf-8",
    )
    metrics = parse_the_one_reports(tmp_path, message_size_bytes=2048)
    assert metrics.delivery_ratio == 0.5
    assert metrics.mean_delay_hours == 1.5
    assert metrics.p95_delay_hours == pytest.approx(1.95)
    assert metrics.forwarded_bytes == 10_240


def test_parse_message_level_delivery_report(tmp_path: Path) -> None:
    (tmp_path / "x_DeliveredMessagesReport.txt").write_text(
        "# time ID size hopcount deliveryTime from to remainingTtl isResponse path\n"
        "20.0 m1 2048 1 20.0 n0 n1 1439 N n0->n1\n",
        encoding="utf-8",
    )
    rows = parse_the_one_deliveries(tmp_path, origin_us=10_000_000)
    assert rows["m1"].delivered_us == 30_000_000
    assert rows["m1"].delay_us == 20_000_000
    assert rows["m1"].hops == 1
