# AidMesh BPv7 partition testbed

This harness runs three independent `dtn7-rs` processes (site A, delayed
relay/ferry, site B) and sends an authorization bundle while the relay is
offline. The edge nodes use persistent stores. Starting the relay after the
bundle is enqueued creates a reproducible disconnected interval; the test passes
only if the received payload is byte-exact.

The bootstrap script pins `dtn7-rs` v0.21.0 and verifies the upstream SHA-512.
On Windows with WSL2:

```powershell
$bin = & scripts\bootstrap-dtn7rs-testbed.ps1
wsl bash testbed/run-dtn7rs-partition.sh `
  "$(wsl wslpath -a $bin)" `
  /mnt/d/DoAnChuyenNganh/AirProof/results/bow_20260822/testbed/dtn7rs
```

The local result is process-level BPv7 evidence, not a physical-device result.
For Raspberry Pi/Android-class replication, place the same three commands on
separate hosts, replace `127.0.0.1` static peers with their LAN addresses, and
record device model, OS, power mode, interface, clock synchronization and packet
impairment configuration in the result manifest.
