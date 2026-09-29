"""Provenance-preserving conversion of SocioPatterns temporal contacts to AidMesh CSV.

The source data only records proximity contacts.  Site/relay/client roles and
bundle workload are therefore a deterministic evaluation overlay, never a
claim about the original participants or a humanitarian deployment.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import math
import re
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

from aidmesh.common.canonical import stable_hash


@dataclass(frozen=True)
class TraceConversionConfig:
    context: str
    bytes_per_second: int
    contact_step_seconds: int = 20
    site_count: int = 4
    relay_count: int = 3
    bundles_per_client: int = 2
    ttl_hours: int = 24
    bundle_size_bytes: int = 2048
    collapse_contiguous: bool = False

    def __post_init__(self) -> None:
        if not self.context:
            raise ValueError("context is required")
        if (
            min(
                self.bytes_per_second,
                self.contact_step_seconds,
                self.site_count,
                self.relay_count,
                self.bundles_per_client,
                self.ttl_hours,
                self.bundle_size_bytes,
            )
            <= 0
        ):
            raise ValueError("trace conversion numeric values must be positive")


@dataclass(frozen=True)
class TemporalContactRow:
    start_us: int
    end_us: int
    node_a: str
    node_b: str
    bytes_per_second: int


@dataclass(frozen=True)
class DieselNetConversionConfig:
    """Frozen interpretation of UMass DieselNet transfer-log observations."""

    context: str = "dieselnet05"
    event_window_seconds: int = 1
    minimum_bytes_per_second: int = 2048
    site_count: int = 4
    relay_count: int = 3
    bundles_per_client: int = 2
    ttl_hours: int = 24
    bundle_size_bytes: int = 2048

    def __post_init__(self) -> None:
        if not self.context:
            raise ValueError("context is required")
        if (
            min(
                self.event_window_seconds,
                self.minimum_bytes_per_second,
                self.site_count,
                self.relay_count,
                self.bundles_per_client,
                self.ttl_hours,
                self.bundle_size_bytes,
            )
            <= 0
        ):
            raise ValueError("DieselNet conversion numeric values must be positive")


@dataclass(frozen=True)
class Ene5aiConversionConfig:
    """Frozen interpretation of the ENE5AI one-second mesh snapshots."""

    context: str = "ene5ai"
    minimum_bytes_per_second: int = 2048
    site_count: int = 4
    relay_count: int = 3
    bundles_per_client: int = 2
    ttl_hours: int = 1
    bundle_size_bytes: int = 2048

    def __post_init__(self) -> None:
        if not self.context:
            raise ValueError("context is required")
        if (
            min(
                self.minimum_bytes_per_second,
                self.site_count,
                self.relay_count,
                self.bundles_per_client,
                self.ttl_hours,
                self.bundle_size_bytes,
            )
            <= 0
        ):
            raise ValueError("ENE5AI conversion numeric values must be positive")


@dataclass(frozen=True)
class AsturiesWildfireConversionConfig:
    """Contact interpretation for upstream Asturies-Wildfires ONE scenarios."""

    context: str
    communication_range_m: int = 200
    snapshot_seconds: int = 30
    bytes_per_second: int = 32_000

    def __post_init__(self) -> None:
        if not self.context:
            raise ValueError("context is required")
        if (
            min(
                self.communication_range_m,
                self.snapshot_seconds,
                self.bytes_per_second,
            )
            <= 0
        ):
            raise ValueError("Asturies conversion numeric values must be positive")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_directory(path: Path) -> str:
    """Hash a directory without depending on the host's path separator."""

    digest = hashlib.sha256()
    files = sorted(item for item in path.rglob("*") if item.is_file())
    if not files:
        raise ValueError(f"no files found in {path}")
    for item in files:
        relative = item.relative_to(path).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(bytes.fromhex(sha256_file(item)))
    return digest.hexdigest()


_DIESELNET_EVENT = re.compile(
    r"^Bus (?P<receiver>\d+).*? in contact with bus (?P<sender>\d+).*? "
    r"at time (?P<minute>\d+):(?P<second>\d+) for (?P<bytes>\d+) bytes$"
)


def _dieselnet_date(name: str) -> dt.date:
    """Parse the original MDDYYYY filenames used by the Spring 2005 archive."""

    if len(name) < 6 or not name.isdigit():
        raise ValueError(f"invalid DieselNet log filename: {name}")
    year = int(name[-4:])
    month_day = name[:-4]
    # The released Spring trace spans January--May, so the first digit is month.
    return dt.date(year, int(month_day[0]), int(month_day[1:]))


