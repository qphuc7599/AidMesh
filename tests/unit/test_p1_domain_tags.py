import hashlib
import json
from pathlib import Path

import yaml
import pytest

from aidmesh.crypto.groth16 import PUBLIC_INPUT_ORDER, default_profile

ROOT = Path(__file__).resolve().parents[2]
CANONICAL_PROFILE = "aidmesh-voucher-p1-groth16-bn128-domain-v2-tagged"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def test_domain_tag_derivation_and_circuit_binding() -> None:
    registry = json.loads((ROOT / "zk" / "domain_tags.json").read_text(encoding="utf-8"))
    modulus = int(registry["field_modulus"])
    circuit = (ROOT / "zk" / "circuits" / "aidmesh_voucher.circom").read_text(encoding="utf-8")
    assert registry["profile"] == "aidmesh-p1-v2-tagged"
    assert len(registry["tags"]) == 8
    values = set()
    for entry in registry["tags"].values():
        derived = (
            int.from_bytes(hashlib.sha256(entry["label"].encode("utf-8")).digest(), "big") % modulus
        )
        assert str(derived) == entry["value"]
        assert entry["value"] in circuit
        values.add(entry["value"])
    assert len(values) == 8


def test_only_tagged_profile_is_canonical() -> None:
    profile = default_profile(ROOT)
    assert profile.circuit_id == CANONICAL_PROFILE
    assert profile.public_inputs == 17
    assert profile.private_inputs == 85
    assert profile.constraint_count == 31_011
    assert not (ROOT / "contracts" / "src" / "AidMeshGroth16Verifier.domain-v2.sol").exists()
    assert (
        ROOT / "contracts" / "src" / "archive" / "AidMeshGroth16Verifier.p1-v2-pretag-20260723.sol"
    ).is_file()


def test_public_signal_order_is_identical_in_circuit_and_manuscript() -> None:
    circuit = (ROOT / "zk" / "circuits" / "aidmesh_voucher.circom").read_text(encoding="utf-8")
    public_block = circuit.split("component main {public [", maxsplit=1)[1].split("]}", maxsplit=1)[
        0
    ]
    circuit_order = tuple(field.strip() for field in public_block.replace("\n", " ").split(","))
    assert circuit_order == PUBLIC_INPUT_ORDER

    manuscript_path = ROOT / "source" / "paper.tex"
    if not manuscript_path.is_file():
        pytest.skip("the manuscript is intentionally excluded from this source-only repository")
    manuscript = manuscript_path.read_text(encoding="utf-8")
    for index, signal in enumerate(PUBLIC_INPUT_ORDER):
        assert f"{index}&\\texttt{{{signal}}}" in manuscript
    assert "10&\\texttt{expiry}" not in manuscript


def test_active_e2e_registries_use_only_the_canonical_profile() -> None:
    registries = sorted((ROOT / "configs" / "e2e").glob("*.yaml"))
    assert registries
    for path in registries:
        registry = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert registry["profile"] == CANONICAL_PROFILE, path


def test_audit_scope_hashes_match_the_canonical_objects() -> None:
    scope_path = ROOT / "docs" / "audit" / "SCOPE.md"
    if not scope_path.is_file():
        pytest.skip("the audit report and generated proving artifacts are distributed separately")
    scope = scope_path.read_text(encoding="utf-8")
    canonical_objects = (
        ROOT / "zk" / "build" / "aidmesh_voucher.r1cs",
        ROOT / "zk" / "build" / "aidmesh_p1_v2_tagged_0000.zkey",
        ROOT / "zk" / "circuits" / "aidmesh_voucher.circom",
        ROOT / "zk" / "domain_tags.json",
        ROOT / "contracts" / "src" / "AidMeshGroth16Verifier.sol",
    )
    for path in canonical_objects:
        assert _sha256(path) in scope, path
    assert CANONICAL_PROFILE in scope
    assert "Constraints/private signals: 31,011/85" in scope
