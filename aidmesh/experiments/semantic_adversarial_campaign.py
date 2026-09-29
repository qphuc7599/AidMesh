"""Measured semantic-authorization attack, atomicity, and negative-control campaign."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
from eth_account import Account
from eth_account.messages import encode_defunct
from web3 import Web3
from web3.providers.eth_tester import EthereumTesterProvider

from aidmesh.experiments.solidity import solc_command

EVM_TX_GAS = 12_000_000
ATTACK_CASES = (
    "valid_quorum",
    "insufficient_quorum",
    "duplicate_witness",
    "foreign_witness",
    "stale_quorum_epoch",
    "forged_site_receipt",
    "wrong_chain_replay",
    "wrong_gateway_replay",
    "wrong_settlement_replay",
    "committee_rebind",
    "valid_right_transfer",
    "forged_right_owner",
    "forked_right_chain",
    "reused_right_nonce",
    "stale_transition_epoch",
    "insufficient_transition_quorum",
    "wrong_final_right_owner",
    "right_single_use",
    "risk_cap_overissue",
    "first_valid_wins_reordering",
    "reentrant_settlement_callback",
    "settlement_revert_atomicity",
)

NEGATIVE_CONTROL_CASES = {
    "insufficient_quorum",
    "duplicate_witness",
    "foreign_witness",
    "stale_quorum_epoch",
    "forged_site_receipt",
    "forged_right_owner",
    "forked_right_chain",
    "reused_right_nonce",
    "stale_transition_epoch",
    "insufficient_transition_quorum",
    "wrong_final_right_owner",
}


def _field(label: str) -> int:
    return int.from_bytes(Web3.keccak(text=label), "big")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def compile_semantic_harness(root: Path) -> dict[str, tuple[list[dict[str, Any]], str]]:
    sources = (
        root / "contracts" / "src" / "AidMeshSemanticAuthorizationGateway.sol",
        root / "contracts" / "src" / "AidMeshSettlement.sol",
        root / "contracts" / "test" / "AlwaysTrueGroth16Verifier.sol",
        root / "contracts" / "test" / "SemanticAdversarialHarness.sol",
    )
    completed = subprocess.run(
        [
            solc_command(root),
            "--evm-version",
            "prague",
            "--via-ir",
            "--optimize",
            "--combined-json",
            "abi,bin",
            *(str(path) for path in sources),
        ],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    artifacts = json.loads(completed.stdout)["contracts"]
    result = {}
    for identifier, artifact in artifacts.items():
        if not artifact["bin"]:
            continue
        name = identifier.rsplit(":", maxsplit=1)[-1]
        abi = artifact["abi"] if isinstance(artifact["abi"], list) else json.loads(
            artifact["abi"]
        )
        result[name] = (abi, artifact["bin"])
    return result


def _deploy(
    web3: Web3,
    artifact: tuple[list[dict[str, Any]], str],
    sender: str,
    *args: Any,
) -> Any:
    abi, bytecode = artifact
    tx_hash = web3.eth.contract(abi=abi, bytecode=bytecode).constructor(*args).transact(
        {"from": sender, "gas": EVM_TX_GAS}
    )
    receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
    if receipt.status != 1:
        raise RuntimeError("contract deployment failed")
    return web3.eth.contract(address=receipt.contractAddress, abi=abi)


def _send(web3: Web3, function: Any, sender: str) -> dict[str, Any]:
    started = time.perf_counter_ns()
    try:
        tx_hash = function.transact({"from": sender, "gas": EVM_TX_GAS})
        receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
        status = int(receipt.status)
        gas_used = int(receipt.gasUsed)
        block_number = int(receipt.blockNumber)
    except Exception as exc:  # pragma: no cover - provider-specific preflight failure
        status = 0
        gas_used = None
        block_number = None
        error = type(exc).__name__
    else:
        error = None
    return {
        "status": status,
        "gas_used": gas_used,
        "block_number": block_number,
        "latency_ms": (time.perf_counter_ns() - started) / 1_000_000,
        "error": error,
    }


@dataclass
class _System:
    web3: Web3
    owner: str
    witnesses: tuple[str, ...]
    site_a: str
    site_b: str
    attacker: str
    keys: dict[str, Any]
    verifier: Any
    gateway: Any
    settlement: Any
    inputs: list[int]
    domain: bytes
    epoch: int
    settlement_kind: str

    @property
    def proof(self) -> tuple[list[int], list[list[int]], list[int]]:
        return [0, 0], [[0, 0], [0, 0]], [0, 0]

    def sign(self, address: str, digest: bytes) -> bytes:
        return bytes(
            Account.sign_message(
                encode_defunct(primitive=digest),
                self.keys[address],
            ).signature
        )


def _deploy_secure(
    artifacts: dict[str, tuple[list[dict[str, Any]], str]],
    *,
    label: str,
    settlement_kind: str = "normal",
) -> _System:
    web3 = Web3(EthereumTesterProvider())
    accounts = web3.eth.accounts
    backend_keys = web3.provider.ethereum_tester.backend.account_keys
    keys = {address: backend_keys[index] for index, address in enumerate(accounts)}
    owner = accounts[0]
    witnesses = tuple(accounts[1:4])
    site_a, site_b, attacker = accounts[4:7]
    verifier = _deploy(web3, artifacts["AlwaysTrueGroth16Verifier"], owner)
    gateway = _deploy(
        web3,
        artifacts["AidMeshSemanticAuthorizationGateway"],
        owner,
        verifier.address,
    )
    if settlement_kind == "normal":
        settlement = _deploy(web3, artifacts["AidMeshSettlement"], owner, gateway.address)
    elif settlement_kind == "reentrant":
        settlement = _deploy(
            web3,
            artifacts["ReentrantSemanticSettlementProbe"],
            owner,
            gateway.address,
        )
    elif settlement_kind == "reverting":
        settlement = _deploy(
            web3,
            artifacts["RevertingSemanticSettlementProbe"],
            owner,
        )
    else:
        raise ValueError(f"unknown settlement kind: {settlement_kind}")
    gateway.functions.setSettlement(settlement.address).transact({"from": owner})
    inputs = [0] * 17
    inputs[0] = _field(f"root:{label}")
    inputs[1] = _field("schema")
    inputs[2] = _field("issuer")
    inputs[4] = 1
    inputs[5] = _field("campaign")
    inputs[6] = _field("expiry")
    inputs[7] = _field(f"nullifier:{label}")
    inputs[11] = web3.eth.chain_id
    inputs[12] = int(gateway.address, 16)
    inputs[13] = int(settlement.address, 16)
    inputs[14] = _field("header")
    inputs[15] = _field("policy")
    epoch = 7
    inputs[16] = gateway.functions.semanticCommitteeHash(
        epoch, 2, 3, list(witnesses)
    ).call()
    domain = gateway.functions.proofDomain(inputs).call()
    if settlement_kind == "normal":
        settlement.functions.registerCampaign(
            domain,
            inputs[14],
            inputs[15],
            inputs[16],
            [site_a, site_b],
        ).transact({"from": owner})
        settlement.functions.fundCampaign(domain, 20).transact({"from": owner})
    gateway.functions.registerAuthorizationDomain(
        domain,
        epoch,
        2,
        inputs[16],
        3,
        list(witnesses),
    ).transact({"from": owner})
    return _System(
        web3=web3,
        owner=owner,
        witnesses=witnesses,
        site_a=site_a,
        site_b=site_b,
        attacker=attacker,
        keys=keys,
        verifier=verifier,
        gateway=gateway,
        settlement=settlement,
        inputs=inputs,
        domain=domain,
        epoch=epoch,
        settlement_kind=settlement_kind,
    )


def _quorum_evidence(
    system: _System,
    *,
    label: str,
    site: str | None = None,
    inputs: list[int] | None = None,
) -> tuple[tuple[Any, ...], list[bytes]]:
    statement_inputs = inputs or system.inputs
    receipt_site = site or system.site_a
    receipt_id = Web3.keccak(text=f"receipt:{label}")
    release_intent_id = Web3.keccak(text=f"release-intent:{label}")
    receipt_skeleton_hash = system.gateway.functions.receiptSkeleton(
        statement_inputs,
        release_intent_id,
        receipt_site,
        system.epoch,
    ).call()
    authorization_hash = system.gateway.functions.authorizationIntent(
        statement_inputs,
        release_intent_id,
        receipt_skeleton_hash,
        receipt_site,
        system.epoch,
    ).call()
    statement_hash = system.gateway.functions.proofStatementHash(statement_inputs).call()
    receipt_payload = system.gateway.functions.receiptPayload(
        system.gateway.functions.proofDomain(statement_inputs).call(),
        statement_hash,
        receipt_id,
        receipt_site,
        release_intent_id,
        receipt_skeleton_hash,
        authorization_hash,
    ).call()
    receipt = (
        receipt_id,
        receipt_site,
        release_intent_id,
        receipt_skeleton_hash,
        authorization_hash,
        system.sign(receipt_site, receipt_payload),
    )
    signatures = [
        system.sign(witness, authorization_hash) for witness in system.witnesses[:2]
    ]
    return receipt, signatures


def _transition(
    system: _System,
    *,
    right_id: bytes,
    owner_from: str,
    owner_to: str,
    epoch: int,
    nonce: int,
    signers: tuple[str, ...] | None = None,
) -> tuple[Any, ...]:
    statement = system.gateway.functions.rightTransitionStatement(
        system.domain,
        right_id,
        owner_from,
        owner_to,
        epoch,
        nonce,
    ).call()
    selected = signers or system.witnesses[:2]
    return (
        owner_from,
        owner_to,
        epoch,
        nonce,
        [system.sign(signer, statement) for signer in selected],
    )


def _right_receipt(
    system: _System,
    *,
    label: str,
    right_id: bytes,
    site: str,
    transitions: list[tuple[Any, ...]],
) -> tuple[Any, ...]:
    receipt_id = Web3.keccak(text=f"right-receipt:{label}")
    release_intent_id = Web3.keccak(text=f"right-release-intent:{label}")
    receipt_skeleton_hash = system.gateway.functions.receiptSkeleton(
        system.inputs,
        release_intent_id,
        site,
        system.epoch,
    ).call()
    authorization_hash = system.gateway.functions.rightAuthorizationHash(
        system.inputs,
        release_intent_id,
        receipt_skeleton_hash,
        site,
        right_id,
        system.epoch,
        transitions,
    ).call()
    statement_hash = system.gateway.functions.proofStatementHash(system.inputs).call()
    receipt_payload = system.gateway.functions.receiptPayload(
        system.domain,
        statement_hash,
        receipt_id,
        site,
        release_intent_id,
        receipt_skeleton_hash,
        authorization_hash,
    ).call()
    return (
        receipt_id,
        site,
        release_intent_id,
        receipt_skeleton_hash,
        authorization_hash,
        system.sign(site, receipt_payload),
    )


def _run_quorum_case(system: _System, case: str, label: str) -> dict[str, Any]:
    a, b, c = system.proof
    inputs = list(system.inputs)
    receipt, signatures = _quorum_evidence(system, label=label)
    epoch = system.epoch
    if case == "insufficient_quorum":
        signatures = signatures[:1]
    elif case == "duplicate_witness":
        signatures = [signatures[0], signatures[0]]
    elif case == "foreign_witness":
        signatures = [signatures[0], system.sign(system.attacker, receipt[4])]
    elif case == "stale_quorum_epoch":
        epoch += 1
    elif case == "forged_site_receipt":
        receipt = (*receipt[:5], system.sign(system.attacker, Web3.keccak(text="forged")))
    elif case == "wrong_chain_replay":
        inputs[11] += 1
    elif case == "wrong_gateway_replay":
        inputs[12] = int(system.attacker, 16)
    elif case == "wrong_settlement_replay":
        inputs[13] = int(system.attacker, 16)
    elif case == "committee_rebind":
        inputs[16] = _field("foreign-committee")
    elif case != "valid_quorum":
        raise ValueError(case)
    return _send(
        system.web3,
        system.gateway.functions.verifyQuorumAndSettle(
            a,
            b,
            c,
            inputs,
            receipt,
            epoch,
            signatures,
        ),
        system.attacker,
    )


def _run_right_case(system: _System, case: str, label: str) -> dict[str, Any]:
    right_id = Web3.keccak(text=f"right:{label}")
    system.gateway.functions.issueRight(
        system.domain,
        right_id,
        system.site_a,
        1,
    ).transact({"from": system.owner})
    transition = _transition(
        system,
        right_id=right_id,
        owner_from=system.site_a,
        owner_to=system.site_b,
        epoch=system.epoch,
        nonce=1,
    )
    transitions = [transition]
    site = system.site_b
    if case == "forged_right_owner":
        transitions = [
            _transition(
                system,
                right_id=right_id,
                owner_from=system.site_b,
                owner_to=system.site_a,
                epoch=system.epoch,
                nonce=1,
            )
        ]
        site = system.site_a
    elif case == "forked_right_chain":
        transitions.append(
            _transition(
                system,
                right_id=right_id,
                owner_from=system.site_a,
                owner_to=system.site_a,
                epoch=system.epoch,
                nonce=2,
            )
        )
        site = system.site_a
    elif case == "reused_right_nonce":
        transitions.append(
            _transition(
                system,
                right_id=right_id,
                owner_from=system.site_b,
                owner_to=system.site_a,
                epoch=system.epoch,
                nonce=1,
            )
        )
        site = system.site_a
    elif case == "stale_transition_epoch":
        transitions = [
            _transition(
                system,
                right_id=right_id,
                owner_from=system.site_a,
                owner_to=system.site_b,
                epoch=system.epoch + 1,
                nonce=1,
            )
        ]
    elif case == "insufficient_transition_quorum":
        transitions = [
            _transition(
                system,
                right_id=right_id,
                owner_from=system.site_a,
                owner_to=system.site_b,
                epoch=system.epoch,
                nonce=1,
                signers=system.witnesses[:1],
            )
        ]
    elif case == "wrong_final_right_owner":
        transitions = []
        site = system.site_b
    elif case not in {"valid_right_transfer", "right_single_use", "settlement_revert_atomicity"}:
        raise ValueError(case)
    receipt = _right_receipt(
        system,
        label=label,
        right_id=right_id,
        site=site,
        transitions=transitions,
    )
    a, b, c = system.proof
    function = system.gateway.functions.verifyRightAndSettle(
        a,
        b,
        c,
        system.inputs,
        receipt,
        right_id,
        system.epoch,
        transitions,
    )
    first = _send(system.web3, function, system.attacker)
    first["status_sequence"] = str(first["status"])
    if case == "right_single_use":
        second = _send(system.web3, function, system.attacker)
        first["status_sequence"] += f",{second['status']}"
        first["security_holds"] = first["status"] == 1 and second["status"] == 0
        first["secondary_gas_used"] = second["gas_used"]
    elif case == "settlement_revert_atomicity":
        state = system.gateway.functions.rights(right_id).call()
        first["security_holds"] = (
            first["status"] == 0
            and state[1] == system.site_a
            and state[4] == 0
            and state[5] is False
        )
    return first


def _run_cap_case(system: _System, label: str) -> dict[str, Any]:
    system.gateway.functions.issueRight(
        system.domain,
        Web3.keccak(text=f"full-cap:{label}"),
        system.site_a,
        3,
    ).transact({"from": system.owner})
    result = _send(
        system.web3,
        system.gateway.functions.issueRight(
            system.domain,
            Web3.keccak(text=f"over-cap:{label}"),
            system.site_a,
            1,
        ),
        system.owner,
    )
    domain = system.gateway.functions.authorizationDomains(system.domain).call()
    result["security_holds"] = result["status"] == 0 and domain[5] == 3
    return result


def _run_reordering_case(system: _System, label: str) -> dict[str, Any]:
    a, b, c = system.proof
    receipt_a, signatures_a = _quorum_evidence(system, label=f"{label}:a", site=system.site_a)
    receipt_b, signatures_b = _quorum_evidence(system, label=f"{label}:b", site=system.site_b)
    first = _send(
        system.web3,
        system.gateway.functions.verifyQuorumAndSettle(
            a, b, c, system.inputs, receipt_b, system.epoch, signatures_b
        ),
        system.attacker,
    )
    second = _send(
        system.web3,
        system.gateway.functions.verifyQuorumAndSettle(
            a, b, c, system.inputs, receipt_a, system.epoch, signatures_a
        ),
        system.owner,
    )
    nullifier = system.inputs[7].to_bytes(32, "big")
    winner = system.settlement.functions.winningReceipt(system.domain, nullifier).call()
    credit_a = system.settlement.functions.credits(system.domain, system.site_a).call()
    credit_b = system.settlement.functions.credits(system.domain, system.site_b).call()
    first["status_sequence"] = f"{first['status']},{second['status']}"
    first["secondary_gas_used"] = second["gas_used"]
    first["security_holds"] = (
        first["status"] == 1
        and second["status"] == 1
        and winner == receipt_b[0]
        and credit_a + credit_b == 1
    )
    return first


def _run_reentrant_case(system: _System, label: str) -> dict[str, Any]:
    receipt, signatures = _quorum_evidence(system, label=label)
    a, b, c = system.proof
    function = system.gateway.functions.verifyQuorumAndSettle(
        a, b, c, system.inputs, receipt, system.epoch, signatures
    )
    system.settlement.functions.setReentryPayload(
        bytes.fromhex(function._encode_transaction_data()[2:])
    ).transact({"from": system.owner})
    result = _send(system.web3, function, system.attacker)
    result["security_holds"] = (
        result["status"] == 1
        and system.settlement.functions.attempted().call()
        and not system.settlement.functions.reentrySucceeded().call()
    )
    return result


def _run_negative_control(
    artifacts: dict[str, tuple[list[dict[str, Any]], str]],
    *,
    label: str,
) -> dict[str, Any]:
    web3 = Web3(EthereumTesterProvider())
    accounts = web3.eth.accounts
    owner, attacker, site = accounts[0], accounts[1], accounts[2]
    verifier = _deploy(web3, artifacts["AlwaysTrueGroth16Verifier"], owner)
    gateway = _deploy(web3, artifacts["VulnerableDigestOnlyGateway"], owner, verifier.address)
    settlement = _deploy(web3, artifacts["AidMeshSettlement"], owner, gateway.address)
    gateway.functions.setSettlement(settlement.address).transact({"from": owner})
    inputs = [0] * 17
    inputs[0] = _field(f"negative-root:{label}")
    inputs[1] = _field("schema")
    inputs[2] = _field("issuer")
    inputs[4] = 1
    inputs[5] = _field("campaign")
    inputs[6] = _field("expiry")
    inputs[7] = _field(f"negative-nullifier:{label}")
    inputs[11] = web3.eth.chain_id
    inputs[12] = int(gateway.address, 16)
    inputs[13] = int(settlement.address, 16)
    inputs[14] = _field("header")
    inputs[15] = _field("policy")
    inputs[16] = _field("attacker-chosen-committee")
    domain = gateway.functions.proofDomain(inputs).call()
    settlement.functions.registerCampaign(
        domain,
        inputs[14],
        inputs[15],
        inputs[16],
        [site],
    ).transact({"from": owner})
    settlement.functions.fundCampaign(domain, 2).transact({"from": owner})
    a, b, c = [0, 0], [[0, 0], [0, 0]], [0, 0]
    return _send(
        web3,
        gateway.functions.settleWithoutSemanticAuthorization(
            a,
            b,
            c,
            inputs,
            Web3.keccak(text=f"negative-receipt:{label}"),
            site,
            Web3.keccak(text="attacker-chosen-authorization"),
        ),
        attacker,
    )


def run_semantic_adversarial_campaign(
    root: Path,
    *,
    trials: int = 10,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    if trials <= 0:
        raise ValueError("trials must be positive")
    artifacts = compile_semantic_harness(root)
    rows = []
    for trial in range(trials):
        for case in ATTACK_CASES:
            label = f"{case}:{trial}"
            settlement_kind = "normal"
            if case == "reentrant_settlement_callback":
                settlement_kind = "reentrant"
            elif case == "settlement_revert_atomicity":
                settlement_kind = "reverting"
            system = _deploy_secure(
                artifacts,
                label=label,
                settlement_kind=settlement_kind,
            )
            if case in {
                "valid_quorum",
                "insufficient_quorum",
                "duplicate_witness",
                "foreign_witness",
                "stale_quorum_epoch",
                "forged_site_receipt",
                "wrong_chain_replay",
                "wrong_gateway_replay",
                "wrong_settlement_replay",
                "committee_rebind",
            }:
                result = _run_quorum_case(system, case, label)
            elif case in {
                "valid_right_transfer",
                "forged_right_owner",
                "forked_right_chain",
                "reused_right_nonce",
                "stale_transition_epoch",
                "insufficient_transition_quorum",
                "wrong_final_right_owner",
                "right_single_use",
                "settlement_revert_atomicity",
            }:
                result = _run_right_case(system, case, label)
            elif case == "risk_cap_overissue":
                result = _run_cap_case(system, label)
            elif case == "first_valid_wins_reordering":
                result = _run_reordering_case(system, label)
            elif case == "reentrant_settlement_callback":
                result = _run_reentrant_case(system, label)
            else:  # pragma: no cover
                raise AssertionError(case)
            expected_status = int(case in {"valid_quorum", "valid_right_transfer"})
            if case in {
                "right_single_use",
                "risk_cap_overissue",
                "first_valid_wins_reordering",
                "reentrant_settlement_callback",
                "settlement_revert_atomicity",
            }:
                security_holds = bool(result.get("security_holds", False))
            else:
                security_holds = result["status"] == expected_status
            negative = (
                _run_negative_control(artifacts, label=label)
                if case in NEGATIVE_CONTROL_CASES
                else None
            )
            rows.append(
                {
                    "trial": trial,
                    "case": case,
                    "secure_status": result["status"],
                    "expected_secure_status": expected_status,
                    "security_holds": security_holds,
                    "secure_gas_used": result["gas_used"],
                    "secure_latency_ms": result["latency_ms"],
                    "status_sequence": result.get("status_sequence"),
                    "secondary_gas_used": result.get("secondary_gas_used"),
                    "negative_control_applicable": negative is not None,
                    "negative_control_status": negative["status"] if negative else None,
                    "negative_control_gas_used": negative["gas_used"] if negative else None,
                    "negative_control_accepts_attack": (
                        negative["status"] == 1 if negative else None
                    ),
                    "proof_backend": "always-true verifier; semantic isolation campaign",
                    "evm_backend": "py-evm via eth-tester",
                }
            )
    frame = pd.DataFrame(rows)
    manifest = {
        "schema_version": 1,
        "campaign": "semantic authorization adversarial and atomicity campaign",
        "trials_per_case": trials,
        "cases": list(ATTACK_CASES),
        "rows": len(frame),
        "secure_checks_passed": int(frame["security_holds"].sum()),
        "negative_controls_applicable": int(frame["negative_control_applicable"].sum()),
        "negative_controls_accepting": int(
        frame["negative_control_accepts_attack"].eq(True).sum()
        ),
        "proof_scope": (
            "AlwaysTrueGroth16Verifier deliberately isolates authorization semantics; "
            "this campaign is not proof-soundness evidence"
        ),
        "contracts": {
            path.name: _sha256(path)
            for path in (
                root / "contracts" / "src" / "AidMeshSemanticAuthorizationGateway.sol",
                root / "contracts" / "src" / "AidMeshSettlement.sol",
                root / "contracts" / "test" / "SemanticAdversarialHarness.sol",
            )
        },
    }
    return frame, manifest


def write_semantic_adversarial_campaign(
    root: Path,
    output: Path,
    *,
    trials: int = 10,
) -> dict[str, Path]:
    output.mkdir(parents=True, exist_ok=True)
    frame, manifest = run_semantic_adversarial_campaign(root, trials=trials)
    summary = (
        frame.groupby("case", as_index=False)
        .agg(
            trials=("trial", "size"),
            secure_acceptance_rate=("secure_status", "mean"),
            security_hold_rate=("security_holds", "mean"),
            mean_secure_gas=("secure_gas_used", "mean"),
            p95_secure_latency_ms=("secure_latency_ms", lambda values: values.quantile(0.95)),
            negative_control_acceptance_rate=("negative_control_status", "mean"),
        )
    )
    paths = {
        "runs": output / "semantic_adversarial_runs.csv",
        "summary": output / "semantic_adversarial_summary.csv",
        "manifest": output / "manifest.json",
    }
    frame.to_csv(paths["runs"], index=False, lineterminator="\n")
    summary.to_csv(paths["summary"], index=False, lineterminator="\n")
    paths["manifest"].write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    checksums = output / "CHECKSUMS.sha256"
    checksums.write_text(
        "".join(f"{_sha256(path)}  {path.name}\n" for path in paths.values()),
        encoding="ascii",
    )
    paths["checksums"] = checksums
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("results/tdsc_full_20260822/semantic_adversarial"),
    )
    parser.add_argument("--trials", type=int, default=10)
    args = parser.parse_args()
    paths = write_semantic_adversarial_campaign(
        args.root.resolve(),
        args.output,
        trials=args.trials,
    )
    for name, path in paths.items():
        print(f"{name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