def normalize_dieselnet_contacts(
    source_directory: Path,
    *,
    config: DieselNetConversionConfig,
) -> list[TemporalContactRow]:
    """Convert successful byte-transfer observations into conservative contacts.

    Every log entry is evidence that at least ``bytes`` bytes crossed at its timestamp.
    The primary profile exposes a one-second opportunity and chooses a rate whose
    capacity is exactly the observed byte count (subject to the registered minimum).
    Reciprocal records at the exact same second are one undirected opportunity and use
    the larger observed capacity; no GPS or route field is used because the source
    documentation marks those fields inaccurate.
    """

    files: list[tuple[dt.date, Path]] = []
    for path in source_directory.iterdir():
        if path.is_file() and path.name.isdigit():
            files.append((_dieselnet_date(path.name), path))
    if not files:
        raise ValueError(f"no DieselNet daily log files found in {source_directory}")
    files.sort()
    origin = files[0][0]
    observations: dict[tuple[int, str, str], int] = {}
    for date, path in files:
        day_offset_seconds = (date - origin).days * 86_400
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                match = _DIESELNET_EVENT.match(line.strip())
                if match is None:
                    raise ValueError(f"unparsed DieselNet event at {path}:{line_number}")
                seconds = (
                    day_offset_seconds
                    + int(match.group("minute")) * 60
                    + int(match.group("second"))
                )
                node_a, node_b = sorted(
                    (f"bus-{match.group('receiver')}", f"bus-{match.group('sender')}")
                )
                transferred = int(match.group("bytes"))
                key = (seconds, node_a, node_b)
                observations[key] = max(observations.get(key, 0), transferred)
    window = config.event_window_seconds
    rows = [
        TemporalContactRow(
            start_us=seconds * 1_000_000,
            end_us=(seconds + window) * 1_000_000,
            node_a=node_a,
            node_b=node_b,
            bytes_per_second=max(
                config.minimum_bytes_per_second,
                math.ceil(transferred / window),
            ),
        )
        for (seconds, node_a, node_b), transferred in observations.items()
    ]
    return sorted(rows, key=lambda row: (row.start_us, row.end_us, row.node_a, row.node_b))


def normalize_ene5ai_contacts(
    source: Path,
    *,
    config: Ene5aiConversionConfig,
) -> list[TemporalContactRow]:
    """Convert ENE5AI direct Wi-Fi observations into conservative contacts.

    The upstream ``edges/all.csv`` contains both routed/global graph edges and
    directly observed radio edges.  A row is admitted only when its edge type
    contains ``local`` and the record contains either the nl80211
    ``connected-time`` or ``signal-avg`` field.  Reciprocal observations in a
    one-second slot become one undirected opportunity.  Capacity is the larger
    observed per-second byte counter, subject to a registered minimum; it is a
    lower bound on available radio capacity, not a link-rate estimate.
    """

    if not source.is_file():
        raise FileNotFoundError(source)
    observations: dict[tuple[int, str, str], int] = {}
    with source.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {
            "timestamp",
            "source",
            "target",
            "connected-time",
            "signal-avg",
            "rx-bytes",
            "tx-bytes",
            "type",
        }
        if missing := required - set(reader.fieldnames or ()):
            raise ValueError(f"ENE5AI edge CSV missing columns: {sorted(missing)}")
        for line_number, row in enumerate(reader, start=2):
            edge_type = (row.get("type") or "").lower()
            if "local" not in edge_type:
                continue
            if not (row.get("connected-time") or row.get("signal-avg")):
                continue
            try:
                timestamp = int(row["timestamp"])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid ENE5AI timestamp at {source}:{line_number}") from exc
            node_a, node_b = sorted((row["source"], row["target"]))
            if not node_a or not node_b or node_a == node_b:
                continue
            counters = []
            for field_name in ("rx-bytes", "tx-bytes"):
                value = row.get(field_name)
                if value:
                    try:
                        counters.append(max(0, math.ceil(float(value))))
                    except ValueError as exc:
                        raise ValueError(
                            f"invalid ENE5AI {field_name} at {source}:{line_number}"
                        ) from exc
            observed_bytes = max(counters, default=0)
            key = (timestamp, node_a, node_b)
            observations[key] = max(observations.get(key, 0), observed_bytes)
    if not observations:
        raise ValueError(f"no direct ENE5AI Wi-Fi observations found in {source}")
    origin = min(timestamp for timestamp, _, _ in observations)
    rows = [
        TemporalContactRow(
            start_us=(timestamp - origin) * 1_000_000,
            end_us=(timestamp - origin + 1) * 1_000_000,
            node_a=node_a,
            node_b=node_b,
            bytes_per_second=max(config.minimum_bytes_per_second, observed_bytes),
        )
        for (timestamp, node_a, node_b), observed_bytes in observations.items()
    ]
    return sorted(rows, key=lambda row: (row.start_us, row.end_us, row.node_a, row.node_b))


