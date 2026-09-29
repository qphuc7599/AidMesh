#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT_REL="second_host/reproduction_linux"
REFERENCE_FINGERPRINT=""
SKIP_FULL_E2E=false
SETUP=false
ZK_ITERATIONS=65

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --output) OUTPUT_REL="$2"; shift 2 ;;
    --reference-host-fingerprint) REFERENCE_FINGERPRINT="${2,,}"; shift 2 ;;
    --skip-full-e2e) SKIP_FULL_E2E=true; shift ;;
    --setup) SETUP=true; shift ;;
    --zk-iterations) ZK_ITERATIONS="$2"; shift 2 ;;
    *) printf 'Tham so khong hop le: %s\n' "$1" >&2; exit 2 ;;
  esac
done

cd "${ROOT}"
if [[ "${SETUP}" == "true" ]]; then
  PYTHON_BOOTSTRAP="${AIDMESH_PYTHON_BOOTSTRAP:-python3}"
  "${PYTHON_BOOTSTRAP}" -m venv .venv-linux
  .venv-linux/bin/python -m pip install --upgrade "pip==26.1.2"
  if [[ -n "${AIDMESH_WHEELHOUSE:-}" ]]; then
    .venv-linux/bin/python -m pip install --no-index \
      --find-links "${AIDMESH_WHEELHOUSE}" -r requirements-linux.lock
  else
    .venv-linux/bin/python -m pip install -r requirements-linux.lock
  fi
  if [[ "${AIDMESH_NPM_OFFLINE:-false}" == "true" ]]; then
    npm ci --offline
  else
    npm ci
  fi
  bash scripts/install-pinned-third-party-tools-linux.sh
fi

PYTHON="${ROOT}/.venv-linux/bin/python"
SOLC="${ROOT}/tools/solc/solc-static-linux-v0.8.36"
TLA="${ROOT}/tools/tla2tools.jar"
for required in "${PYTHON}" "${SOLC}" "${TLA}"; do
  if [[ ! -e "${required}" ]]; then
    printf 'Thieu dependency: %s. Chay lai voi --setup.\n' "${required}" >&2
    exit 3
  fi
done
export AIDMESH_SOLC="${SOLC}"
export TLA2TOOLS_JAR="${TLA}"

OUTPUT="${ROOT}/${OUTPUT_REL}"
mkdir -p "${OUTPUT}"
COMMAND_LOG="${OUTPUT}/commands.log"
STEPS_TSV="${OUTPUT}/steps.tsv"
printf 'name\tstatus\tstarted_utc\tfinished_utc\telapsed_seconds\n' > "${STEPS_TSV}"

machine_material="$(
  {
    hostname
    cat /etc/machine-id 2>/dev/null || true
    lscpu 2>/dev/null | grep -E '^(Architecture|Model name|CPU\\(s\\)):' || true
  } | tr '\n' '|'
)"
HOST_FINGERPRINT="$(printf '%s' "${machine_material}" | sha256sum | awk '{print $1}')"
if [[ -n "${REFERENCE_FINGERPRINT}" && "${HOST_FINGERPRINT}" == "${REFERENCE_FINGERPRINT}" ]]; then
  printf 'Host fingerprint trung may phat trien; day khong phai second-host.\n' >&2
  exit 4
fi

{
  printf 'captured_utc=%s\n' "$(date -u +%FT%TZ)"
  printf 'host_fingerprint_sha256=%s\n' "${HOST_FINGERPRINT}"
  printf 'kernel=%s\n' "$(uname -srvmo)"
  printf 'cpu=%s\n' "$(lscpu | awk -F: '/Model name/ {gsub(/^ +/, "", $2); print $2; exit}')"
  printf 'ram_bytes=%s\n' "$(awk '/MemTotal/ {print $2 * 1024}' /proc/meminfo)"
  printf 'python=%s\n' "$("${PYTHON}" --version 2>&1)"
  printf 'node=%s\n' "$(node --version 2>&1)"
  printf 'npm=%s\n' "$(npm --version 2>&1)"
  printf 'java=%s\n' "$(java -version 2>&1 | tr '\n' '|')"
  printf 'solc=%s\n' "$("${SOLC}" --version 2>&1 | tr '\n' '|')"
  printf 'slurm_job_id=%s\n' "${SLURM_JOB_ID:-not-slurm}"
} > "${OUTPUT}/environment.txt"

