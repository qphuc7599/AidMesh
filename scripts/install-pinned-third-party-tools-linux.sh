#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOLC_PATH="${ROOT}/tools/solc/solc-static-linux-v0.8.36"
TLA_PATH="${ROOT}/tools/tla2tools.jar"
SOLC_URL="https://github.com/argotorg/solidity/releases/download/v0.8.36/solc-static-linux"
TLA_URL="https://github.com/tlaplus/tlaplus/releases/download/v1.7.4/tla2tools.jar"
SOLC_SHA256="c8d35afdddc3cd2743ee88b8f25e0fecd16e2bdd5f2120f37e52cd9cc45ae0e6"
TLA_SHA256="936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88"

download_and_verify() {
  local name="$1"
  local url="$2"
  local destination="$3"
  local expected="$4"
  local temporary="${destination}.download"

  mkdir -p "$(dirname "${destination}")"
  if [[ -f "${destination}" ]]; then
    local existing
    existing="$(sha256sum "${destination}" | awk '{print $1}')"
    if [[ "${existing}" == "${expected}" ]]; then
      printf 'OK (already present): %s\n' "${name}"
      return
    fi
    printf 'Hash mismatch for existing file: %s\n' "${destination}" >&2
    exit 2
  fi

  curl --fail --location --retry 4 --output "${temporary}" "${url}"
  local actual
  actual="$(sha256sum "${temporary}" | awk '{print $1}')"
  if [[ "${actual}" != "${expected}" ]]; then
    printf 'Downloaded hash mismatch for %s: expected %s, got %s\n' \
      "${name}" "${expected}" "${actual}" >&2
    exit 3
  fi
  mv "${temporary}" "${destination}"
  printf 'Installed and verified: %s\n' "${name}"
}

download_and_verify "Solidity 0.8.36 Linux compiler" \
  "${SOLC_URL}" "${SOLC_PATH}" "${SOLC_SHA256}"
chmod 0755 "${SOLC_PATH}"
download_and_verify "TLA+ tools 1.7.4 / TLC rev. 5a47802" \
  "${TLA_URL}" "${TLA_PATH}" "${TLA_SHA256}"
