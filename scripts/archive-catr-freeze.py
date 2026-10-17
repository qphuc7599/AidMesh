"""Archive and verify every file named by a CATR cryptographic freeze."""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--freeze", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    freeze = json.loads(args.freeze.read_text(encoding="utf-8"))
    for record in freeze["files"]:
        path = workspace / record["path"]
        observed = _sha256(path)
        if observed != record["sha256"]:
            raise SystemExit(f"frozen input changed before archival: {record['path']}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(args.freeze, "freeze.json")
        for record in freeze["files"]:
            archive.write(workspace / record["path"], record["path"])
    checksum = args.output.with_suffix(args.output.suffix + ".sha256")
    checksum.write_text(
        f"{_sha256(args.output)}  {args.output.name}\n",
        encoding="ascii",
    )
    print(json.dumps({"archive": str(args.output), "sha256": _sha256(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
