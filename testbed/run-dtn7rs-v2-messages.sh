#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "usage: $0 <dtn7-rs-bin-dir> <new-output-dir>" >&2
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
[[ ! -e "$OUTPUT_DIR" ]] || { echo "output already exists: $OUTPUT_DIR" >&2; exit 4; }

mkdir -p "$OUTPUT_DIR"
OUTPUT_DIR="$(cd "$OUTPUT_DIR" && pwd)"
RUNTIME_DIR="$(mktemp -d /tmp/aidmesh-dtn7rs-v2-XXXXXX)"
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

start_node site-a 3201 17261 "$RUNTIME_DIR/site-a" \
  --endpoint outgoing \
  --static-peer mtcp://127.0.0.1:17262/relay
start_node site-b 3203 17263 "$RUNTIME_DIR/site-b" \
  --endpoint incoming \
  --static-peer mtcp://127.0.0.1:17262/relay
sleep 2

PAYLOAD_DIR="${OUTPUT_DIR}/payloads"
RECEIVED_DIR="${OUTPUT_DIR}/received"
mkdir -p "$PAYLOAD_DIR" "$RECEIVED_DIR"
python3 - "$PAYLOAD_DIR" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
domain = {
    "protocol": "AidMesh",
    "version": 2,
    "chain_id": 31337,
    "gateway": "0x1111111111111111111111111111111111111111",
    "settlement": "0x2222222222222222222222222222222222222222",
    "header_hash": "0x" + "33" * 32,
    "policy_hash": "0x" + "44" * 32,
    "committee_hash": "0x" + "55" * 32,
}
intent = {
    "type": "AuthorizationIntentV1",
    "domain": domain,
    "epoch": 7,
    "nullifier": "0x" + "66" * 32,
    "proof_hash": "0x" + "77" * 32,
    "site": "site-b",
    "value": 1,
    "release_intent_id": "release-v2-0001",
    "receipt_skeleton_hash": "0x" + "88" * 32,
}
canonical_intent = json.dumps(intent, sort_keys=True, separators=(",", ":")).encode()
intent_hash = "0x" + hashlib.sha256(canonical_intent).hexdigest()
messages = [
    {
        "class": "eligibility_request_v2",
        "domain": domain,
        "epoch": 7,
        "nullifier": intent["nullifier"],
        "proof_hash": intent["proof_hash"],
        "requested_site": "site-b",
        "value": 1,
    },
    {"class": "authorization_intent_v1", **intent, "intent_hash": intent_hash},
    {
        "class": "quorum_share_v1",
        "domain": domain,
        "epoch": 7,
        "nullifier": intent["nullifier"],
        "intent_hash": intent_hash,
        "witness": "witness-h1",
        "signature": "0x" + "99" * 65,
    },
    {
        "class": "terminal_certificate_v1",
        "domain": domain,
        "transfer_id": "transfer-v2-0001",
        "right_id": "right-v2-0001",
        "source": "site-a",
        "destination": "site-b",
        "decision": "accept",
        "certificate_hash": "0x" + "aa" * 32,
    },
    {
        "class": "receipt_v2",
        "domain": domain,
        "epoch": 7,
        "nullifier": intent["nullifier"],
        "authorization_intent_hash": intent_hash,
        "release_intent_id": intent["release_intent_id"],
        "receipt_skeleton_hash": intent["receipt_skeleton_hash"],
        "evidence_hash": "0x" + "bb" * 32,
        "site_signature": "0x" + "cc" * 65,
    },
]
for index, message in enumerate(messages):
    message_class = message["class"]
    (root / f"{index:02d}-{message_class}.json").write_text(
        json.dumps(message, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
PY

SEND_NS="$(date +%s%N)"
: >"${OUTPUT_DIR}/send.log"
for payload in "$PAYLOAD_DIR"/*.json; do
  "$DTNSEND" --port 3201 --sender dtn://site-a/outgoing \
    --receiver dtn://site-b/incoming --lifetime 120 "$payload" \
    >>"${OUTPUT_DIR}/send.log" 2>&1
done

sleep 3
start_node relay 3202 17262 "$RUNTIME_DIR/relay" \
  --static-peer mtcp://127.0.0.1:17261/site-a \
  --static-peer mtcp://127.0.0.1:17263/site-b

DELIVERED=0
for attempt in $(seq 1 40); do
  received="${RECEIVED_DIR}/$(printf '%02d' "$DELIVERED").json"
  if "$DTNRECV" --port 3203 --endpoint incoming --outfile "$received" \
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

for port in 3201 3202 3203; do
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
sent = sorted((hashlib.sha256(path.read_bytes()).hexdigest(), path.name)
              for path in sent_root.glob("*.json"))
received_hashes = sorted(hashlib.sha256(path.read_bytes()).hexdigest()
                         for path in received_root.glob("*.json"))
sent_hashes = sorted(item[0] for item in sent)
summary = {
    "schema_version": 2,
    "implementation": "dtn7-rs v0.21.0",
    "protocol": "Bundle Protocol Version 7 (RFC 9171)",
    "application_semantics": "AidMesh protocol-V2 message serialization",
    "topology": "site-a -> delayed relay/ferry -> site-b",
    "processes": 3,
    "partition_seconds_before_relay_start": 3,
    "message_classes": [name[3:-5] for _, name in sent],
    "bundles_sent": len(sent_hashes),
    "bundles_delivered": delivered,
    "sent_payload_sha256": sent_hashes,
    "received_payload_sha256": received_hashes,
    "all_payloads_exact": delivered == len(sent_hashes) and sent_hashes == received_hashes,
    "wall_clock_latency_seconds": (receive_ns - send_ns) / 1_000_000_000,
    "host": platform.platform(),
    "boundary": (
        "Single-host WSL three-process BPv7 serialization/delivery test; it does not "
        "validate Android/Raspberry Pi hardware, cryptographic correctness, or site "
        "state-machine execution."
    ),
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
    raise SystemExit("BPv7 protocol-V2 payload set was not delivered exactly")
PY

cat "${OUTPUT_DIR}/summary.json"
