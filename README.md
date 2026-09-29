# AidMesh experimental source

Source code for modeling and evaluating private, bounded-risk offline vouchers
under network partitions. This repository contains the implementation, experiment
designs, formal models and tests. Manuscripts, measured results, figures, downloaded
datasets, compiled artifacts and cryptographic setup outputs are not included.

## Implementation map

| Component | Source |
| --- | --- |
| Shared-resource DTN simulation, authorization and seen-tag baselines | `aidmesh/simulator/`, `aidmesh/experiments/` |
| Quorum certificates, conserved rights, transfer recovery and durable receipts | `aidmesh/protocol/` |
| Full-box leximin allocation, baselines and exact certificates | `aidmesh/allocator/` |
| Credential admission, Groth16 integration and BLS aggregate certificates | `aidmesh/crypto/`, `zk/circuits/` |
| Settlement and semantic authorization gateways | `contracts/src/` |
| Privacy analysis, paired comparisons and statistical estimators | `aidmesh/privacy/`, `aidmesh/analysis/` |
| TLA+, Tamarin and executable verification conditions | `formal/`, `aidmesh/formal/` |
| Registered designs and input validation | `configs/`, `schemas/` |
| Experiment execution, checks and dependency bootstrapping | `scripts/`, `testbed/` |
| Unit, property and local-EVM tests | `tests/` |

The Wang-style seen-tag and Snappy components are research reconstructions with
explicit scope, not claims to be the original authors' implementations. The
upstream Coconut component benchmark requires the original external package.
Connected component checks, bounded model checks and process-crash experiments
are distinct from a physical deployment or a complete implementation proof.

## Setup and quick start

Use Python 3.12 or 3.13. Run commands from the repository root.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev,crypto,evm]"
python -m aidmesh.cli --help
python -m aidmesh.cli validate configs/development/smoke.yaml
python -m aidmesh.cli simulate configs/development/smoke.yaml --output results/smoke
python -m pytest -q tests/unit
```

On Windows, activate with `.venv\Scripts\Activate.ps1`. The two requirements lock
files retain the original Windows/Linux dependency snapshots; the editable
installation above uses the supported dependency ranges from `pyproject.toml`.
Some tests are skipped when they require separately generated Groth16 artifacts,
downloaded trace data, historical result inputs or the manuscript. Property and EVM
tests additionally require the pinned Solidity compiler and EVM extras.

Outputs are created locally in ignored directories. Small CSVs under `examples/`
are input fixtures, not measured results. Dates in script names identify fixed
experiment designs and preserve the link between runners and configurations.

## Experiments

### Matched seen-tag baseline

The current design uses the physical duplicate-exposure endpoint at handoff,
with 1 h, 6 h and 24 h seen-tag synchronization. The 6 h profile is primary.

```bash
python scripts/run-compressed-confirmatory-registry-20260910.py --registry configs/postreview_20260929/t13_wang_seen_tag_matched_physical_v2.yaml --output results/t13_wang_seen_tag_matched_20260929/raw --software-snapshot YOUR_GIT_COMMIT --workers 4
python scripts/analyze-t13-wang-seen-tag-20260929.py
```

Replace `YOUR_GIT_COMMIT` with `git rev-parse HEAD`. The runner retains failed
cells, validates event checksums and resumes only completed cells. The registry
contains all nine scenarios and five methods.

### Reference and disjoint holdout

```bash
python scripts/freeze-postreview-original-registries-20260910.py
python scripts/run-compressed-confirmatory-registry-20260910.py --registry configs/postreview_20260910/original_registry_replay_v1.yaml --output results/postreview_original_registry_20260910/replay --software-snapshot YOUR_GIT_COMMIT --workers 4
python scripts/run-compressed-confirmatory-registry-20260910.py --registry configs/postreview_20260910/original_registry_holdout_v1.yaml --output results/postreview_original_registry_20260910/holdout --software-snapshot YOUR_GIT_COMMIT --workers 4
```

### Fixed allocation replication

```bash
python scripts/run-h3-fixed-replication-20260928.py freeze --registry configs/postreview_20260928/h3_fixed_replication.json --output results/h3_fixed_replication_20260928
python scripts/run-h3-fixed-replication-20260928.py run --registry configs/postreview_20260928/h3_fixed_replication.json --output results/h3_fixed_replication_20260928 --workers 4
python scripts/run-h3-fixed-replication-20260928.py analyze --registry configs/postreview_20260928/h3_fixed_replication.json --output results/h3_fixed_replication_20260928
```

### Receipt control, crash recovery and baseline validation

```bash
python scripts/run-matched-receipt-controller-20260910.py
python scripts/run-release-crash-matrix-20260926.py --output results/release_crash_matrix --repetitions 30
python scripts/run-snappy-validation-20260928.py
```

These scripts may take substantially longer than the smoke run. The full design
should be executed without changing stopping rules based on intermediate results.

## External trace experiments

Raw traces and normalized contacts must be obtained separately. Their importers
are in `aidmesh/experiments/trace_preprocess.py`; source URLs and interpretation
rules are recorded there. Designs under `configs/trace_families/` and
`configs/postreview_20260928/external_validity_three_family.yaml` specify expected
paths for Asturies, SocioPatterns, UMass DieselNet and ENE5AI inputs. Use
`python -m aidmesh.cli --help` to locate the import and trace-family commands.
Analysis scripts that read earlier results require those results to be generated
first; this source-only repository does not include the historical measurements.

## Solidity, ZK and transport prerequisites

- `npm ci` installs the pinned Circom/snarkjs JavaScript dependencies.
- `scripts/Install-PinnedThirdPartyTools.ps1` (Windows) or
  `scripts/install-pinned-third-party-tools-linux.sh` downloads checksum-verified
  Solidity 0.8.36 and TLC tools. They are not vendored.
- `npm run zk:compile` uses `CIRCOM2_BIN` or a native Circom installation in
  `~/.cargo/bin`. Input generation is available through `npm run zk:inputs`.
- Groth16 benchmark runs need matching circuit builds, setup keys, verification
  keys and generated Solidity verifiers. These outputs are intentionally absent;
  inspect `zk/ceremony/Run-Ceremony.ps1` and the `build-v3-development-key.mjs`
  script before generating a local development setup. A local development setup
  is not a production trusted ceremony. Never mix artifacts from different circuits.
- `testbed/` runs independent dtn7-rs processes; its bootstrap script downloads
  the pinned transport implementation. Windows transport runners use WSL.
- Geth settlement runners require a local configured Geth client and freshly
  generated signed/proved inputs. Local fixture keys are test-only. Do not use
  real funds or production credentials for experiment fixtures.

## Formal checks

```bash
python -m pip install -r reproducibility/requirements-formal-vcs.txt
python formal/recovery_refinement_vcs.py
python formal/value_conservation_vcs.py
bash scripts/run-tla-integrated.sh
```

TLC needs Java. Tamarin models require Tamarin and Maude installed separately;
the supplied WSL wrappers expect `external/tamarin/tamarin-prover` and
`tools/maude-3.5.1/bin/maude`. Negative-control configurations intentionally produce
counterexamples and must not be classified as successful safety proofs.

## License

Apache-2.0, as specified in `LICENSE`. External datasets, tools and original
third-party implementations retain their own licenses and are not redistributed.
