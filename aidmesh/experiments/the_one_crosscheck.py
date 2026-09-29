"""Conversion and comparison helpers for the external The ONE DTN simulator."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from aidmesh.simulator.dtn import (
    RoutingPolicy,
    TemporalContactSimulator,
    load_bundles_csv,
    load_contacts_csv,
)

THE_ONE_COMMIT = "c973bfb892ee3d624549a0142018f4d1dedf17d7"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    index = (len(ordered) - 1) * q
    lower = math.floor(index)
    upper = math.ceil(index)
    weight = index - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def export_the_one_scenario(
    contacts_path: Path,
    bundles_path: Path,
    output: Path,
    *,
    buffer_bytes: int = 96 * 1024,
    update_interval_seconds: float = 20.0,
) -> dict[str, Path]:
    """Map a temporal-contact CSV workload into The ONE external-event format."""

    contacts = load_contacts_csv(contacts_path)
    bundles = load_bundles_csv(bundles_path)
    nodes = sorted(
        {node for contact in contacts for node in (contact.node_a, contact.node_b)}
        | {node for bundle in bundles for node in (bundle.source, bundle.destination)}
    )
    address = {node: index for index, node in enumerate(nodes)}
    origin_us = min(
        [contact.start_us for contact in contacts]
        + [bundle.created_us for bundle in bundles]
    )
    max_end_us = max(
        [contact.end_us for contact in contacts]
        + [bundle.expires_us for bundle in bundles]
    )
    speeds = {contact.bytes_per_second for contact in contacts}
    if len(speeds) != 1:
        raise ValueError("The ONE simple interface requires one common contact speed")
    speed = speeds.pop()

    # Down events precede message creation and up events at the same instant.
    # This preserves [start,end) slots and permits a message created at start.
    events: list[tuple[float, int, str]] = []
    for contact in contacts:
        start = (contact.start_us - origin_us) / 1_000_000
        end = (contact.end_us - origin_us) / 1_000_000
        a, b = address[contact.node_a], address[contact.node_b]
        events.append((start, 2, f"{start:.6f} CONN n{a} n{b} up"))
        events.append((end, 0, f"{end:.6f} CONN n{a} n{b} down"))
    for bundle in bundles:
        created = (bundle.created_us - origin_us) / 1_000_000
        source, destination = address[bundle.source], address[bundle.destination]
        events.append(
            (
                created,
                1,
                f"{created:.6f} C {bundle.bundle_id} n{source} n{destination} "
                f"{bundle.size_bytes}",
            )
        )

    output.mkdir(parents=True, exist_ok=True)
    events_path = output / "events.txt"
    events_path.write_text(
        "\n".join(item[2] for item in sorted(events, key=lambda item: (item[0], item[1], item[2])))
        + "\n",
        encoding="utf-8",
    )
    mapping_path = output / "node_mapping.json"
    mapping_path.write_text(
        json.dumps({"source_to_the_one_address": address}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    end_seconds = math.ceil((max_end_us - origin_us) / 1_000_000) + 20
    settings_path = output / "aidmesh_invs13_settings.txt"
    settings_path.write_text(
        "\n".join(
            [
                "Scenario.name = aidmesh_invs13_external_crosscheck",
                "Scenario.simulateConnections = false",
                f"Scenario.updateInterval = {update_interval_seconds:g}",
                f"Scenario.endTime = {end_seconds}",
                "Scenario.nrofHostGroups = 1",
                "traceInterface.type = SimpleBroadcastInterface",
                f"traceInterface.transmitSpeed = {speed}",
                "traceInterface.transmitRange = 1",
                "Group.groupID = n",
                f"Group.nrofHosts = {len(nodes)}",
                "Group.movementModel = StationaryMovement",
                "Group.nodeLocation = 0,0",
                "Group.router = EpidemicRouter",
                f"Group.bufferSize = {buffer_bytes}",
                "Group.nrofInterfaces = 1",
                "Group.interface1 = traceInterface",
                "Group.msgTtl = 1440",
                "Events.nrof = 1",
                f"Events1.filePath = {events_path.resolve().as_posix()}",
                "Events1.nrofPreload = 20000",
                "MovementModel.rngSeed = 1",
                "MovementModel.worldSize = 10,10",
                "MovementModel.warmup = 0",
                "Report.nrofReports = 3",
                "Report.warmup = 0",
                f"Report.reportDir = {(output / 'reports').resolve().as_posix()}",
                "Report.report1 = MessageStatsReport",
                "Report.report2 = MessageDelayReport",
                "Report.report3 = DeliveredMessagesReport",
                "Optimization.randomizeUpdateOrder = false",
                "",
            ]
        ),
        encoding="utf-8",
    )
    manifest_path = output / "input_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "external_simulator": "The ONE",
                "the_one_commit": THE_ONE_COMMIT,
                "contacts": {"path": str(contacts_path), "sha256": _sha256(contacts_path)},
                "bundles": {"path": str(bundles_path), "sha256": _sha256(bundles_path)},
                "nodes": len(nodes),
                "contacts_count": len(contacts),
                "bundles_count": len(bundles),
                "buffer_bytes": buffer_bytes,
                "update_interval_seconds": update_interval_seconds,
                "bytes_per_second": speed,
                "origin_us": origin_us,
                "semantic_contract": {
                    "contact_interval": "[start,end)",
                    "routing": "Epidemic",
                    "ttl_minutes": 1440,
                    "message_size": "from bundles.csv",
                    "connection_speed": "single common trace speed",
                    "router_scheduler": (
                        "The ONE permits one active transfer per connection/router update; "
                        "update interval is an explicit sensitivity variable"
                    ),
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "events": events_path,
        "settings": settings_path,
        "mapping": mapping_path,
        "manifest": manifest_path,
    }


def run_the_one(the_one_root: Path, settings_path: Path) -> None:
    """Compile and run a pinned The ONE checkout in batch mode."""

    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=the_one_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if commit != THE_ONE_COMMIT:
        raise ValueError(f"The ONE commit mismatch: expected {THE_ONE_COMMIT}, got {commit}")
    subprocess.run(["cmd", "/c", "compile.bat"], cwd=the_one_root, check=True)
    subprocess.run(
        ["cmd", "/c", "one.bat", "-b", "1", str(settings_path.resolve())],
        cwd=the_one_root,
        check=True,
    )


@dataclass(frozen=True)
class CrosscheckMetrics:
    implementation: str
    bundles: int
    delivered: int
    delivery_ratio: float
    mean_delay_hours: float | None
    p95_delay_hours: float | None
    forwarded_bytes: int
    dropped: int


@dataclass(frozen=True)
class MessageDelivery:
    bundle_id: str
    delivered_us: int
    delay_us: int
    hops: int


def parse_the_one_deliveries(report_dir: Path, *, origin_us: int) -> dict[str, MessageDelivery]:
    """Parse the pinned simulator's native per-message first-delivery report."""

    paths = list(report_dir.glob("*_DeliveredMessagesReport.txt"))
    if len(paths) != 1:
        raise ValueError("expected exactly one DeliveredMessagesReport")
    rows: dict[str, MessageDelivery] = {}
    for line_number, raw in enumerate(
        paths[0].read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not raw or raw.startswith("#"):
            continue
        fields = raw.split()
        if len(fields) < 5:
            raise ValueError(f"malformed DeliveredMessagesReport line {line_number}")
        bundle_id = fields[1]
        if bundle_id in rows:
            raise ValueError(f"duplicate first-delivery record for {bundle_id}")
        rows[bundle_id] = MessageDelivery(
            bundle_id=bundle_id,
            delivered_us=origin_us + round(float(fields[0]) * 1_000_000),
            delay_us=round(float(fields[4]) * 1_000_000),
            hops=int(fields[3]),
        )
    return rows


def _parse_key_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        if ":" in raw:
            key, value = raw.split(":", 1)
            values[key.strip()] = value.strip()
    return values


def parse_the_one_reports(report_dir: Path, *, message_size_bytes: int) -> CrosscheckMetrics:
    """Parse The ONE's aggregate and delay reports into comparable metrics."""

    stats_paths = list(report_dir.glob("*_MessageStatsReport.txt"))
    delay_paths = list(report_dir.glob("*_MessageDelayReport.txt"))
    if len(stats_paths) != 1 or len(delay_paths) != 1:
        raise ValueError("expected exactly one MessageStatsReport and MessageDelayReport")
    stats = _parse_key_values(stats_paths[0])
    delays_seconds: list[float] = []
    for raw in delay_paths[0].read_text(encoding="utf-8").splitlines():
        if raw and not raw.startswith("#"):
            delays_seconds.append(float(raw.split()[0]))
    created = int(stats["created"])
    delivered = int(stats["delivered"])
    relayed = int(stats["relayed"])
    return CrosscheckMetrics(
        implementation="the_one_external_epidemic",
        bundles=created,
        delivered=delivered,
        delivery_ratio=delivered / created if created else 0.0,
        mean_delay_hours=(
            sum(delays_seconds) / len(delays_seconds) / 3600 if delays_seconds else None
        ),
        p95_delay_hours=(
            (_quantile(delays_seconds, 0.95) or 0.0) / 3600 if delays_seconds else None
        ),
        forwarded_bytes=relayed * message_size_bytes,
        dropped=int(stats["dropped"]),
    )


def compare_with_internal(
    contacts_path: Path,
    bundles_path: Path,
    the_one_metrics: CrosscheckMetrics,
    output: Path,
    *,
    buffer_bytes: int = 96 * 1024,
) -> Path:
    """Run the repository Epidemic implementation and write an honest comparison."""

    contacts = load_contacts_csv(contacts_path)
    bundles = load_bundles_csv(bundles_path)
    internal = TemporalContactSimulator(
        contacts=contacts,
        bundles=bundles,
        policy=RoutingPolicy.EPIDEMIC,
        buffer_bytes=buffer_bytes,
        copy_budget=8,
    ).run()
    internal_metrics = CrosscheckMetrics(
        implementation="aidmesh_temporal_contact_epidemic",
        bundles=internal.bundles,
        delivered=internal.delivered,
        delivery_ratio=internal.delivery_ratio,
        mean_delay_hours=internal.mean_delay_hours,
        p95_delay_hours=internal.p95_delay_hours,
        forwarded_bytes=internal.forwarded_bytes,
        dropped=internal.dropped,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(asdict(internal_metrics))
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow(asdict(internal_metrics))
        writer.writerow(asdict(the_one_metrics))
    return output


def compare_message_level(
    contacts_path: Path,
    bundles_path: Path,
    report_dir: Path,
    output: Path,
    *,
    origin_us: int,
    buffer_bytes: int = 96 * 1024,
) -> tuple[Path, dict[str, int | float | str]]:
    """Retain one row for every message and classify all delivery differences."""

    contacts = load_contacts_csv(contacts_path)
    bundles = load_bundles_csv(bundles_path)
    result = TemporalContactSimulator(
        contacts=contacts,
        bundles=bundles,
        policy=RoutingPolicy.EPIDEMIC,
        buffer_bytes=buffer_bytes,
        copy_budget=8,
    ).run()
    internal = {row.bundle_id: row for row in result.deliveries}
    external = parse_the_one_deliveries(report_dir, origin_us=origin_us)
    rows: list[dict[str, object]] = []
    counts = {"both": 0, "aidmesh_only": 0, "the_one_only": 0, "neither": 0}
    common_deltas: list[int] = []
    for bundle in bundles:
        left = internal.get(bundle.bundle_id)
        right = external.get(bundle.bundle_id)
        if left is not None and right is not None:
            classification = "both"
            delta = left.delivered_us - right.delivered_us
            common_deltas.append(abs(delta))
        elif left is not None:
            classification, delta = "aidmesh_only", None
        elif right is not None:
            classification, delta = "the_one_only", None
        else:
            classification, delta = "neither", None
        counts[classification] += 1
        rows.append(
            {
                "bundle_id": bundle.bundle_id,
                "classification": classification,
                "created_us": bundle.created_us,
                "expires_us": bundle.expires_us,
                "source": bundle.source,
                "destination": bundle.destination,
                "aidmesh_delivered_us": left.delivered_us if left else None,
                "the_one_delivered_us": right.delivered_us if right else None,
                "delivery_delta_us": delta,
                "aidmesh_hops": left.hops if left else None,
                "the_one_hops": right.hops if right else None,
            }
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    summary: dict[str, int | float | str] = {
        "bundles": len(bundles),
        "both_delivered": counts["both"],
        "aidmesh_only": counts["aidmesh_only"],
        "the_one_only": counts["the_one_only"],
        "neither": counts["neither"],
        "delivery_agreement_fraction": (
            counts["both"] + counts["neither"]
        ) / max(1, len(bundles)),
        "mean_absolute_common_delivery_delta_hours": (
            sum(common_deltas) / len(common_deltas) / 3_600_000_000
            if common_deltas
            else 0.0
        ),
        "status": "resolved_message_level_measurement",
    }
    return output, summary


def run_the_one_discrepancy_sweep(
    the_one_root: Path,
    contacts_path: Path,
    bundles_path: Path,
    output: Path,
    *,
    update_intervals: tuple[float, ...] = (20.0, 5.0, 1.0),
    buffer_sizes: tuple[int, ...] = (96 * 1024, 1024 * 1024 * 1024),
) -> Path:
    """Run a registered scheduler/buffer factorial with per-message classification."""

    bundles = load_bundles_csv(bundles_path)
    contacts = load_contacts_csv(contacts_path)
    from aidmesh.experiments.dtn_crosscheck import (
        earliest_intraslot_closure_delivery,
        earliest_temporal_delivery,
    )

    slot_oracle = {
        bundle.bundle_id
        for bundle in bundles
        if earliest_temporal_delivery(contacts, bundle).reachable
    }
    closure_oracle = {
        bundle.bundle_id
        for bundle in bundles
        if earliest_intraslot_closure_delivery(contacts, bundle).reachable
    }
    if len({bundle.size_bytes for bundle in bundles}) != 1:
        raise ValueError("aggregate The ONE byte accounting requires fixed bundle size")
    message_size = bundles[0].size_bytes
    rows: list[dict[str, object]] = []
    for update_interval in update_intervals:
        for buffer_bytes in buffer_sizes:
            label = f"dt-{update_interval:g}_buffer-{buffer_bytes}"
            case_root = output / label
            assets = export_the_one_scenario(
                contacts_path,
                bundles_path,
                case_root,
                buffer_bytes=buffer_bytes,
                update_interval_seconds=update_interval,
            )
            run_the_one(the_one_root, assets["settings"])
            manifest = json.loads(assets["manifest"].read_text(encoding="utf-8"))
            metrics = parse_the_one_reports(case_root / "reports", message_size_bytes=message_size)
            external_deliveries = parse_the_one_deliveries(
                case_root / "reports", origin_us=int(manifest["origin_us"])
            )
            external_set = set(external_deliveries)
            comparison_path = case_root / "message_level_comparison.csv"
            _, comparison = compare_message_level(
                contacts_path,
                bundles_path,
                case_root / "reports",
                comparison_path,
                origin_us=int(manifest["origin_us"]),
                buffer_bytes=buffer_bytes,
            )
            rows.append(
                {
                    "update_interval_seconds": update_interval,
                    "buffer_bytes": buffer_bytes,
                    **asdict(metrics),
                    **comparison,
                    "slot_causal_oracle_delivered": len(slot_oracle),
                    "intraslot_closure_oracle_delivered": len(closure_oracle),
                    "matches_slot_causal_delivery_set": external_set == slot_oracle,
                    "matches_intraslot_closure_delivery_set": external_set == closure_oracle,
                    "semantic_resolution": (
                        "slot_causal"
                        if external_set == slot_oracle
                        else (
                            "continuous_intraslot_closure"
                            if external_set == closure_oracle
                            else "scheduler_limited_intermediate"
                        )
                    ),
                    "the_one_dropped_semantics": (
                        "per-copy delete events from buffer pressure or TTL expiry; "
                        "not unique undelivered bundles"
                    ),
                    "aidmesh_dropped_semantics": "finite-buffer eviction events",
                    "case_manifest": str(assets["manifest"]),
                    "message_rows": str(comparison_path),
                }
            )
    output.mkdir(parents=True, exist_ok=True)
    summary_path = output / "the_one_discrepancy_sweep.csv"
    with summary_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return summary_path
