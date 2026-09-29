#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
JAR_PATH="${TLA2TOOLS_JAR:-${ROOT}/tools/tla2tools.jar}"
OUTPUT="${1:-${ROOT}/results/extension_20260724/formal/tlc_integrated_protocol_faithful_linux}"
WORKERS="${2:-4}"

if [[ ! -f "${JAR_PATH}" ]]; then
  printf 'Khong tim thay tla2tools.jar: %s\n' "${JAR_PATH}" >&2
  exit 2
fi
mkdir -p "${OUTPUT}"

CASES=(
  "integrated-safe-mixed-path|AidMeshIntegratedSafe.cfg|safe|"
  "integrated-safe-conflict|AidMeshIntegratedSafeConflict.cfg|safe|"
  "integrated-unsafe-bypass|AidMeshIntegratedUnsafeBypass.cfg|unsafe|SettlementRequiresRelease"
  "integrated-unsafe-conservation|AidMeshIntegratedUnsafeConservation.cfg|unsafe|FallbackConservation"
  "integrated-unsafe-settlement|AidMeshIntegratedUnsafeSettlement.cfg|unsafe|AtMostOnceSettlement"
)

SUMMARY_TSV="${OUTPUT}/tlc_integrated_summary.tsv"
printf 'case\tconfig\tnegative_control\texpected_invariant\texit_code\tpassed\tgenerated_states\tdistinct_states\tdepth\telapsed_seconds\tlog\n' > "${SUMMARY_TSV}"

pushd "${ROOT}/formal" >/dev/null
for entry in "${CASES[@]}"; do
  IFS='|' read -r name config kind expected <<< "${entry}"
  case_root="${OUTPUT}/${name}"
  state_root="${case_root}/states"
  log_path="${case_root}/tlc.log"
  mkdir -p "${state_root}"
  started="$(date +%s)"
  set +e
  java -XX:+UseParallelGC -cp "${JAR_PATH}" tlc2.TLC \
    -deadlock -workers "${WORKERS}" -metadir "${state_root}" \
    -config "${config}" AidMeshIntegrated.tla >"${log_path}" 2>&1
  exit_code=$?
  set -e
  elapsed="$(( $(date +%s) - started ))"

  passed=false
  if [[ "${kind}" == "safe" ]]; then
    if [[ "${exit_code}" -eq 0 ]] &&
      grep -q "Model checking completed. No error has been found" "${log_path}"; then
      passed=true
    fi
  elif [[ "${exit_code}" -ne 0 ]] &&
    grep -q "Error: Invariant" "${log_path}" &&
    grep -q "${expected}" "${log_path}"; then
    passed=true
  fi

  states="$(
    grep -Eo '[0-9,]+ states generated, [0-9,]+ distinct states found' "${log_path}" |
      tail -n 1 || true
  )"
  generated="$(printf '%s' "${states}" | sed -E 's/^([0-9,]+) states generated.*/\1/' | tr -d ',')"
  distinct="$(printf '%s' "${states}" | sed -E 's/.* ([0-9,]+) distinct states found$/\1/' | tr -d ',')"
  depth="$(
    grep -Eo 'depth of the complete state graph search is [0-9]+' "${log_path}" |
      tail -n 1 | awk '{print $NF}' || true
  )"
  [[ "${states}" == *"states generated"* ]] || generated=""
  [[ "${states}" == *"distinct states found"* ]] || distinct=""
  negative=false
  [[ "${kind}" == "unsafe" ]] && negative=true
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "${name}" "${config}" "${negative}" "${expected}" "${exit_code}" "${passed}" \
    "${generated}" "${distinct}" "${depth}" "${elapsed}" "${log_path}" >> "${SUMMARY_TSV}"
  if [[ "${passed}" != "true" ]]; then
    printf 'TLC case khong dat tieu chi: %s\n' "${name}" >&2
    exit 4
  fi
done
popd >/dev/null

python - "${SUMMARY_TSV}" "${OUTPUT}/tlc_integrated_summary.json" <<'PY'
import csv
import json
import pathlib
import sys

source = pathlib.Path(sys.argv[1])
destination = pathlib.Path(sys.argv[2])
rows = []
with source.open(encoding="utf-8", newline="") as handle:
    for raw in csv.DictReader(handle, delimiter="\t"):
        rows.append(
            {
                "case": raw["case"],
                "module": "AidMeshIntegrated.tla",
                "config": raw["config"],
                "negative_control": raw["negative_control"] == "true",
                "expected_invariant": raw["expected_invariant"],
                "exit_code": int(raw["exit_code"]),
                "passed": raw["passed"] == "true",
                "generated_states": int(raw["generated_states"]) if raw["generated_states"] else None,
                "distinct_states": int(raw["distinct_states"]) if raw["distinct_states"] else None,
                "depth": int(raw["depth"]) if raw["depth"] else None,
                "elapsed_seconds": float(raw["elapsed_seconds"]),
                "log": raw["log"],
            }
        )
destination.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
PY

CHECKSUM_PATH="${OUTPUT}/CHECKSUMS.sha256"
(
  cd "${OUTPUT}"
  find . -type f ! -path '*/states/*' \
    ! -name 'CHECKSUMS.sha256' -print0 |
    sort -z |
    xargs -0 sha256sum
) > "${CHECKSUM_PATH}"
printf '%s\n%s\n' "${OUTPUT}/tlc_integrated_summary.json" "${CHECKSUM_PATH}"
