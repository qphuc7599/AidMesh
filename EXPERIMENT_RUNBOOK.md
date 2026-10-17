# AidMesh experiment runbook

## 1. Material and environment

Clone the release tag `artifact-2026-10-17` and overlay the final-evidence,
historical-reference and development-zk ZIPs, or extract all four ZIPs into an
empty directory. Verify them before running code:

```bash
python scripts/verify-public-artifact.py --archive-dir /path/to/downloads
python scripts/verify-public-artifact.py --root .
```

Use Python 3.12, Node.js 22 and the included lock files. The recorded external
tools were Geth 1.17.5, Solidity 0.8.36 (Prague target), Tamarin 1.12.0,
Maude 3.5.1 and TLC 2.19. The installer is
`scripts/install-pinned-third-party-tools-linux.sh`. Some historical tools expect
`tools/solc/solc-static-linux-v0.8.36` and `tools/tla2tools.jar`.
For EVM tests configure `AIDMESH_SOLC` if the compiler is installed elsewhere.
Tool installers download upstream packages; they are not included binaries.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev,crypto,evm]"
npm ci
python -m aidmesh.cli --help
python -m pytest -q tests/unit
```

For the exact recorded dependency sets use `requirements.lock` on Windows or
`requirements-linux.lock` on Linux. Hardware-dependent timings are not expected
to be identical on another machine. Missing external traces or manuscripts cause
their explicitly dependent unit tests to skip; that is not a passed trace study.

## 2. Inspect recorded evidence before rerunning

The archive preserves original paths because registries, receipts and signatures
bind their inputs. Use the final paths listed in `artifacts/RELEASE_SCOPE.json`.
Historical hashes inside original freeze records continue to identify the original
source/configuration; they are not rewritten to match the packaging commit.
The new per-file manifest identifies exactly what this release distributes.

Several runners refuse to overwrite outputs; others use fixed paths. Use a second
clean checkout or an empty new output directory. Never run a fixed-output script
over the downloaded evidence, edit endpoints or seeds and then label it the
registered study. Every reproduction below is a new run, not new holdout discovery.

## 3. Original registry replay and holdout

```bash
python scripts/run-compressed-confirmatory-registry-20260910.py \
  --registry configs/postreview_20260910/original_registry_replay_v1.yaml \
  --output results/reproduction_replay --software-snapshot artifact-2026-10-17 --workers 8
python scripts/run-compressed-confirmatory-registry-20260910.py \
  --registry configs/postreview_20260910/original_registry_holdout_v1.yaml \
  --output results/reproduction_holdout --software-snapshot artifact-2026-10-17 --workers 8
python -m aidmesh.cli confirmatory-analyze \
  --registry configs/postreview_20260910/original_registry_holdout_v1.yaml \
  --input results/reproduction_holdout --output results/reproduction_holdout_analysis
```

The recorded campaigns have 2,650 cells each. Retain the replay's historical
semantic mismatches and the holdout's negative/null contrasts. The original
synthetic reference events are in `raw/confirmatory/` (historical-reference ZIP).

## 4. Synchronized seen-tag baseline (T13)

```bash
python -m aidmesh.cli confirmatory-run \
  --registry configs/postreview_20260929/t13_wang_seen_tag_matched_physical_v2.yaml \
  --output results/reproduction_t13_raw --software-commit artifact-2026-10-17 --workers 8
python -m aidmesh.cli confirmatory-analyze \
  --registry configs/postreview_20260929/t13_wang_seen_tag_matched_physical_v2.yaml \
  --input results/reproduction_t13_raw --output results/reproduction_t13_analysis
```

Check 1,450 cells, all five methods and all 21 endpoint contrasts. Use physical
duplicate exposure at handoff. The 6-hour profile is primary; 1/24-hour profiles
are sensitivity bounds. The earlier settlement-endpoint run is invalidated.

## 5. H3 fixed replication

Use a new output directory for all three phases:

```bash
python scripts/run-h3-fixed-replication-20260928.py freeze \
  --registry configs/postreview_20260928/h3_fixed_replication.json --output results/reproduction_h3
python scripts/run-h3-fixed-replication-20260928.py run \
  --registry configs/postreview_20260928/h3_fixed_replication.json --output results/reproduction_h3 --workers 8
python scripts/run-h3-fixed-replication-20260928.py analyze \
  --registry configs/postreview_20260928/h3_fixed_replication.json --output results/reproduction_h3
```

Preserve the seed-cluster unit and fixed 3% criterion. Recorded original-contract
analysis is separate from the weighted-utility criterion.

## 6. External traces, matched protocols and controller

Acquire the exact external inputs in `DATA_SOURCES.md`, then use:

```bash
python -m aidmesh.cli independent-trace-campaign \
  --registry configs/postreview_20260928/external_validity_three_family.yaml \
  --output results/reproduction_three_family
```

The matched protocol/controller runners are
`scripts/run-matched-protocol-baselines-20260910.py` and
`scripts/run-matched-receipt-controller-20260910.py`. Inspect each script's fixed
output root first and use a fresh checkout. Baseline validation and Snappy component
reproduction are in `scripts/run-snappy-validation-20260928.py` and the baseline
audit scripts. A matched local controller comparison is distinct from the routing
component grid's null result.

## 7. Proofs, recovery and settlement

Extract the development-zk archive before real-proof tests. Both historical tagged
and value-admission profiles are retained; do not mix public signal order,
verifier, key or packet profile. `scripts/compile-zk.mjs` can rebuild the default
circuit using the downloaded public powers-of-tau input. The final value-admission
profile is under `zk/build_v3/`.

Entry points include `scripts/run-value-admission-20260909.py`,
`scripts/run-integrated-recovery-batch.py`, `scripts/run-recovery-bpv7-gateway.py`,
`scripts/audit-atomic100-final-20260910.py`, `scripts/audit-staged-storage-20260910.py`
and `scripts/benchmark-final-aggregate-pipeline-20260910.py`.
Inspect positional arguments in each entry point before starting.

The archived final run binds
`results/value_admission_atomic100_attackmatrix_final4_20260910/` to
`results/geth_eip7825_atomic100_attackmatrix_final4_20260910/`. A fresh local Geth
profile needs a 60M block gas limit and one-second block period; transaction cap
checks remain separate. The settlement ablation runner documents nonce/address
recreation because signed evidence binds deployment addresses. No live funds or
public-chain credentials are required.

## 8. Formal checks, crashes and figures

Run Python bounded refinement checks with a new output path, for example:

```bash
python scripts/check-release-refinement.py results/reproduction_release_refinement.json
python scripts/run-release-crash-matrix-20260926.py \
  --output results/reproduction_crashes --repetitions 30
```

Tamarin/TLC commands are in `scripts/run-tamarin-v2.py`,
`scripts/run-tla-integrated.sh` and the obligation-specific `run-tla-*.ps1`
wrappers. Original safe logs and counterexamples are in the evidence archives.

For figures use only the latest generator:

```bash
mkdir -p overleaf/figs
python scripts/journal-result-figures.py
```

This regenerates five result figures from the recorded final evidence. The output
directory is an artifact of the existing script interface; no manuscript is
included. Older restyle scripts do not represent the current presentation.

## 9. Historical utilities

Manuscript-editing, release-assembly and historical audit scripts are retained
for source completeness. Some require the intentionally excluded paper, an old
directory layout or local runtime paths. In particular the old postreview-release
verifier includes manuscript checks and is not the public-package integrity
verifier. Use `verify-public-artifact.py` for this expanded release.
