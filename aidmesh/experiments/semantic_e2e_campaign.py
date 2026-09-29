"""Trace-stratified real-Groth16 to semantic-settlement E2E campaign.

Unlike the semantic adversarial harness, this campaign deploys the generated
Groth16 verifier.  Every semantic rejection therefore traverses the same
proof/context checks as a valid transaction before exercising quorum or
transferable-right authorization.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
from pathlib import Path
from typing import Any

import pandas as pd
from eth_account import Account
from eth_account.messages import encode_defunct
from web3 import Web3
from web3.providers.eth_tester import EthereumTesterProvider

from aidmesh.common.canonical import stable_hash
from aidmesh.crypto import evm_wire
from aidmesh.experiments.e2e_campaign import (
    BN254_SCALAR_FIELD,
    E2ERegistry,
    ProofArtifact,
    load_e2e_registry,
    prepare_proof_cache,
)
from aidmesh.experiments.semantic_adversarial_campaign import EVM_TX_GAS, _send
from aidmesh.experiments.solidity import solc_command


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _field(value: object, *, domain: str) -> int:
    return (int(stable_hash(value, domain=domain), 16) % (BN254_SCALAR_FIELD - 1)) + 1


def _compile(root: Path, *, verifier_source: Path | None = None) -> dict[str, tuple[list[dict[str, Any]], str]]:
    sources = (
        verifier_source or root / "contracts" / "src" / "AidMeshGroth16Verifier.sol",
        root / "contracts" / "src" / "AidMeshSemanticAuthorizationGateway.sol",
        root / "contracts" / "src" / "AidMeshSettlement.sol",
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
    compiled = json.loads(completed.stdout)["contracts"]
    result: dict[str, tuple[list[dict[str, Any]], str]] = {}
    for identifier, artifact in compiled.items():
        if not artifact["bin"]:
            continue
        name = identifier.rsplit(":", maxsplit=1)[-1]
        abi = artifact["abi"] if isinstance(artifact["abi"], list) else json.loads(artifact["abi"])
        result[name] = (abi, artifact["bin"])
    required = {
        "Groth16Verifier",
        "AidMeshSemanticAuthorizationGateway",
        "AidMeshSettlement",
    }
    if missing := required.difference(result):
        raise RuntimeError(f"semantic E2E compile missing contracts: {sorted(missing)}")
    return result


class _Harness:
    def __init__(self, root: Path, *, block_gas_limit: int | None = None,
                 verifier_source: Path | None = None, rpc_url: str | None = None,
                 deployment: dict[str, object] | None = None) -> None:
        if rpc_url is not None:
            self.web3 = Web3(Web3.HTTPProvider(rpc_url))
            if not self.web3.is_connected():
                raise RuntimeError(f"cannot connect to EVM RPC {rpc_url}")
        elif block_gas_limit is None:
            self.web3 = Web3(EthereumTesterProvider())
        else:
            from eth_tester import EthereumTester, PyEVMBackend

            self.web3 = Web3(
                EthereumTesterProvider(
                    EthereumTester(
                        backend=PyEVMBackend(genesis_parameters={"gas_limit": block_gas_limit})
                    )
                )
            )
        if rpc_url is None:
            self.accounts = tuple(self.web3.eth.accounts)
            backend_keys = self.web3.provider.ethereum_tester.backend.account_keys
            self.keys = {address: backend_keys[index] for index, address in enumerate(self.accounts)}
            self.owner = self.accounts[0]
        else:
            # Deterministic, openly documented development-only signers. The dev
            # account unlocked by geth owns/configures contracts; it never signs
            # AidMesh protocol evidence.
            generated = [Account.from_key(Web3.keccak(text=f"aidmesh/geth-dev/{i}"))
                         for i in range(1, 10)]
            self.owner = self.web3.eth.accounts[0]
            self.accounts = (self.owner, *(account.address for account in generated))
            self.keys = {account.address: account.key for account in generated}
        self.witnesses = self.accounts[1:4]
        self.sites = self.accounts[4:8]
        self.relayer = self.accounts[8] if rpc_url is None else self.owner
        artifacts = _compile(root, verifier_source=verifier_source)
        if deployment is None:
            self.verifier, verifier_gas = self._deploy(artifacts["Groth16Verifier"])
            self.gateway, gateway_gas = self._deploy(
                artifacts["AidMeshSemanticAuthorizationGateway"], self.verifier.address
            )
            self.settlement, settlement_gas = self._deploy(
                artifacts["AidMeshSettlement"], self.gateway.address
            )
            link = self.gateway.functions.setSettlement(self.settlement.address).transact(
                {"from": self.owner, "gas": EVM_TX_GAS}
            )
            link_receipt = self.web3.eth.wait_for_transaction_receipt(link)
            if link_receipt.status != 1:
                raise RuntimeError("failed to link semantic settlement")
            self.deployment_gas = (
                verifier_gas + gateway_gas + settlement_gas + int(link_receipt.gasUsed)
            )
        else:
            self.verifier=self.web3.eth.contract(address=deployment["verifier"],abi=artifacts["Groth16Verifier"][0])
            self.gateway=self.web3.eth.contract(address=deployment["gateway"],abi=artifacts["AidMeshSemanticAuthorizationGateway"][0])
            self.settlement=self.web3.eth.contract(address=deployment["settlement"],abi=artifacts["AidMeshSettlement"][0])
            self.deployment_gas=int(deployment["deploymentGas"])

    def _deploy(self, artifact: tuple[list[dict[str, Any]], str], *args: object) -> tuple[Any, int]:
        abi, bytecode = artifact
        factory = self.web3.eth.contract(abi=abi, bytecode=bytecode)
        tx_hash = factory.constructor(*args).transact({"from": self.owner, "gas": EVM_TX_GAS})
        receipt = self.web3.eth.wait_for_transaction_receipt(tx_hash)
        if receipt.status != 1:
            raise RuntimeError("semantic E2E deployment failed")
        return (
            self.web3.eth.contract(address=receipt.contractAddress, abi=abi),
            int(receipt.gasUsed),
        )

    def sign(self, address: str, digest: bytes) -> bytes:
        return bytes(
            Account.sign_message(
                encode_defunct(primitive=digest),
                private_key=self.keys[address],
            ).signature
        )


def _select_conditions(primary: pd.DataFrame, registry: E2ERegistry) -> list[dict[str, Any]]:
    required = {"family", "registered_context", "seed", "cap"}
    missing = required.difference(primary.columns)
    if missing:
        raise ValueError(f"primary result lacks columns: {sorted(missing)}")
    conditions = []
    for context in registry.contexts:
        subset = primary.loc[primary["registered_context"] == context]
        if subset.empty:
            raise ValueError(f"primary result lacks registered context {context}")
        family = str(subset["family"].iloc[0])
        for seed in registry.seeds:
            seed_rows = subset.loc[subset["seed"] == seed]
            if seed_rows.empty:
                raise ValueError(f"primary result lacks {context} seed {seed}")
            caps = sorted({int(value) for value in seed_rows["cap"]})
            selected_caps = (caps[0], caps[-1]) if len(caps) > 1 else (caps[0],)
            for cap in selected_caps:
                for threshold in (2, 3):
                    conditions.append(
                        {
                            "family": family,
                            "registered_context": context,
                            "seed": int(seed),
                            "cap": cap,
                            "threshold": threshold,
                        }
                    )
    return conditions


def _receipt(
    harness: _Harness,
    *,
    artifact: ProofArtifact,
    authorization_hash: bytes,
    receipt_id: bytes,
    release_intent_id: bytes,
    receipt_skeleton_hash: bytes,
    site: str,
) -> tuple[Any, ...]:
    *_, public = artifact.calldata
    domain = evm_wire.proof_domain(public)
    statement = evm_wire.proof_statement_hash(public)
    payload = evm_wire.receipt_payload(
        domain,
        statement,
        receipt_id,
        site,
        release_intent_id,
        receipt_skeleton_hash,
        authorization_hash,
    )
    return (
        receipt_id,
        site,
        release_intent_id,
        receipt_skeleton_hash,
        authorization_hash,
        harness.sign(site, payload),
    )


def _measure_attempt(
    harness: _Harness,
    *,
    function: Any,
    site: str,
    expected: str,
    common: dict[str, Any],
) -> dict[str, Any]:
    before = int(harness.settlement.functions.credits(common["domain"], site).call())
    measured = _send(harness.web3, function, harness.relayer)
    after = int(harness.settlement.functions.credits(common["domain"], site).call())
    delta = after - before
    if expected == "settle":
        security_holds = measured["status"] == 1 and delta == 1
    elif expected == "reject":
        security_holds = measured["status"] == 0 and delta == 0
    elif expected == "idempotent":
        security_holds = delta == 0
    else:  # pragma: no cover - internal contract
        raise ValueError(expected)
    return {
        **common,
        **measured,
        "expected_outcome": expected,
        "credit_before": before,
        "credit_after": after,
        "settlement_credit_delta": delta,
        "security_holds": bool(security_holds),
    }


def _register_domain(
    harness: _Harness,
    *,
    public: list[int],
    threshold: int,
    cap: int,
    initial_funding: int = 2,
) -> bytes:
    domain = evm_wire.proof_domain(public)
    transactions = [
        harness.settlement.functions.registerCampaign(
            domain,
            public[14],
            public[15],
            public[16],
            list(harness.sites),
        ),
        harness.gateway.functions.registerAuthorizationDomain(
            domain,
            1,
            threshold,
            public[16],
            cap,
            list(harness.witnesses),
        ),
    ]
    if initial_funding:
        transactions.insert(
            1, harness.settlement.functions.fundCampaign(domain, initial_funding)
        )
    for index, function in enumerate(transactions):
        tx_hash = function.transact({"from": harness.owner, "gas": EVM_TX_GAS})
        receipt = harness.web3.eth.wait_for_transaction_receipt(tx_hash)
        if receipt.status != 1:
            raise RuntimeError(f"domain registration transaction {index} failed")
    return bytes(domain)


def _run_condition(
    root: Path,
    output: Path,
    harness: _Harness,
    registry: E2ERegistry,
    condition: dict[str, Any],
) -> list[dict[str, Any]]:
    context = str(condition["registered_context"])
    seed = int(condition["seed"])
    cap = int(condition["cap"])
    threshold = int(condition["threshold"])
    committee_hash = int(
        harness.gateway.functions.semanticCommitteeHash(
            1, threshold, cap, list(harness.witnesses)
        ).call()
    )
    domain_context = {
        "chainId": int(harness.web3.eth.chain_id),
        "gatewayAddress": int(harness.gateway.address, 16),
        "settlementAddress": int(harness.settlement.address, 16),
        "headerHash": _field(condition, domain="aidmesh/semantic-e2e/header/v1"),
        "policyHash": _field(
            {"cap": cap, "threshold": threshold, "value": registry.value},
            domain="aidmesh/semantic-e2e/policy/v1",
        ),
        "committeeHash": committee_hash,
    }
    prefix = f"{context}:s{seed}:c{cap}:q{threshold}"
    quorum_key = f"{prefix}:quorum"
    right_key = f"{prefix}:right"
    proofs = prepare_proof_cache(
        root,
        registry=registry,
        context=f"semantic-{context}-c{cap}-q{threshold}",
        seed=seed,
        domain_context=domain_context,
        voucher_keys=(quorum_key, right_key),
        output=output,
    )
    quorum_artifact = proofs[(quorum_key, "quorum")]
    right_artifact = proofs[(right_key, "fallback")]
    qa, qb, qc, qpublic = quorum_artifact.calldata
    ra, rb, rc, rpublic = right_artifact.calldata
    domain = _register_domain(
        harness,
        public=qpublic,
        threshold=threshold,
        cap=cap,
    )
    if bytes(evm_wire.proof_domain(rpublic)) != domain:
        raise AssertionError("quorum and right proofs do not share a registered domain")

    rows: list[dict[str, Any]] = []
    for path, artifact in (("quorum", quorum_artifact), ("transferable_right", right_artifact)):
        started = time.perf_counter_ns()
        verified = bool(harness.verifier.functions.verifyProof(*artifact.calldata).call())
        verify_ms = (time.perf_counter_ns() - started) / 1_000_000
        if not verified:
            raise AssertionError(f"real Groth16 verifier rejected {path} proof")
        condition[f"{path}_verify_ms"] = verify_ms

    quorum_receipt_id = Web3.keccak(text=f"{prefix}:quorum-receipt")
    quorum_release_intent_id = Web3.keccak(text=f"{prefix}:quorum-release-intent")
    quorum_skeleton_hash = evm_wire.receipt_skeleton(
        qpublic, quorum_release_intent_id, harness.sites[0], 1
    )
    quorum_hash = evm_wire.authorization_intent(
        qpublic,
        quorum_release_intent_id,
        quorum_skeleton_hash,
        harness.sites[0],
        1,
    )
    quorum_receipt = _receipt(
        harness,
        artifact=quorum_artifact,
        authorization_hash=quorum_hash,
        receipt_id=quorum_receipt_id,
        release_intent_id=quorum_release_intent_id,
        receipt_skeleton_hash=quorum_skeleton_hash,
        site=harness.sites[0],
    )
    quorum_signatures = [
        harness.sign(witness, quorum_hash) for witness in harness.witnesses[:threshold]
    ]
    quorum_function = harness.gateway.functions.verifyQuorumAndSettle(
        qa, qb, qc, qpublic, quorum_receipt, 1, quorum_signatures
    )
    quorum_common = {
        **condition,
        "domain": domain,
        "authorization_path": "quorum",
        "proof_mode": "quorum",
        "proof_generation_ms": quorum_artifact.proof_ms,
        "proof_verification_ms": condition["quorum_verify_ms"],
        "proof_sha256": quorum_artifact.proof_sha256,
        "public_sha256": quorum_artifact.public_sha256,
        "nullifier": quorum_artifact.nullifier,
    }
    rows.append(
        _measure_attempt(
            harness,
            function=harness.gateway.functions.verifyQuorumAndSettle(
                qa,
                qb,
                qc,
                qpublic,
                quorum_receipt,
                1,
                quorum_signatures[:-1],
            ),
            site=harness.sites[0],
            expected="reject",
            common={**quorum_common, "attempt": "insufficient_quorum"},
        )
    )
    rows.append(
        _measure_attempt(
            harness,
            function=quorum_function,
            site=harness.sites[0],
            expected="settle",
            common={**quorum_common, "attempt": "valid"},
        )
    )
    rows.append(
        _measure_attempt(
            harness,
            function=quorum_function,
            site=harness.sites[0],
            expected="idempotent",
            common={**quorum_common, "attempt": "duplicate"},
        )
    )

    right_id = Web3.keccak(text=f"{prefix}:right-id")
    issue = harness.gateway.functions.issueRight(
        domain, right_id, harness.sites[0], registry.value
    ).transact({"from": harness.owner, "gas": EVM_TX_GAS})
    if harness.web3.eth.wait_for_transaction_receipt(issue).status != 1:
        raise RuntimeError("right issuance failed")
    transition_hash = evm_wire.right_transition_statement(
        domain, right_id, harness.sites[0], harness.sites[1], 1, 1
    )
    valid_transition = (
        harness.sites[0],
        harness.sites[1],
        1,
        1,
        [harness.sign(witness, transition_hash) for witness in harness.witnesses[:threshold]],
    )
    invalid_transition = (*valid_transition[:4], valid_transition[4][:-1])
    right_receipt_id = Web3.keccak(text=f"{prefix}:right-receipt")
    right_release_intent_id = Web3.keccak(text=f"{prefix}:right-release-intent")
    right_skeleton_hash = evm_wire.receipt_skeleton(
        rpublic, right_release_intent_id, harness.sites[1], 1
    )
    right_hash = evm_wire.right_authorization_hash(
        rpublic,
        right_release_intent_id,
        right_skeleton_hash,
        harness.sites[1],
        right_id,
        1,
        [valid_transition],
    )
    right_receipt = _receipt(
        harness,
        artifact=right_artifact,
        authorization_hash=right_hash,
        receipt_id=right_receipt_id,
        release_intent_id=right_release_intent_id,
        receipt_skeleton_hash=right_skeleton_hash,
        site=harness.sites[1],
    )
    right_common = {
        **condition,
        "domain": domain,
        "authorization_path": "transferable_right",
        "proof_mode": "fallback",
        "proof_generation_ms": right_artifact.proof_ms,
        "proof_verification_ms": condition["transferable_right_verify_ms"],
        "proof_sha256": right_artifact.proof_sha256,
        "public_sha256": right_artifact.public_sha256,
        "nullifier": right_artifact.nullifier,
    }
    rows.append(
        _measure_attempt(
            harness,
            function=harness.gateway.functions.verifyRightAndSettle(
                ra,
                rb,
                rc,
                rpublic,
                right_receipt,
                right_id,
                1,
                [invalid_transition],
            ),
            site=harness.sites[1],
            expected="reject",
            common={**right_common, "attempt": "insufficient_transition_quorum"},
        )
    )
    right_function = harness.gateway.functions.verifyRightAndSettle(
        ra, rb, rc, rpublic, right_receipt, right_id, 1, [valid_transition]
    )
    rows.append(
        _measure_attempt(
            harness,
            function=right_function,
            site=harness.sites[1],
            expected="settle",
            common={**right_common, "attempt": "valid"},
        )
    )
    rows.append(
        _measure_attempt(
            harness,
            function=right_function,
            site=harness.sites[1],
            expected="idempotent",
            common={**right_common, "attempt": "duplicate"},
        )
    )
    return rows


def run_campaign(
    root: Path,
    *,
    registry_path: Path,
    primary_runs: Path,
    output: Path,
) -> dict[str, Path]:
    registry = load_e2e_registry(registry_path)
    primary = pd.read_csv(primary_runs)
    conditions = _select_conditions(primary, registry)
    output.mkdir(parents=True, exist_ok=True)
    harness = _Harness(root)
    rows: list[dict[str, Any]] = []
    for index, condition in enumerate(conditions, start=1):
        rows.extend(_run_condition(root, output, harness, registry, dict(condition)))
        print(
            json.dumps(
                {
                    "progress": f"{index}/{len(conditions)}",
                    **condition,
                    "rows": len(rows),
                }
            ),
            flush=True,
        )
    frame = pd.DataFrame(rows)
    runs = output / "semantic_e2e_runs.csv"
    frame.to_csv(runs, index=False, lineterminator="\n")
    summary = output / "semantic_e2e_summary.csv"
    (
        frame.groupby(["family", "registered_context", "authorization_path", "attempt"])
        .agg(
            trials=("security_holds", "size"),
            security_hold_rate=("security_holds", "mean"),
            settlement_probability=("settlement_credit_delta", lambda values: (values > 0).mean()),
            mean_gas=("gas_used", "mean"),
            p95_latency_ms=("latency_ms", lambda values: values.quantile(0.95)),
            mean_proof_ms=("proof_generation_ms", "mean"),
            mean_verify_ms=("proof_verification_ms", "mean"),
        )
        .reset_index()
        .to_csv(summary, index=False, lineterminator="\n")
    )
    manifest = output / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "campaign": "trace-stratified real-Groth16 semantic authorization E2E",
                "registry": str(registry_path),
                "registry_sha256": _sha256(registry_path),
                "primary_runs": str(primary_runs),
                "primary_runs_sha256": _sha256(primary_runs),
                "conditions": len(conditions),
                "rows": len(frame),
                "security_checks_passed": int(frame["security_holds"].sum()),
                "deployment_gas": harness.deployment_gas,
                "proof_scope": (
                    "real Groth16 verifier plus semantic quorum/right gateway and settlement"
                ),
                "source_sha256": {
                    path.name: _sha256(path)
                    for path in (
                        verifier_source or root / "contracts" / "src" / "AidMeshGroth16Verifier.sol",
                        root / "contracts" / "src" / "AidMeshSemanticAuthorizationGateway.sol",
                        root / "contracts" / "src" / "AidMeshSettlement.sol",
                    )
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    checksums = output / "CHECKSUMS.sha256"
    checksums.write_text(
        "".join(f"{_sha256(path)}  {path.name}\n" for path in (runs, summary, manifest)),
        encoding="ascii",
    )
    return {"runs": runs, "summary": summary, "manifest": manifest, "checksums": checksums}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, default=Path("configs/e2e/semantic_6h.yaml"))
    parser.add_argument(
        "--primary-runs",
        type=Path,
        default=Path("results/tdsc_full_20260822/primary/bow_primary_runs.csv"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("results/tdsc_full_20260822/semantic_e2e"),
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    outputs = run_campaign(
        root,
        registry_path=args.registry,
        primary_runs=args.primary_runs,
        output=args.output,
    )
    for label, path in outputs.items():
        print(f"{label}: {path}")


if __name__ == "__main__":
    main()
