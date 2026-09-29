"""Pinned Solidity compiler resolution for reproducible EVM experiments."""

from __future__ import annotations

import os
import shutil
from pathlib import Path


PINNED_SOLC_VERSION = "0.8.36"
PINNED_SOLC_SHA256 = "38cf5df656da822cf88b854cf736475a196c679f4f5f3f15ddd8c85ce5dbb36a"


def solc_command(root: Path | None = None) -> str:
    """Resolve the audited compiler, with an explicit environment override."""

    override = os.environ.get("AIDMESH_SOLC") or os.environ.get("SOLC_BIN")
    if override:
        path = Path(override).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"configured Solidity compiler does not exist: {path}")
        return str(path)
    project = root or Path(__file__).resolve().parents[2]
    pinned = project / "tools" / "solc" / f"solc-windows-v{PINNED_SOLC_VERSION}.exe"
    if pinned.is_file():
        return str(pinned)
    fallback = shutil.which("solc")
    if fallback:
        return fallback
    raise FileNotFoundError(
        "Solidity compiler unavailable; restore tools/solc or set AIDMESH_SOLC"
    )
