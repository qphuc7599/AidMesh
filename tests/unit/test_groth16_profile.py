from __future__ import annotations

import pytest

from aidmesh.crypto.groth16 import PUBLIC_INPUT_ORDER, parse_solidity_calldata


def test_public_input_order_is_stable() -> None:
    assert PUBLIC_INPUT_ORDER == (
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


def test_parse_solidity_calldata_when_artifact_exists() -> None:
    path = pytest.importorskip("pathlib").Path("zk/build/bench_calldata.txt")
    if not path.exists():
        pytest.skip("Groth16 calldata artifact has not been generated")

    a, b, c, public = parse_solidity_calldata(path)

    assert len(a) == 2
    assert len(b) == 2
    assert len(c) == 2
    assert len(public) == len(PUBLIC_INPUT_ORDER)
    assert public[4] == 10
