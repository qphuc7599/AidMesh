# Five-contributor Docker workflow simulation

> **Historical P1-v1 ceremony simulation.** Its R1CS/zkey/transcript is not the
> canonical P1-v2-tagged circuit and must not be used for current performance,
> security, or production-trust claims. The current tagged zkey remains a development
> SRS artifact unless a genuinely independent ceremony/audit is completed.

This directory provides a reproducible Groth16 phase-2 workflow using the frozen
AidMesh R1CS/PTAU and the repository-locked `snarkjs` 0.7.6 dependency. The Docker
base image is pinned by immutable multi-platform digest.

## Trust boundary

This is a **single-operator, single-host, five-container workflow simulation**.
It may assume the researcher behaves honestly for development and reproducibility.
It is not independent multiparty trust, not a production trusted setup, and not
evidence that toxic waste is unknowable. A real ceremony still requires independent
contributors on independently controlled hosts, plus an independent circuit audit.

## Completed simulation

Run `20260713T023942Z` completed all five sequential contributor containers using
Docker Engine 29.1.3 through Ubuntu WSL2. Every initial/intermediate/final zkey
verified against the frozen R1CS/PTAU, and transcript validation plus checksum
verification passed.

- Output: `zk/ceremony/output/20260713T023942Z/`
- Final zkey SHA-256:
  `2cbe10ec5d1ae5e0d2f74aa0654520bb64856609a28739374a8cc54a27d0730b`
- Verification key SHA-256:
  `93920cab6b7d5523175292dc0a50103c699f68e8317d167dcb32fa049edf4e7c`
- Solidity verifier SHA-256:
  `a44455d9be3f8b011fec6bbb6720ad69c172ee7cdb43f88938883095f354ee17`
- Transcript SHA-256:
  `7756e71e2ae38ec7d931e20a61341b93942eaddc3b8d302f9467ab858bcbed84`
- Final-key benchmark SHA-256:
  `05c1390bd1b278e65d45d5b189bbd2702d74a9149fd62c5e02f30d94f9c6af59`

The final-key benchmark generated and verified a proof and rejected 11/11 public,
4/4 private/Merkle, one malformed-proof, and 12/12 Solidity mutations.

Each logical contributor (`sim-node-1` through `sim-node-5`) runs sequentially in a
fresh `docker run --rm` container with no network, a read-only root filesystem,
dropped capabilities, and no-new-privileges. Entropy is generated inside that
container with `node:crypto.randomBytes(64)`, passed directly to the snarkjs library,
never placed on the command line, and not written to logs or files. The process clears
the mutable entropy buffer before the short-lived container exits.

## Prerequisites

- Docker Desktop/Engine with Linux-container support and a running daemon. If no
  native Windows Docker CLI is on PATH, the script can use Docker in the `Ubuntu`
  WSL distribution.
- PowerShell.
- Python 3.12+ (the repository virtual environment is used when present).
- Frozen inputs:
  - `zk/build/aidmesh_voucher.r1cs`
  - `zk/public/powersOfTau28_hez_final_15.ptau`

The script fails before setup if either input hash differs from the expected frozen
hash. It never falls back to a host-only ceremony.

## Run

From the repository root:

```powershell
.\zk\ceremony\Run-Ceremony.ps1
```

To select a fresh output directory:

```powershell
.\zk\ceremony\Run-Ceremony.ps1 `
  -OutputDirectory zk\ceremony\output\my-run
```

The default output is `zk/ceremony/output/<UTC-run-id>/`. Existing directories are
never overwritten. The canonical development key and Solidity verifier are never
modified.

Successful output contains:

- the initial zkey and all five chain steps;
- `aidmesh_final_simulated.zkey`;
- `verification_key.json`;
- `AidMeshGroth16Verifier.sol`;
- `transcript.json`;
- `SUMMARY.md`;
- `CHECKSUMS.txt`.

Validate any completed transcript independently:

```powershell
.\.venv\Scripts\python.exe zk\ceremony\validate_transcript.py `
  --transcript zk\ceremony\output\<run-id>\transcript.json `
  --output-root zk\ceremony\output\<run-id>
```

Run the proof, mutation, and exported-Solidity checks against a selected simulated
key without replacing canonical development artifacts:

```powershell
.\.venv\Scripts\python.exe -m aidmesh.cli zk-bench `
  --warmup 0 --iterations 1 `
  --zkey zk\ceremony\output\<run-id>\aidmesh_final_simulated.zkey `
  --verification-key zk\ceremony\output\<run-id>\verification_key.json `
  --solidity-verifier zk\ceremony\output\<run-id>\AidMeshGroth16Verifier.sol `
  --output zk\ceremony\output\<run-id>\zk_benchmark_final.csv
```

The transcript records only non-secret metadata and is stamped with
`single_operator_single_host_simulation=true` and
`production_trusted_setup=false`.
