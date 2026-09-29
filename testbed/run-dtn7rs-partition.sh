#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "usage: $0 <dtn7-rs-bin-dir> <output-dir>" >&2
  exit 2
fi

BIN_DIR="$(cd "$1" && pwd)"
OUTPUT_DIR="$2"
DTND="${BIN_DIR}/dtnd"
DTNSEND="${BIN_DIR}/dtnsend"
DTNRECV="${BIN_DIR}/dtnrecv"
DTNQUERY="${BIN_DIR}/dtnquery"

for executable in "$DTND" "$DTNSEND" "$DTNRECV" "$DTNQUERY"; do
  [[ -x "$executable" ]] || { echo "missing executable: $executable" >&2; exit 3; }
done

mkdir -p "$OUTPUT_DIR"
OUTPUT_DIR="$(cd "$OUTPUT_DIR" && pwd)"
RUNTIME_DIR="$(mktemp -d /tmp/aidmesh-dtn7rs-XXXXXX)"
mkdir -p "$RUNTIME_DIR/site-a" "$RUNTIME_DIR/relay" "$RUNTIME_DIR/site-b"

PIDS=()
cleanup() {
  for pid in "${PIDS[@]:-}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

start_node() {
  local name="$1"
  local web_port="$2"
  local mtcp_port="$3"
  local workdir="$4"
  shift 4
  "$DTND" \
    --nodeid "dtn://${name}/" \
    --workdir "$workdir" \
    --web-port "$web_port" \
    --db sled \
    --routing epidemic \
    --disable_nd \
    --cla "mtcp:port=${mtcp_port}:bind=127.0.0.1" \
    "$@" >"${OUTPUT_DIR}/${name}.log" 2>&1 &
  PIDS+=("$!")
}

# Edge nodes start while the ferry/relay is unavailable.  Their static-peer
# configuration remains causal: connections fail until the relay process exists.
start_node site-a 3101 17161 "$RUNTIME_DIR/site-a" \
  --endpoint outgoing \
  --static-peer mtcp://127.0.0.1:17162/relay
start_node site-b 3103 17163 "$RUNTIME_DIR/site-b" \
  --endpoint incoming \
  --static-peer mtcp://127.0.0.1:17162/relay
sleep 2

PAYLOAD_DIR="${OUTPUT_DIR}/payloads"
RECEIVED_DIR="${OUTPUT_DIR}/received"
mkdir -p "$PAYLOAD_DIR" "$RECEIVED_DIR"
python3 - "$PAYLOAD_DIR" <<'PY'
import json, sys
from pathlib import Path

root = Path(sys.argv[1])
for index, message_class in enumerate((
    "request", "quorum_request", "quorum_share", "right_transfer", "receipt"
)):
    (root / f"{index:02d}-{message_class}.json").write_text(json.dumps({
        "class": message_class,
        "correlation_id": "testbed-authorization-1",
        "nullifier": "testbed-nullifier-1",
        "right_id": "testbed-right-1",
        "epoch": 7,
        "value": 1,
    }, sort_keys=True) + "\n", encoding="utf-8")
PY

SEND_NS="$(date +%s%N)"
: >"${OUTPUT_DIR}/send.log"
for payload in "$PAYLOAD_DIR"/*.json; do
  "$DTNSEND" --port 3101 --sender dtn://site-a/outgoing \
    --receiver dtn://site-b/incoming --lifetime 120 "$payload" \
    >>"${OUTPUT_DIR}/send.log" 2>&1
done

# Preserve a real disconnected interval after the bundle has entered site A's
# persistent BPv7 store, then make the relay/ferry reachable.
sleep 3
start_node relay 3102 17162 "$RUNTIME_DIR/relay" \
  --static-peer mtcp://127.0.0.1:17161/site-a \
  --static-peer mtcp://127.0.0.1:17163/site-b

DELIVERED=0
for attempt in $(seq 1 40); do
  received="${RECEIVED_DIR}/$(printf '%02d' "$DELIVERED").json"
  if "$DTNRECV" --port 3103 --endpoint incoming --outfile "$received" \
      >"${OUTPUT_DIR}/receive.log" 2>&1; then
    if [[ -s "$received" ]]; then
      DELIVERED=$((DELIVERED + 1))
      if [[ "$DELIVERED" -eq 5 ]]; then
        break
      fi
    fi
  fi
  sleep 1
done
RECEIVE_NS="$(date +%s%N)"

for port in 3101 3102 3103; do
  "$DTNQUERY" --port "$port" peers >"${OUTPUT_DIR}/peers-${port}.txt" 2>&1 || true
  "$DTNQUERY" --port "$port" store >"${OUTPUT_DIR}/store-${port}.txt" 2>&1 || true
  "$DTNQUERY" --port "$port" info >"${OUTPUT_DIR}/info-${port}.txt" 2>&1 || true
done

python3 - "$PAYLOAD_DIR" "$RECEIVED_DIR" "$OUTPUT_DIR" "$DELIVERED" "$SEND_NS" "$RECEIVE_NS" <<'PY'
import hashlib
import json
import platform
import sys
from pathlib import Path

sent_root, received_root, output = map(Path, sys.argv[1:4])
delivered = int(sys.argv[4])
send_ns, receive_ns = map(int, sys.argv[5:7])
sent_hashes = sorted(hashlib.sha256(path.read_bytes()).hexdigest() for path in sent_root.glob("*.json"))
received_hashes = sorted(
    hashlib.sha256(path.read_bytes()).hexdigest() for path in received_root.glob("*.json")
)
summary = {
    "schema_version": 1,
    "implementation": "dtn7-rs v0.21.0",
    "protocol": "Bundle Protocol Version 7 (RFC 9171)",
    "topology": "site-a -> delayed relay/ferry -> site-b",
    "processes": 3,
    "partition_seconds_before_relay_start": 3,
    "message_classes": [
        "request", "quorum_request", "quorum_share", "right_transfer", "receipt"
    ],
    "bundles_sent": len(sent_hashes),
    "bundles_delivered": delivered,
    "sent_payload_sha256": sent_hashes,
    "received_payload_sha256": received_hashes,
    "all_payloads_exact": delivered == len(sent_hashes) and sent_hashes == received_hashes,
    "wall_clock_latency_seconds": (receive_ns - send_ns) / 1_000_000_000,
    "host": platform.platform(),
    "boundary": "single-host WSL process testbed; no Android/Raspberry Pi hardware",
}
(output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
checksums = []
for path in sorted(output.rglob("*")):
    if path.is_file() and path.name != "CHECKSUMS.sha256":
        checksums.append(
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}  "
            f"{path.relative_to(output).as_posix()}\n"
        )
(output / "CHECKSUMS.sha256").write_text("".join(checksums), encoding="ascii")
if not summary["all_payloads_exact"]:
    raise SystemExit("BPv7 payload set was not delivered exactly")
PY

cat "${OUTPUT_DIR}/summary.json"
