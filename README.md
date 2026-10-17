# AidMesh experimental source and research evidence

AidMesh studies private, bounded-risk offline vouchers for partitioned edge, fog
and cloud relief networks. This repository contains the protocol implementation,
simulators, exact allocation checks, recovery and receipt gateways, Solidity
settlement, cryptographic circuits, formal models and experiment runners.

**The complete retained artifact is distributed in
[release artifact-2026-10-17](https://github.com/qphuc7599/AidMesh/releases/tag/artifact-2026-10-17).**
The main branch exposes source and final aggregate results. The release adds
complete per-run evidence, historical reference events and development ZK
artifacts as separate overlay archives, with archive and per-file SHA-256 checksums.
No experiments were rerun to prepare this publication.

The [earlier source-only Zenodo record](https://zenodo.org/records/23037290)
has DOI [10.5281/zenodo.23037290](https://doi.org/10.5281/zenodo.23037290).
That DOI identifies the September source snapshot, **not this expanded evidence
release**. A DOI for this release must be verified after Zenodo processes it.

## Inspect the final evidence

| Study | Final data and recorded result | Reproduction entry point |
| --- | --- | --- |
| Original design, replay and holdout | [Evidence and decisions](results/postreview_original_registry_20260910/POST_RUN_EVIDENCE.json): 2,650 replay cells and 2,650 disjoint-seed holdout cells. Core completion difference +0.21306. The 6-hour and witness-0.6 reversals, wrong-direction RMST and null tight-cap completion remain recorded. | `scripts/run-compressed-confirmatory-registry-20260910.py` |
| Wang-style seen tags | [Final output](results/t13_wang_seen_tag_matched_20260929/): 1,450 cells, five methods, nine scenarios and 21 contrasts. At the core, AidMesh minus Wang-6h completion is -0.0402 and duplicate exposure is -91.26. The witness-0.9 exposure reversal is retained. | CLI and registry in the runbook |
| Allocation and H3 | [Fixed replication](results/h3_fixed_replication_20260928/summary.json): 640 seed clusters, 5,760 conditions and 34,560 method rows. Weighted-utility loss 2.44768%, one-sided 95% upper bound 2.56112% against a 3% margin. | `scripts/run-h3-fixed-replication-20260928.py` |
| External validity | [Fixed analysis](results/external_validity_three_family_20260928/fixed_analysis.json): 150 context-seed shards, three families and 12,240 policy rows. Transfer improves over periodic sync in all families, but optimized static placement remains better. | `aidmesh.experiments.independent_trace_campaign` |
| Protocol baselines | [Summary](results/matched_protocol_baselines_20260910/summary.json): 1,080 runs, nine scenarios, four methods and 30 paired seeds. [Baseline eligibility audit](results/baseline_validation_20260928/audit.json). | `scripts/run-matched-protocol-baselines-20260910.py` |
| Receipt controller | [Summary](results/matched_receipt_controller_20260910/summary.json): 1,080 runs on 360 matched workloads; lower median delay in all 12 primary cells. | `scripts/run-matched-receipt-controller-20260910.py` |
| Recovery and staged atomic settlement | [Final settlement](results/geth_eip7825_atomic100_attackmatrix_final4_20260910/summary.json): 100 vouchers, 50 ACCEPT and 50 RECLAIM, ten lock-only stages and one finalizer; credits 100/50; replay and incomplete finalize revert. | `scripts/run-integrated-recovery-batch.py` and settlement audit scripts |
| Proofs and aggregate certificates | [Timings and controls](results/geth_eip7825_atomic100_attackmatrix_final4_20260910/aggregate_pipeline_benchmark.json): 100 trials per committee profile and 300 rejected certificate mutations. | `scripts/benchmark-final-aggregate-pipeline-20260910.py` |
| Formal obligations | [Portfolio](results/postreview_formal_20260910/portfolio.json): 14 safe Tamarin lemmas, four Z3 conditions and six unsafe counterexamples, with bounded TLC/refinement evidence. | `formal/`, `aidmesh/formal/`, `scripts/check-*.py` |
| Crash/restart and privacy | [Crash campaign](results/release_crash_matrix_20260926_py312/), [privacy composition](results/staged_privacy_20260909_v2/privacy_composition_summary.json) and [wire controls](results/public_wire_privacy_matrix_20260906/). | `scripts/run-release-crash-matrix-20260926.py`, `scripts/run-recovery-field-matrix.py` |

The exact final root list is in [RELEASE_SCOPE.json](artifacts/RELEASE_SCOPE.json).
The [evidence guide](EVIDENCE_GUIDE.md) explains provenance and invalidations.
The five [final result figures](figures/results/) are included as PDF, SVG and
PNG, copied byte-for-byte from the final recorded presentation. Their data inputs
are in the evidence ZIPs and their generator is `scripts/journal-result-figures.py`.

## Download and verify

Download the four ZIPs, `SHA256SUMS` and `FILE_MANIFEST.json` from the release:

| Archive | Contents |
| --- | --- |
| `aidmesh-source-2026-10-17.zip` | Source, registries, documentation, aggregate results and validation tools |
| `aidmesh-final-evidence-2026-10-17.zip` | Complete final runs, analyses, formal transcripts and execution receipts |
| `aidmesh-historical-reference-2026-10-17.zip` | Original simulator events and retained supporting/predecessor evidence |
| `aidmesh-development-zk-2026-10-17.zip` | Development proving keys, verification keys, R1CS/WASM, generated verifier and setup transcripts |

Extract all four into one empty directory, or overlay the three evidence archives
onto a clone of this release. All use the same root-relative layout.

```bash
python scripts/verify-public-artifact.py --archive-dir /path/to/downloads
python scripts/verify-public-artifact.py --root /path/to/extracted-artifact
```

The verifier checks every archive and contained file. Hash verification does not
rerun experiments or establish a security claim.

## Setup

Use Python 3.12 and Node.js 22. See [EXPERIMENT_RUNBOOK.md](EXPERIMENT_RUNBOOK.md)
for the recorded environment, external tools and reproduction commands.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev,crypto,evm]"
npm ci
python -m aidmesh.cli --help
python -m pytest -q tests/unit
```

On Windows activate with `.venv\Scripts\Activate.ps1`. Python 3.13 is also
supported by package metadata. The lock files retain the recorded dependency
snapshots. Solidity/Geth, TLC, Tamarin/Maude and external traces require separate
tools/inputs. Tests requiring unavailable manuscripts or external traces skip.

## Source map

| Component | Location |
| --- | --- |
| DTN simulation, matched policies and seen tags | `aidmesh/simulator/`, `aidmesh/experiments/` |
| Quorum, rights, recovery, actuator claim and CLOSED receipt | `aidmesh/protocol/` |
| Exact full-box leximin allocation | `aidmesh/allocator/` |
| Admission, Groth16 and BLS | `aidmesh/crypto/`, `zk/circuits/` |
| Settlement and semantic authorization | `contracts/src/` |
| TLA+, Tamarin and executable refinement | `formal/`, `aidmesh/formal/` |
| Fixed designs and analyses | `configs/`, `schemas/`, `aidmesh/analysis/` |
| Execution and figures | `scripts/`, especially `scripts/journal-result-figures.py` |
| Functional, property and EVM tests | `tests/` |

Dates and profile names preserve links to existing registries. Historical
authoring/packaging scripts are retained; the runbook identifies current runners.

## Scope

Snappy and synchronized seen tags are scoped research reconstructions, not the
original authors' executables. DPDRM and CSAR are related-work comparators, not
validated full-protocol baselines. Qualified disclosure privacy does not establish
traffic-analysis resistance. Formal bounded checks, local-EVM runs and process
crashes do not establish hardware-fault resistance, production trusted setup,
public-chain congestion tolerance or humanitarian field readiness.

External contact datasets remain with their providers. See
[DATA_SOURCES.md](DATA_SOURCES.md) and [LICENSE_SCOPE.md](LICENSE_SCOPE.md).
Research source and original fixtures retain Apache-2.0. Synthetic enrollment
secrets and test keys are intentionally public and must never be used in production.
