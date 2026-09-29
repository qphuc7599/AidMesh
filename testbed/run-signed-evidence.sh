#!/usr/bin/env bash
set -euo pipefail
BIN="$(cd "$1" && pwd)"
PAYLOAD="$(realpath "$2")"
OUT="$3"
[[ ! -e "$OUT" ]] || { echo 'fresh output required' >&2; exit 2; }
mkdir -p "$OUT"
OUT="$(realpath "$OUT")"
mkdir -p "$OUT/source" "$OUT/relay" "$OUT/gateway"
PIDS=()
cleanup() { for pid in "${PIDS[@]:-}"; do kill "$pid" 2>/dev/null || true; done; wait || true; }
trap cleanup EXIT INT TERM
start() {
  local name="$1" web="$2" mtcp="$3"; shift 3
  "$BIN/dtnd" --nodeid "dtn://${name}/" --workdir "$OUT/$name" --web-port "$web" \
    --db sled --routing epidemic --disable_nd --cla "mtcp:port=${mtcp}:bind=127.0.0.1" \
    "$@" >"$OUT/$name.log" 2>&1 &
  PIDS+=("$!")
}
start source 3301 17361 --endpoint outgoing --static-peer mtcp://127.0.0.1:17362/relay
start gateway 3303 17363 --endpoint incoming --static-peer mtcp://127.0.0.1:17362/relay
sleep 2
"$BIN/dtnsend" --port 3301 --sender dtn://source/outgoing --receiver dtn://gateway/incoming \
  --lifetime 120 "$PAYLOAD" >"$OUT/send.log" 2>&1
sleep 3
start relay 3302 17362 --static-peer mtcp://127.0.0.1:17361/source \
  --static-peer mtcp://127.0.0.1:17363/gateway
for attempt in $(seq 1 40); do
  if "$BIN/dtnrecv" --port 3303 --endpoint incoming --outfile "$OUT/received.json" \
       >"$OUT/receive.log" 2>&1 && [[ -s "$OUT/received.json" ]]; then
    cmp "$PAYLOAD" "$OUT/received.json"
    sha256sum "$PAYLOAD" "$OUT/received.json" >"$OUT/payload_hashes.txt"
    echo 'Signed evidence payload delivered unchanged over three BPv7 processes.'
    exit 0
  fi
  sleep 1
done
echo 'delivery timeout' >&2
exit 3