def normalize_asturies_wildfire_contacts(
    source: Path,
    *,
    config: AsturiesWildfireConversionConfig,
) -> list[TemporalContactRow]:
    """Derive exact 30-second radio contacts from an upstream ONE mobility file.

    The upstream file begins with a six-field scenario-bounds record.  Every
    subsequent row is ``time node x y`` in the projected metric coordinates used by
    the authors' ONE scenarios.  A pair is a contact for one source snapshot exactly
    when its Euclidean distance is within the registered communication range.  No
    AidMesh outcome is consulted by this conversion.
    """

    if not source.is_file():
        raise FileNotFoundError(source)
    positions_by_time: dict[int, dict[str, tuple[int, int]]] = defaultdict(dict)
    with source.open("r", encoding="utf-8") as handle:
        header = handle.readline().split()
        if len(header) != 6:
            raise ValueError(f"invalid Asturies ONE bounds row: {source}")
        for line_number, line in enumerate(handle, start=2):
            fields = line.split()
            if len(fields) != 4:
                raise ValueError(f"invalid Asturies row at {source}:{line_number}")
            try:
                timestamp, node, x, y = map(int, fields)
            except ValueError as exc:
                raise ValueError(f"non-integer Asturies row at {source}:{line_number}") from exc
            positions_by_time[timestamp][f"ast-{node}"] = (x, y)
    if not positions_by_time:
        raise ValueError(f"no Asturies positions found in {source}")
    origin = min(positions_by_time)
    range_squared = config.communication_range_m**2
    samples: list[TemporalContactRow] = []
    for timestamp, positions in sorted(positions_by_time.items()):
        nodes = sorted(positions)
        for left_index, node_a in enumerate(nodes):
            x_a, y_a = positions[node_a]
            for node_b in nodes[left_index + 1 :]:
                x_b, y_b = positions[node_b]
                if (x_a - x_b) ** 2 + (y_a - y_b) ** 2 > range_squared:
                    continue
                start_us = (timestamp - origin) * 1_000_000
                samples.append(
                    TemporalContactRow(
                        start_us=start_us,
                        end_us=start_us + config.snapshot_seconds * 1_000_000,
                        node_a=node_a,
                        node_b=node_b,
                        bytes_per_second=config.bytes_per_second,
                    )
                )
    # Consecutive snapshots for the same pair describe one continuous radio
    # opportunity.  Coalescing is lossless for the contact model and avoids
    # replaying tens of thousands of redundant boundary events per incident.
    merged: list[TemporalContactRow] = []
    by_pair = sorted(samples, key=lambda row: (row.node_a, row.node_b, row.start_us))
    for row in by_pair:
        if (
            merged
            and merged[-1].node_a == row.node_a
            and merged[-1].node_b == row.node_b
            and merged[-1].bytes_per_second == row.bytes_per_second
            and row.start_us <= merged[-1].end_us
        ):
            previous = merged[-1]
            merged[-1] = TemporalContactRow(
                start_us=previous.start_us,
                end_us=max(previous.end_us, row.end_us),
                node_a=previous.node_a,
                node_b=previous.node_b,
                bytes_per_second=previous.bytes_per_second,
            )
        else:
            merged.append(row)
    return sorted(
        merged,
        key=lambda row: (row.start_us, row.end_us, row.node_a, row.node_b),
    )


