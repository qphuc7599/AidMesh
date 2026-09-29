"""Local Groth16 verifier helpers for the AidMesh P1 circuit profile.

The verifier in this module is a reproducibility harness around pinned snarkjs
artifacts.  It is intentionally explicit about file paths so experiment outputs
can report exactly which circuit, zkey and public signal vector were used.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

PUBLIC_INPUT_ORDER = (
    "campaignId",
    "epoch",
    "riskCell",
    "category",
    "value",
    "credentialRoot",
    "entitlementRoot",
    "nullifier",
    "fallbackMode",
    "fallbackWindow",
    "fallbackTag",
    "chainId",
    "gatewayAddress",
    "settlementAddress",
    "headerHash",
    "policyHash",
    "committeeHash",
)


@dataclass(frozen=True)
class Groth16Profile:
    circuit_id: str
    public_input_order: tuple[str, ...]
    r1cs_path: str
    wasm_path: str
    zkey_path: str
    verification_key_path: str
    constraint_count: int | None = None
    public_inputs: int | None = None
    private_inputs: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Groth16VerificationResult:
    ok: bool
    command: tuple[str, ...]
    stdout: str
    stderr: str
    returncode: int


def default_profile(root: Path) -> Groth16Profile:
    build = root / "zk" / "build"
    return Groth16Profile(
        circuit_id="aidmesh-voucher-p1-groth16-bn128-domain-v2-tagged",
        public_input_order=PUBLIC_INPUT_ORDER,
        r1cs_path=str(build / "aidmesh_voucher.r1cs"),
        wasm_path=str(build / "aidmesh_voucher_js" / "aidmesh_voucher.wasm"),
        zkey_path=str(build / "aidmesh_dev_final.zkey"),
        verification_key_path=str(build / "verification_key.json"),
        constraint_count=31011,
        public_inputs=len(PUBLIC_INPUT_ORDER),
        private_inputs=85,
    )


def snarkjs_command() -> str:
    executable = "npx.cmd" if shutil.which("npx.cmd") else "npx"
    if not shutil.which(executable):
        raise FileNotFoundError("npx/snarkjs toolchain is not available on PATH")
    return executable


def load_public_signals(path: Path) -> list[int]:
    values = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(values, list):
        raise ValueError("public signal file must contain a JSON list")
    return [int(value) for value in values]


def write_public_signals(path: Path, values: list[int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([str(value) for value in values], indent=2) + "\n", encoding="utf-8")


def verify_with_snarkjs(
    *,
    root: Path,
    verification_key: Path,
    public_signals: Path,
    proof: Path,
    check: bool = False,
) -> Groth16VerificationResult:
    command = (
        snarkjs_command(),
        "snarkjs",
        "groth16",
        "verify",
        str(verification_key),
        str(public_signals),
        str(proof),
    )
    completed = subprocess.run(command, cwd=root, capture_output=True, text=True, check=False)
    ok = completed.returncode == 0 and "OK" in completed.stdout
    if check and not ok:
        raise AssertionError(completed.stdout + completed.stderr)
    return Groth16VerificationResult(
        ok=ok,
        command=command,
        stdout=completed.stdout,
        stderr=completed.stderr,
        returncode=completed.returncode,
    )


def parse_solidity_calldata(path: Path) -> tuple[list[int], list[list[int]], list[int], list[int]]:
    """Parse snarkjs soliditycalldata output into Web3-friendly integer arrays."""

    raw = path.read_text(encoding="utf-8-sig").strip()
    values = json.loads(f"[{raw}]")
    if len(values) != 4:
        raise ValueError("expected four Groth16 verifier arguments")

    def convert(value: Any) -> Any:
        if isinstance(value, str):
            return int(value, 16) if value.startswith("0x") else int(value)
        if isinstance(value, list):
            return [convert(item) for item in value]
        raise TypeError(f"unsupported calldata value {type(value).__name__}")

    a, b, c, public = convert(values)
    return a, b, c, public
