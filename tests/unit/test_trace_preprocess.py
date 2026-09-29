from __future__ import annotations

import csv
import json
from pathlib import Path

from aidmesh.experiments.trace_preprocess import (
    AsturiesWildfireConversionConfig,
    Ene5aiConversionConfig,
    TraceConversionConfig,
    normalize_asturies_wildfire_contacts,
    normalize_ene5ai_contacts,
    normalize_sociopatterns_contacts,
    write_asturies_wildfire_trace,
    write_ene5ai_trace,
    write_sociopatterns_trace,
)


def test_sociopatterns_converter_collapses_samples_and_records_overlay(tmp_path: Path) -> None:
    source = tmp_path / "tij.dat"
    source.write_text("0 b a\n20 a b\n40 a c\n60 c a\n", encoding="utf-8")
    config = TraceConversionConfig(
        context="test",
        bytes_per_second=1000,
        site_count=1,
        relay_count=1,
        bundles_per_client=2,
        ttl_hours=1,
        bundle_size_bytes=128,
        collapse_contiguous=True,
    )

    contacts = normalize_sociopatterns_contacts(source, config=config)

    assert len(contacts) == 2
    assert contacts[0].start_us == 0
    assert contacts[0].end_us == 40_000_000
    assets = write_sociopatterns_trace(source, config=config, output=tmp_path / "out")
    rows = list(csv.DictReader(assets["contacts"].open(encoding="utf-8")))
    provenance = json.loads(assets["provenance"].read_text(encoding="utf-8"))

    assert len(rows) == 2
    assert assets["bundles"].exists()
    assert provenance["context"] == "test"
    assert "evaluation overlay" in provenance["bundle_rule"]


def test_ene5ai_converter_keeps_only_direct_evidenced_wifi_edges(tmp_path: Path) -> None:
    source = tmp_path / "all.csv"
    source.write_text(
        "timestamp,source,target,connected-time,signal-avg,rx-bytes,tx-bytes,type\n"
        "100,a,b,10,-50,100,300,local\n"
        "100,b,a,10,-49,400,200,local+global\n"
        "100,a,c,,,900,900,global\n"
        "101,a,b,11,-48,,,local\n"
        "101,b,c,12,-60,1000,1000,cell\n"
        "102,a,c,1,-70,100,100,local\n"
        "103,c,d,,,1000,1000,local\n",
        encoding="utf-8",
    )
    config = Ene5aiConversionConfig(
        context="ene",
        minimum_bytes_per_second=2048,
        site_count=1,
        relay_count=1,
        bundles_per_client=1,
    )
    contacts = normalize_ene5ai_contacts(source, config=config)
    assert len(contacts) == 3
    assert contacts[0].start_us == 0
    assert contacts[0].bytes_per_second == 2048
    assets = write_ene5ai_trace(source, config=config, output=tmp_path / "out")
    provenance = json.loads(assets["provenance"].read_text(encoding="utf-8"))
    assert provenance["dataset_doi"] == "10.57745/DFMCYS"
    assert provenance["normalized_contacts"] == 3


def test_asturies_converter_uses_registered_metric_range(tmp_path: Path) -> None:
    source = tmp_path / "WF1_one.csv"
    source.write_text(
        "0 30 0 1000 0 1000\n0 0 0 0\n0 1 100 0\n0 2 500 0\n30 0 0 0\n30 1 250 0\n30 2 150 0\n",
        encoding="utf-8",
    )
    config = AsturiesWildfireConversionConfig(
        context="wf01",
        communication_range_m=200,
        snapshot_seconds=30,
        bytes_per_second=1000,
    )
    contacts = normalize_asturies_wildfire_contacts(source, config=config)
    assert [(row.start_us, row.node_a, row.node_b) for row in contacts] == [
        (0, "ast-0", "ast-1"),
        (30_000_000, "ast-0", "ast-2"),
        (30_000_000, "ast-1", "ast-2"),
    ]
    assets = write_asturies_wildfire_trace(
        source,
        config=config,
        output=tmp_path / "out",
        upstream_commit="abc123",
    )
    provenance = json.loads(assets["provenance"].read_text(encoding="utf-8"))
    assert provenance["citation_doi"] == "10.1109/ACCESS.2017.2778347"
    assert provenance["normalized_contacts"] == 3


def test_asturies_converter_coalesces_consecutive_pair_snapshots(tmp_path: Path) -> None:
    source = tmp_path / "WF2_one.csv"
    source.write_text(
        "0 60 0 1000 0 1000\n0 0 0 0\n0 1 100 0\n30 0 0 0\n30 1 100 0\n60 0 0 0\n60 1 500 0\n",
        encoding="utf-8",
    )
    contacts = normalize_asturies_wildfire_contacts(
        source,
        config=AsturiesWildfireConversionConfig(context="wf02"),
    )
    assert len(contacts) == 1
    assert contacts[0].start_us == 0
    assert contacts[0].end_us == 60_000_000