def write_asturies_wildfire_trace(
    source: Path,
    *,
    config: AsturiesWildfireConversionConfig,
    output: Path,
    upstream_commit: str,
) -> dict[str, Path]:
    """Write one independently observed wildfire incident and its provenance."""

    contacts = normalize_asturies_wildfire_contacts(source, config=config)
    output.mkdir(parents=True, exist_ok=True)
    contacts_path = output / "contacts.csv"
    provenance_path = output / "provenance.json"
    with contacts_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(TemporalContactRow.__dataclass_fields__))
        writer.writeheader()
        writer.writerows(asdict(contact) for contact in contacts)
    nodes = sorted({node for row in contacts for node in (row.node_a, row.node_b)})
    duration_us = max((row.end_us for row in contacts), default=0)
    cutoff_us = round(duration_us * 0.4)
    snapshot_us = config.snapshot_seconds * 1_000_000
    raw_contact_samples = sum(
        math.ceil((row.end_us - row.start_us) / snapshot_us) for row in contacts
    )
    calibration_contacts = sum(
        max(0, min(row.end_us, cutoff_us) - row.start_us) // snapshot_us
        for row in contacts
        if row.start_us < cutoff_us
    )
    heldout_contacts = raw_contact_samples - calibration_contacts
    provenance = {
        "schema_version": 1,
        "dataset": "Asturies-Wildfires firefighter vehicle mobility traces",
        "family": "asturies_wildfires",
        "citation_doi": "10.1109/ACCESS.2017.2778347",
        "source_repository": "https://github.com/sergiocabrero/asturies-wildfires",
        "upstream_commit": upstream_commit,
        "upstream_readme_terms": (
            "Upstream README invites research use and requests citation; no SPDX "
            "license file is present in the inspected commit"
        ),
        "source_file": source.name,
        "source_sha256": sha256_file(source),
        "context": config.context,
        "conversion": asdict(config),
        "conversion_semantics": (
            "one undirected contact per upstream 30-second mobility snapshot when "
            "projected Euclidean distance is within the registered radio range; "
            "adjacent snapshots of the same pair are losslessly coalesced"
        ),
        "synthetic_overlay_boundary": (
            "contacts are incident-derived; AidMesh sites, witnesses, clients, "
            "requests, and demand drift remain deterministic evaluation overlays"
        ),
        "normalized_contacts": len(contacts),
        "raw_contact_samples": raw_contact_samples,
        "nodes_with_contacts": len(nodes),
        "duration_us": duration_us,
        "calibration_contacts": calibration_contacts,
        "heldout_contacts": heldout_contacts,
        "contacts_sha256": sha256_file(contacts_path),
    }
    provenance_path.write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {"contacts": contacts_path, "provenance": provenance_path}


def _source_samples(path: Path) -> dict[tuple[str, str], list[int]]:
    samples: dict[tuple[str, str], list[int]] = defaultdict(list)
    seen: set[tuple[int, str, str]] = set()
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            fields = stripped.split()
            if len(fields) < 3:
                raise ValueError(f"expected t i j at {path}:{line_number}")
            try:
                timestamp = int(fields[0])
            except ValueError as exc:
                raise ValueError(f"invalid timestamp at {path}:{line_number}") from exc
            if timestamp < 0:
                raise ValueError(f"negative timestamp at {path}:{line_number}")
            left, right = sorted((fields[1], fields[2]))
            if left == right:
                raise ValueError(f"self contact at {path}:{line_number}")
            key = (timestamp, left, right)
            if key not in seen:
                samples[(left, right)].append(timestamp)
                seen.add(key)
    return samples


def normalize_sociopatterns_contacts(
    source: Path,
    *,
    config: TraceConversionConfig,
) -> list[TemporalContactRow]:
    """Convert samples to slots, optionally collapsing adjacent pair observations."""

    rows: list[TemporalContactRow] = []
    step = config.contact_step_seconds
    for (node_a, node_b), timestamps in sorted(_source_samples(source).items()):
        ordered = sorted(timestamps)
        if not config.collapse_contiguous:
            rows.extend(
                TemporalContactRow(
                    start_us=timestamp * 1_000_000,
                    end_us=(timestamp + step) * 1_000_000,
                    node_a=node_a,
                    node_b=node_b,
                    bytes_per_second=config.bytes_per_second,
                )
                for timestamp in ordered
            )
            continue
        start = previous = ordered[0]
        for timestamp in ordered[1:]:
            if timestamp != previous + step:
                rows.append(
                    TemporalContactRow(
                        start_us=start * 1_000_000,
                        end_us=(previous + step) * 1_000_000,
                        node_a=node_a,
                        node_b=node_b,
                        bytes_per_second=config.bytes_per_second,
                    )
                )
                start = timestamp
            previous = timestamp
        rows.append(
            TemporalContactRow(
                start_us=start * 1_000_000,
                end_us=(previous + step) * 1_000_000,
                node_a=node_a,
                node_b=node_b,
                bytes_per_second=config.bytes_per_second,
            )
        )
    if not rows:
        raise ValueError(f"no contact rows found in {source}")
    return sorted(rows, key=lambda row: (row.start_us, row.end_us, row.node_a, row.node_b))


