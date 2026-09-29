"""Frozen/resumable routing grid. Not a full H1 or protocol-baseline campaign."""
import argparse
import hashlib
import itertools
import json
import sys
from dataclasses import asdict, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from aidmesh.simulator.dtn import (
    HOUR_US, RoutingPolicy, TemporalContactSimulator,
    generate_receipt_bundles, generate_synthetic_contacts,
)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["freeze", "run"])
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    out = args.directory
    sources = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
               for p in (Path(__file__).resolve(), ROOT / "aidmesh/simulator/dtn.py")}
    if args.phase == "freeze":
        out.mkdir(parents=True, exist_ok=False)
        spec = dict(seeds=list(range(99601, 99631)), partitions=[0, 1, 6, 24, 72],
                    bandwidth_kbit=[64, 256, 1024], buffer_mb=[10, 50, 200],
                    policies=[p.value for p in RoutingPolicy], sites=8, relays=3,
                    bundles=250, horizon_hours=168, ttl_hours=72, copies=4,
                    loss=0, source_sha256=sources,
                    scope="Routing factorial only; fixed 8 sites/250 bundles/zero loss. "
                          "No DPDRM, CSAR, cryptographic online baseline or original holdout.",
                    endpoint="50th percentile including failures at infinity; TTL-capped mean secondary",
                    matching="Identical contacts and bundles across policies; capacity is full duplex. "
                             "Epidemic is an unbounded-copy diagnostic, excluded from equal-copy claims.")
        (out / "protocol.json").write_text(json.dumps(spec, indent=2))
        return
    spec = json.loads((out / "protocol.json").read_text())
    if sources != spec["source_sha256"]:
        raise ValueError("source drift: freeze a new campaign")
    raw = out / "runs.jsonl"
    completed = set()
    if raw.exists():
        for line in raw.read_text().splitlines():
            completed.add(json.loads(line)["key"])
    with raw.open("a", encoding="utf-8") as stream:
        for seed, partition, bandwidth in itertools.product(
                spec["seeds"], spec["partitions"], spec["bandwidth_kbit"]):
            contacts = generate_synthetic_contacts(
                seed=seed, sites=spec["sites"], relays=spec["relays"],
                horizon_us=spec["horizon_hours"] * HOUR_US,
                partition_us=partition * HOUR_US, bytes_per_second=bandwidth * 1000 // 8)
            contacts = tuple(c for c in contacts if "contract" not in (c.node_a, c.node_b)
                             or c.start_us >= partition * HOUR_US)
            bundles = generate_receipt_bundles(seed=seed, count=spec["bundles"],
                sites=spec["sites"], horizon_us=48 * HOUR_US,
                ttl_us=spec["ttl_hours"] * HOUR_US)
            targets = {b.bundle_id for i, b in enumerate(bundles) if i % 3 != 2}
            bundles = tuple(replace(b, risk_score=1.0 if b.bundle_id in targets else 0.1)
                            for b in bundles)
            trace_hash = digest([asdict(c) for c in contacts])
            bundle_hash = digest([asdict(b) for b in bundles])
            for buffer_mb, name in itertools.product(spec["buffer_mb"], spec["policies"]):
                key = f"{seed}/{partition}/{bandwidth}/{buffer_mb}/{name}"
                if key in completed:
                    continue
                result = TemporalContactSimulator(contacts=contacts, bundles=bundles,
                    policy=RoutingPolicy(name), buffer_bytes=buffer_mb * 1_000_000,
                    copy_budget=spec["copies"]).run()
                observed = sorted(d.delay_us / HOUR_US for d in result.deliveries
                                  if d.bundle_id in targets)
                rank = (len(targets) + 1) // 2
                median = observed[rank - 1] if len(observed) >= rank else None
                row = dict(key=key, seed=seed, partition=partition, bandwidth_kbit=bandwidth,
                    buffer_mb=buffer_mb, policy=name, contact_sha256=trace_hash,
                    bundle_sha256=bundle_hash, target_count=len(targets), delivered=len(observed),
                    median_hours=median, median_reached=median is not None,
                    capped_mean_hours=(sum(observed) + (len(targets)-len(observed))*spec["ttl_hours"])/len(targets),
                    forwarded_bytes=result.forwarded_bytes,
                    capacity_bytes_full_duplex=2*sum(c.capacity_bytes for c in contacts),
                    event_checksum=result.event_checksum)
                if row["forwarded_bytes"] > row["capacity_bytes_full_duplex"]:
                    raise AssertionError("capacity exceeded")
                stream.write(json.dumps(row, sort_keys=True) + "\n")
                stream.flush()
            print(f"completed seed={seed} partition={partition} bandwidth={bandwidth}", flush=True)
    import pandas as pd
    frame = pd.read_json(raw, lines=True)
    expected = (len(spec["seeds"])*len(spec["partitions"])*len(spec["bandwidth_kbit"])
                *len(spec["buffer_mb"])*len(spec["policies"]))
    assert len(frame) == expected and frame.key.nunique() == expected
    group = ["seed", "partition", "bandwidth_kbit", "buffer_mb"]
    assert (frame.groupby(group).contact_sha256.nunique() == 1).all()
    assert (frame.groupby(group).bundle_sha256.nunique() == 1).all()
    frame.groupby(["partition", "bandwidth_kbit", "buffer_mb", "policy"]).agg(
        capped_mean_hours=("capped_mean_hours", "mean"),
        median_reached_fraction=("median_reached", "mean"),
        mean_delivered=("delivered", "mean")).to_csv(out / "summary.csv")
    (out / "COMPLETE.json").write_text(json.dumps(dict(rows=len(frame), matched=True,
        raw_sha256=hashlib.sha256(raw.read_bytes()).hexdigest(), scope=spec["scope"]), indent=2))


if __name__ == "__main__":
    main()