run_step() {
  local name="$1"
  shift
  local started_epoch started_utc finished_epoch finished_utc status elapsed
  started_epoch="$(date +%s)"
  started_utc="$(date -u +%FT%TZ)"
  printf '[%s] START %s\n' "${started_utc}" "${name}" >> "${COMMAND_LOG}"
  set +e
  "$@" > >(tee -a "${OUTPUT}/${name}.log") 2> >(tee -a "${OUTPUT}/${name}.log" >&2)
  local exit_code=$?
  set -e
  finished_epoch="$(date +%s)"
  finished_utc="$(date -u +%FT%TZ)"
  elapsed="$((finished_epoch - started_epoch))"
  status=pass
  [[ "${exit_code}" -eq 0 ]] || status=fail
  printf '%s\t%s\t%s\t%s\t%s\n' \
    "${name}" "${status}" "${started_utc}" "${finished_utc}" "${elapsed}" >> "${STEPS_TSV}"
  printf '[%s] END %s %s\n' "${finished_utc}" "${name}" "${status}" >> "${COMMAND_LOG}"
  if [[ "${status}" != "pass" ]]; then
    printf 'Buoc %s that bai, exit code %s.\n' "${name}" "${exit_code}" >&2
    exit "${exit_code}"
  fi
}

run_step input_preflight "${PYTHON}" scripts/preflight-second-host-inputs.py \
  --root "${ROOT}" --output "${OUTPUT}/input_preflight.json"
run_step pytest "${PYTHON}" -m pytest -q
run_step tlc_integrated bash scripts/run-tla-integrated.sh \
  "${OUTPUT}/formal/tlc_integrated" "${SLURM_CPUS_PER_TASK:-4}"
run_step protocol_refinement "${PYTHON}" -m aidmesh.cli protocol-refinement \
  --output "${OUTPUT}/formal/protocol_refinement" --traces 256 --steps 64
run_step zk_p1_v2_tagged "${PYTHON}" -m aidmesh.cli zk-bench \
  --iterations "${ZK_ITERATIONS}" --warmup 5 --output "${OUTPUT}/zk_p1_v2_tagged.csv"
run_step evm_solidity_0_8_36 "${PYTHON}" -m aidmesh.cli evm-bench \
  --output "${OUTPUT}/evm"
run_step heldout_primary_baselines "${PYTHON}" -m aidmesh.cli heldout-temporal-frontier \
  --trace-root external/sociopatterns/normalized \
  --output "${OUTPUT}/heldout_primary_baselines" \
  --seed-start 7301 --seeds 8 --buffer-bytes 98304
run_step privacy_sparse_cover "${PYTHON}" -m aidmesh.cli supporting-privacy-pareto \
  --registry configs/supporting_2026Q3/privacy_pareto/registry_sparse_preregistered.yaml \
  --output "${OUTPUT}/privacy_sparse_cover"
run_step distributed_rights "${PYTHON}" -m aidmesh.cli distributed-rights-campaign \
  --output "${OUTPUT}/distributed_rights" --trials 8

if [[ "${SKIP_FULL_E2E}" != "true" ]]; then
  E2E="${OUTPUT}/e2e_p1_v2_tagged_full"
  run_step e2e_p1_v2_tagged_full "${PYTHON}" -m aidmesh.cli e2e-local-run \
    --registry configs/e2e/registry_p1_v2_tagged_full.yaml \
    --trace-root external/sociopatterns/normalized --output "${E2E}"
  run_step e2e_analysis "${PYTHON}" -m aidmesh.cli e2e-local-analyze \
    --input "${E2E}" --output "${E2E}/analysis"
  run_step e2e_checksum_verify "${PYTHON}" -m aidmesh.cli e2e-local-verify --input "${E2E}"
  run_step integrated_trace_refinement "${PYTHON}" -m aidmesh.formal.integrated_refinement \
    --campaign "${E2E}" --output "${OUTPUT}/formal/integrated_trace_refinement"
fi

"${PYTHON}" - "${STEPS_TSV}" "${OUTPUT}/second_host_result.json" \
  "${HOST_FINGERPRINT}" "${REFERENCE_FINGERPRINT}" "${SKIP_FULL_E2E}" <<'PY'
import csv
import json
import pathlib
import sys

steps_path, output_path, host, reference, skipped = sys.argv[1:]
with pathlib.Path(steps_path).open(encoding="utf-8", newline="") as handle:
    steps = list(csv.DictReader(handle, delimiter="\t"))
for step in steps:
    step["elapsed_seconds"] = int(step["elapsed_seconds"])
result = {
    "schema_version": 1,
    "status": "pass",
    "independent_claim": "Reviewer must complete and sign reviewer_declaration.md",
    "canonical_profile": "aidmesh-voucher-p1-groth16-bn128-domain-v2-tagged",
    "expected_constraints": 31011,
    "expected_public_signals": 17,
    "expected_fault_controls": 15,
    "full_e2e_skipped": skipped == "true",
    "host_fingerprint_sha256": host,
    "reference_host_fingerprint_sha256": reference,
    "fingerprint_differs_from_reference": not reference or host != reference,
    "steps": steps,
}
pathlib.Path(output_path).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
PY

(
  cd "${OUTPUT}"
  find . -type f ! -path '*/states/*' ! -name 'CHECKSUMS.sha256' -print0 |
    sort -z | xargs -0 sha256sum
) > "${OUTPUT}/CHECKSUMS.sha256"
printf 'Second-host reproduction hoan tat: %s\n' "${OUTPUT}"
