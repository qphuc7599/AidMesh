"""Frozen corrective audit: all eligible incidents, paired seeds, no tuning."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from aidmesh.experiments.bow_temporal import run_bow_temporal_campaign  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output", type=Path, default=ROOT / "results/q1_revision_20260906/replay_final"
    )
    args = parser.parse_args()
    out = args.output
    out.mkdir(parents=True, exist_ok=True)
    tracked = [Path(__file__).resolve(), *sorted((ROOT / "aidmesh").rglob("*.py"))]
    fingerprint = {
        str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in tracked
    }
    provenance = out / "implementation.json"
    if provenance.exists():
        if json.loads(provenance.read_text()) != fingerprint:
            raise RuntimeError("cached experiment implementation differs; use a fresh --output")
    elif list(out.glob("*.csv")):
        raise RuntimeError("unversioned cached rows; use a fresh --output")
    else:
        provenance.write_text(json.dumps(fingerprint, indent=2))
    trace = ROOT / "external/asturies_wildfires/normalized"
    contexts = json.loads((trace / "family_manifest.json").read_text())["eligible_contexts"]
    protocol = dict(
        contexts=contexts,
        seeds=[96101, 96102],
        cap_fractions=[0.2, 0.4],
        quorum_availabilities=[0.35, 0.9],
        policies=["robust_static", "local_pull", "local_pull_certified"],
        primary="unique_entitlement_coverage",
        bootstrap_unit="incident",
        status="corrective exploratory audit; reused incidents, fresh workload seeds",
        tuning="none; reserve_fraction=0.5; common prefix placement",
        information="pull is delivered before donor reads own balance and debits",
    )
    protocol_path = out / "protocol.json"
    serialized = json.dumps(protocol, indent=2)
    if protocol_path.exists() and protocol_path.read_text() != serialized:
        raise RuntimeError("refusing to overwrite a different registered protocol")
    protocol_path.write_text(serialized)
    frames = []
    for context in contexts:
        path = out / f"{context}.csv"
        if path.exists():
            frame = pd.read_csv(path)
        else:
            frame, manifest = run_bow_temporal_campaign(
                trace,
                contexts=(context,),
                seeds=tuple(protocol["seeds"]),
                cap_fractions=tuple(protocol["cap_fractions"]),
                quorum_availabilities=tuple(protocol["quorum_availabilities"]),
                policies=tuple(protocol["policies"]),
                oracle_policies=(),
                experiment_prefix="q1revision20260906",
            )
            frame.to_csv(path, index=False)
            (out / f"{context}.manifest.json").write_text(json.dumps(manifest, indent=2))
        frames.append(frame)
        print(context, len(frame), flush=True)
    data = pd.concat(frames, ignore_index=True)
    data.to_csv(out / "runs.csv", index=False)
    # The existing generator clamps cap to the site count and deduplicates it.
    # Small workloads therefore collapse the two nominal cap fractions.
    keys = ["context", "seed", "cap", "quorum_availability"]
    assert data.groupby(keys).policy.nunique().eq(3).all()
    assert data.context.nunique() == 23
    assert not data.duplicated(keys + ["policy"]).any()
    assert data.rights_conservation_holds.all()
    assert (data.risk_cap_violation == 0).all()
    metrics = [
        protocol["primary"],
        "unique_completion_per_generated",
        "settled_per_generated",
        "exposure_per_1000_requests",
        "forwarded_bytes",
        "transfers_locked_final",
    ]
    means = data.groupby(["context", "policy"])[metrics].mean()
    means.groupby("policy").mean().to_csv(out / "policy_means.csv")
    rng = np.random.default_rng(9610609)
    sample = rng.integers(0, 23, size=(9999, 23))
    contrasts = []
    for policy in protocol["policies"][1:]:
        delta = means.xs(policy, level="policy") - means.xs("robust_static", level="policy")
        delta.to_csv(out / f"incident_deltas_{policy}.csv")
        for metric in metrics:
            values = delta[metric].to_numpy()
            low, high = np.quantile(values[sample].mean(axis=1), [0.025, 0.975])
            contrasts.append(
                dict(
                    policy=policy,
                    metric=metric,
                    delta=values.mean(),
                    low=low,
                    high=high,
                    positive=int((values > 0).sum()),
                    negative=int((values < 0).sum()),
                )
            )
    pd.DataFrame(contrasts).to_csv(out / "contrasts.csv", index=False)
    hashes = {
        str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(out.glob("*"))
        if p.is_file() and p.name != "checksums.json"
    }
    (out / "checksums.json").write_text(json.dumps(hashes, indent=2))
    print(pd.DataFrame(contrasts).to_string(index=False), flush=True)


if __name__ == "__main__":
    main()
