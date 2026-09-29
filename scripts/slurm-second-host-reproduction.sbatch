#!/usr/bin/env bash
#SBATCH --job-name=aidmesh-reproduce
#SBATCH --partition=defq
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --time=72:00:00
#SBATCH --output=slurm-aidmesh-%j.out
#SBATCH --error=slurm-aidmesh-%j.err

set -euo pipefail

module purge
module load shared
module load python312
if [[ -n "${AIDMESH_JAVA_MODULE:-}" ]]; then
  module load "${AIDMESH_JAVA_MODULE}"
fi
if [[ -n "${AIDMESH_NODE_MODULE:-}" ]]; then
  module load "${AIDMESH_NODE_MODULE}"
fi

PROJECT_ROOT="${AIDMESH_PROJECT_ROOT:-${SLURM_SUBMIT_DIR}}"
cd "${PROJECT_ROOT}"
if command -v python3 >/dev/null 2>&1; then
  export AIDMESH_PYTHON_BOOTSTRAP=python3
else
  command -v python
  export AIDMESH_PYTHON_BOOTSTRAP=python
fi
command -v java
command -v node
command -v npm
command -v curl

OUTPUT="${AIDMESH_SECOND_HOST_OUTPUT:-second_host/uit_slurm_full}"
bash scripts/run-second-host-reproduction.sh \
  --setup \
  --output "${OUTPUT}" \
  --reference-host-fingerprint \
  "4cd5a70dfbe52f5e1147a9fcec0b592b46fe65cfe218154505e5a02858924fd3"