def _node_scores(contacts: list[TemporalContactRow]) -> dict[str, int]:
    scores: dict[str, int] = defaultdict(int)
    for contact in contacts:
        duration = contact.end_us - contact.start_us
        scores[contact.node_a] += duration
        scores[contact.node_b] += duration
    return dict(scores)


def deterministic_role_mapping(
    contacts: list[TemporalContactRow],
    *,
    config: TraceConversionConfig,
) -> dict[str, list[str]]:
    """Map anonymous trace nodes to evaluation roles by contact-time centrality."""

    scores = _node_scores(contacts)
    ordered = sorted(scores, key=lambda node: (-scores[node], node))
    needed = config.site_count + config.relay_count + 1
    if len(ordered) < needed:
        raise ValueError(f"trace has {len(ordered)} nodes, requires at least {needed}")
    sites = ordered[: config.site_count]
    relays = ordered[config.site_count : config.site_count + config.relay_count]
    clients = ordered[config.site_count + config.relay_count :]
    return {"sites": sites, "relays": relays, "clients": clients}


def generate_trace_bundles(
    contacts: list[TemporalContactRow],
    *,
    mapping: dict[str, list[str]],
    config: TraceConversionConfig,
) -> list[dict[str, int | str | float]]:
    """Generate deterministic client-to-site bundles from observed client activity windows."""

    contacts_by_node: dict[str, list[int]] = defaultdict(list)
    for contact in contacts:
        contacts_by_node[contact.node_a].append(contact.start_us)
        contacts_by_node[contact.node_b].append(contact.start_us)
    bundles: list[dict[str, int | str | float]] = []
    ttl_us = config.ttl_hours * 3_600_000_000
    for client_index, client in enumerate(mapping["clients"]):
        activity = sorted(contacts_by_node[client])
        if not activity:
            continue
        for ordinal in range(config.bundles_per_client):
            position = min(len(activity) - 1, ordinal * len(activity) // config.bundles_per_client)
            destination = mapping["sites"][(client_index + ordinal) % len(mapping["sites"])]
            if destination == client:
                destination = mapping["sites"][(client_index + ordinal + 1) % len(mapping["sites"])]
            bundle_id = stable_hash(
                {"context": config.context, "client": client, "ordinal": ordinal},
                domain="aidmesh/external-trace/bundle-id/v1",
            )[:24]
            risk_seed = int(
                stable_hash(
                    {"bundle_id": bundle_id},
                    domain="aidmesh/external-trace/risk-score/v1",
                )[:8],
                16,
            )
            bundles.append(
                {
                    "bundle_id": f"b-{bundle_id}",
                    "source": client,
                    "destination": destination,
                    "created_us": activity[position],
                    "ttl_us": ttl_us,
                    "size_bytes": config.bundle_size_bytes,
                    "risk_score": round(0.1 + (risk_seed % 9000) / 10_000, 4),
                }
            )
    if not bundles:
        raise ValueError("role mapping did not produce any client bundles")
    return bundles


def write_sociopatterns_trace(
    source: Path,
    *,
    config: TraceConversionConfig,
    output: Path,
) -> dict[str, Path]:
    """Write AidMesh contact/bundle CSV plus exact source and overlay provenance."""

    contacts = normalize_sociopatterns_contacts(source, config=config)
    mapping = deterministic_role_mapping(contacts, config=config)
    bundles = generate_trace_bundles(contacts, mapping=mapping, config=config)
    output.mkdir(parents=True, exist_ok=True)
    contacts_path = output / "contacts.csv"
    bundles_path = output / "bundles.csv"
    mapping_path = output / "mapping.json"
    provenance_path = output / "provenance.json"
    with contacts_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(TemporalContactRow.__dataclass_fields__))
        writer.writeheader()
        writer.writerows(asdict(contact) for contact in contacts)
    with bundles_path.open("w", encoding="utf-8", newline="") as handle:
        fields = [
            "bundle_id",
            "source",
            "destination",
            "created_us",
            "ttl_us",
            "size_bytes",
            "risk_score",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(bundles)
    mapping_path.write_text(json.dumps(mapping, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    provenance_path.write_text(
        json.dumps(
            {
                "dataset": "SocioPatterns contact.tar.bz2, Zenodo record 2540795",
                "source_file": source.name,
                "source_sha256": sha256_file(source),
                "license": "CC BY-NC 4.0; researcher must confirm intended use complies",
                "context": config.context,
                "conversion": asdict(config),
                "role_mapping_rule": "rank anonymous nodes by total observed contact duration; "
                "assign sites, then relays, then clients in deterministic lexical tie order",
                "bundle_rule": "create a fixed number of client-to-site bundles at observed "
                "client contact timestamps; this is an evaluation overlay, not source metadata",
                "contacts_sha256": sha256_file(contacts_path),
                "bundles_sha256": sha256_file(bundles_path),
                "mapping_sha256": sha256_file(mapping_path),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "contacts": contacts_path,
        "bundles": bundles_path,
        "mapping": mapping_path,
        "provenance": provenance_path,
    }


def write_dieselnet_trace(
    source_directory: Path,
    *,
    config: DieselNetConversionConfig,
    output: Path,
) -> dict[str, Path]:
    """Write the normalized DieselNet family with immutable provenance."""

    contacts = normalize_dieselnet_contacts(source_directory, config=config)
    common = TraceConversionConfig(
        context=config.context,
        bytes_per_second=config.minimum_bytes_per_second,
        contact_step_seconds=config.event_window_seconds,
        site_count=config.site_count,
        relay_count=config.relay_count,
        bundles_per_client=config.bundles_per_client,
        ttl_hours=config.ttl_hours,
        bundle_size_bytes=config.bundle_size_bytes,
    )
    mapping = deterministic_role_mapping(contacts, config=common)
    bundles = generate_trace_bundles(contacts, mapping=mapping, config=common)
    output.mkdir(parents=True, exist_ok=True)
    contacts_path = output / "contacts.csv"
    bundles_path = output / "bundles.csv"
    mapping_path = output / "mapping.json"
    provenance_path = output / "provenance.json"
    with contacts_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(TemporalContactRow.__dataclass_fields__))
        writer.writeheader()
        writer.writerows(asdict(contact) for contact in contacts)
    with bundles_path.open("w", encoding="utf-8", newline="") as handle:
        fields = [
            "bundle_id",
            "source",
            "destination",
            "created_us",
            "ttl_us",
            "size_bytes",
            "risk_score",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(bundles)
    mapping_path.write_text(json.dumps(mapping, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    source_archive = source_directory.parents[1] / "source" / "UMassDieselNet_Spring2005.tar.gz"
    provenance = {
        "schema_version": 1,
        "dataset": "UMass DieselNet Spring 2005",
        "family": "umass_dieselnet",
        "source_repository": "UMass Trace Repository",
        "source_url": (
            "https://skulddata.cs.umass.edu/traces/network/UMassDieselNet_Spring2005.tar.gz"
        ),
        "repository_record": "https://traces.cs.umass.edu/docs/traces/network/",
        "source_archive_sha256": sha256_file(source_archive) if source_archive.exists() else None,
        "source_directory_sha256": sha256_directory(source_directory),
        "license": "CC BY 4.0 (UMass Trace Repository default, accessed 2026-07-18)",
        "citation": (
            "John Burgess, Brian Gallagher, David Jensen, and Brian Neil Levine, "
            "MaxProp: Routing for Vehicle-Based Disruption-Tolerant Networking, "
            "IEEE INFOCOM, 2006."
        ),
        "context": config.context,
        "conversion": asdict(config),
        "conversion_semantics": (
            "one undirected contact per pair/timestamp; reciprocal exact-time records "
            "deduplicated by maximum observed bytes; fixed registered opportunity window; "
            "capacity equals observed bytes subject to a minimum payload rate"
        ),
        "excluded_source_fields": "GPS coordinates and route labels, per source README",
        "role_mapping_rule": (
            "rank anonymous bus IDs by total observed contact duration; assign synthetic "
            "sites, relays, then clients using deterministic lexical tie-breaking"
        ),
        "bundle_rule": (
            "synthetic evaluation overlay generated from observed activity timestamps; "
            "not a humanitarian deployment or original source label"
        ),
        "raw_events": sum(
            1
            for path in source_directory.iterdir()
            if path.is_file() and path.name.isdigit()
            for _ in path.open("r", encoding="utf-8")
        ),
        "normalized_contacts": len(contacts),
        "nodes": len({node for row in contacts for node in (row.node_a, row.node_b)}),
        "contacts_sha256": sha256_file(contacts_path),
        "bundles_sha256": sha256_file(bundles_path),
        "mapping_sha256": sha256_file(mapping_path),
    }
    provenance_path.write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return {
        "contacts": contacts_path,
        "bundles": bundles_path,
        "mapping": mapping_path,
        "provenance": provenance_path,
    }


def write_ene5ai_trace(
    source: Path,
    *,
    config: Ene5aiConversionConfig,
    output: Path,
    source_archive: Path | None = None,
) -> dict[str, Path]:
    """Write a normalized ENE5AI trace and complete source interpretation."""

    contacts = normalize_ene5ai_contacts(source, config=config)
    common = TraceConversionConfig(
        context=config.context,
        bytes_per_second=config.minimum_bytes_per_second,
        contact_step_seconds=1,
        site_count=config.site_count,
        relay_count=config.relay_count,
        bundles_per_client=config.bundles_per_client,
        ttl_hours=config.ttl_hours,
        bundle_size_bytes=config.bundle_size_bytes,
    )
    mapping = deterministic_role_mapping(contacts, config=common)
    bundles = generate_trace_bundles(contacts, mapping=mapping, config=common)
    output.mkdir(parents=True, exist_ok=True)
    contacts_path = output / "contacts.csv"
    bundles_path = output / "bundles.csv"
    mapping_path = output / "mapping.json"
    provenance_path = output / "provenance.json"
    with contacts_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(TemporalContactRow.__dataclass_fields__))
        writer.writeheader()
        writer.writerows(asdict(contact) for contact in contacts)
    with bundles_path.open("w", encoding="utf-8", newline="") as handle:
        fields = [
            "bundle_id",
            "source",
            "destination",
            "created_us",
            "ttl_us",
            "size_bytes",
            "risk_score",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(bundles)
    mapping_path.write_text(json.dumps(mapping, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    archive_md5 = None
    if source_archive is not None and source_archive.is_file():
        archive_md5 = hashlib.md5(source_archive.read_bytes()).hexdigest()  # noqa: S324
    provenance = {
        "schema_version": 1,
        "dataset": "ENE5AI Dataset (5G and Wi-Fi Mesh Mobility), version 1.0",
        "family": "ene5ai",
        "dataset_doi": "10.57745/DFMCYS",
        "file_doi": "10.57745/ISKJN8",
        "source_url": (
            "https://entrepot.recherche.data.gouv.fr/api/access/datafile/"
            ":persistentId?persistentId=doi:10.57745/ISKJN8"
        ),
        "license": "Etalab Open License 2.0",
        "source_file": str(source),
        "source_sha256": sha256_file(source),
        "source_archive_md5": archive_md5,
        "context": config.context,
        "conversion": asdict(config),
        "conversion_semantics": (
            "one undirected one-second contact per direct local Wi-Fi pair/slot; "
            "requires nl80211 connected-time or signal evidence; routed global, cell, "
            "Ethernet and virtual edges excluded; capacity is observed byte-counter "
            "lower bound subject to the registered minimum"
        ),
        "role_mapping_rule": (
            "rank trace node identifiers by total direct-contact duration; assign "
            "synthetic sites, relays, then clients with lexical tie-breaking"
        ),
        "bundle_rule": (
            "synthetic evaluation overlay at observed activity timestamps; the source "
            "trace is a real firefighter exercise but is not an AidMesh deployment"
        ),
        "normalized_contacts": len(contacts),
        "nodes": len({node for row in contacts for node in (row.node_a, row.node_b)}),
        "contacts_sha256": sha256_file(contacts_path),
        "bundles_sha256": sha256_file(bundles_path),
        "mapping_sha256": sha256_file(mapping_path),
    }
    provenance_path.write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return {
        "contacts": contacts_path,
        "bundles": bundles_path,
        "mapping": mapping_path,
        "provenance": provenance_path,
    }
