"""Representative P1-v2 parity campaign against the public Sepolia EVM.

This is deliberately a small public-chain campaign.  The full trace matrix stays
on the reproducible local EVM; Sepolia checks that the exact compiled stack,
domain-bound proof, receipt signature and accounting transition behave the same
under a public RPC and block producer.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from eth_account import Account
from eth_account.messages import encode_defunct
from web3 import Web3
from web3.logs import DISCARD

from aidmesh.common.canonical import stable_hash
from aidmesh.experiments.e2e_campaign import (
    _build_header,
    _field_commitment,
    load_e2e_registry,
    prepare_proof_cache,
)
from aidmesh.experiments.testnet_deploy import compile_testnet_contracts

SEPOLIA_CHAIN_ID = 11_155_111


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _deployment_addresses(manifest: dict[str, Any]) -> dict[str, str]:
    linked = manifest.get("linked_context")
    if not isinstance(linked, dict):
        raise ValueError("deployment manifest has no canonical linked_context")
    required = ("verifier_address", "gateway_address", "settlement_address")
    output = {}
    for key in required:
        value = linked.get(key)
        if not isinstance(value, str) or not Web3.is_address(value):
            raise ValueError(f"deployment manifest has invalid {key}")
        output[key] = Web3.to_checksum_address(value)
    return output


def _fee_fields(web3: Web3) -> dict[str, int]:
    latest = web3.eth.get_block("latest")
    try:
        priority = int(web3.eth.max_priority_fee)
    except Exception:
        priority = 1_000_000_000
    base_fee = int(latest.get("baseFeePerGas", web3.eth.gas_price))
    return {
        "maxPriorityFeePerGas": priority,
        "maxFeePerGas": 2 * base_fee + priority,
    }


def _receipt_evidence(web3: Web3, tx_hash: Any, receipt: Any) -> dict[str, Any]:
    block = web3.eth.get_block(receipt.blockNumber)
    price = int(receipt.effectiveGasPrice)
    return {
        "transaction_hash": tx_hash.hex(),
        "explorer_url": f"https://sepolia.etherscan.io/tx/{tx_hash.hex()}",
        "status": int(receipt.status),
        "block_number": int(receipt.blockNumber),
        "block_hash": receipt.blockHash.hex(),
        "block_timestamp": int(block.timestamp),
        "gas_used": int(receipt.gasUsed),
        "effective_gas_price_wei": price,
        "transaction_cost_wei": int(receipt.gasUsed) * price,
    }


def _node_package_version(root: Path, package: str) -> str:
    completed = subprocess.run(
        ["node", "-p", f"require('./node_modules/{package}/package.json').version"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _scan_campaign_events_from_receipts(
    web3: Web3,
    *,
    settlement: Any,
    gateway: Any,
    from_block: int,
    to_block: int,
    domain: bytes,
) -> tuple[list[Any], list[Any], list[Any]]:
    """Decode campaign events from block receipts without using eth_getLogs."""

    registered: list[Any] = []
    funded: list[Any] = []
    bound: list[Any] = []
    settlement_address = Web3.to_checksum_address(settlement.address)
    gateway_address = Web3.to_checksum_address(gateway.address)
    try:
        for block_number in range(from_block, to_block + 1):
            block = web3.eth.get_block(block_number, full_transactions=True)
            for transaction in block.transactions:
                destination = transaction.get("to")
                if destination is None:
                    continue
                destination = Web3.to_checksum_address(destination)
                if destination not in (settlement_address, gateway_address):
                    continue
                receipt = web3.eth.get_transaction_receipt(transaction["hash"])
                if destination == settlement_address:
                    decoded_groups = (
                        (
                            registered,
                            settlement.events.CampaignRegistered().process_receipt(
                                receipt, errors=DISCARD
                            ),
                        ),
                        (
                            funded,
                            settlement.events.CampaignFunded().process_receipt(
                                receipt, errors=DISCARD
                            ),
                        ),
                    )
                else:
                    decoded_groups = (
                        (
                            bound,
                            gateway.events.SettlementBound().process_receipt(
                                receipt, errors=DISCARD
                            ),
                        ),
                    )
                for target, decoded in decoded_groups:
                    target.extend(
                        log
                        for log in decoded
                        if bytes(log["args"]["domain"]) == domain
                    )
            if registered and funded and len(bound) >= 3:
                break
    except Exception:
        raise ConnectionError(
            "Sepolia block-receipt event recovery failed; RPC details were redacted"
        ) from None
    return registered, funded, bound


class _Sender:
    def __init__(self, web3: Web3, private_key: str) -> None:
        self.web3 = web3
        self.account = web3.eth.account.from_key(private_key)
        self.nonce = web3.eth.get_transaction_count(self.account.address, "pending")

    def send(self, function: Any, *, label: str) -> dict[str, Any]:
        transaction = function.build_transaction(
            {
                "from": self.account.address,
                "nonce": self.nonce,
                "chainId": SEPOLIA_CHAIN_ID,
                **_fee_fields(self.web3),
            }
        )
        transaction["gas"] = max(
            50_000, int(self.web3.eth.estimate_gas(transaction) * 1.20)
        )
        signed = self.account.sign_transaction(transaction)
        submitted_unix_ns = time.time_ns()
        started = time.perf_counter_ns()
        tx_hash = self.web3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = self.web3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
        receipt_wait_ms = (time.perf_counter_ns() - started) / 1_000_000
        self.nonce += 1
        evidence = _receipt_evidence(self.web3, tx_hash, receipt)
        evidence["label"] = label
        evidence["submitted_unix_ns"] = submitted_unix_ns
        evidence["receipt_wait_ms"] = receipt_wait_ms
        if evidence["status"] != 1:
            raise RuntimeError(f"Sepolia transaction reverted: {label} {tx_hash.hex()}")
        return evidence


def preflight_sepolia(
    *,
    rpc_url: str,
    private_key: str | None = None,
) -> dict[str, Any]:
    web3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 30}))
    if not web3.is_connected():
        raise ConnectionError("cannot connect to Sepolia RPC")
    chain_id = int(web3.eth.chain_id)
    if chain_id != SEPOLIA_CHAIN_ID:
        raise ValueError(
            f"refusing public-chain action: expected {SEPOLIA_CHAIN_ID}, got {chain_id}"
        )
    latest = web3.eth.get_block("latest")
    result: dict[str, Any] = {
        "schema_version": 1,
        "status": "ready",
        "chain_id": chain_id,
        "latest_block": int(latest.number),
        "latest_block_hash": latest.hash.hex(),
        "latest_block_timestamp": int(latest.timestamp),
        "client_version": web3.client_version,
        "gas_price_wei": int(web3.eth.gas_price),
        "private_key_present": private_key is not None,
    }
    if private_key:
        account = web3.eth.account.from_key(private_key)
        result.update(
            {
                "account": account.address,
                "balance_wei": int(web3.eth.get_balance(account.address)),
                "pending_nonce": int(
                    web3.eth.get_transaction_count(account.address, "pending")
                ),
            }
        )
    return result


def run_sepolia_parity_campaign(
    root: Path,
    *,
    rpc_url: str,
    private_key: str,
    deployment_manifest: Path,
    output: Path,
    run_id: str,
    confirmations: int = 3,
    confirmatory_plan_sha256: str | None = None,
) -> Path:
    allowed = "-_abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    if not run_id or any(character not in allowed for character in run_id):
        raise ValueError("run_id must contain only letters, numbers, '-' or '_'")
    if confirmations < 1:
        raise ValueError("confirmations must be positive")
    preflight = preflight_sepolia(rpc_url=rpc_url, private_key=private_key)
    web3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 60}))
    sender = _Sender(web3, private_key)
    manifest = json.loads(deployment_manifest.read_text(encoding="utf-8"))
    if manifest.get("chain_id") != SEPOLIA_CHAIN_ID or manifest.get("dry_run"):
        raise ValueError("campaign requires a live Sepolia deployment manifest")
    addresses = _deployment_addresses(manifest)
    if Web3.to_checksum_address(manifest["deployer_address"]) != sender.account.address:
        raise ValueError("campaign key is not the owner recorded in deployment manifest")

    compiled = compile_testnet_contracts(root)
    gateway = web3.eth.contract(
        address=addresses["gateway_address"],
        abi=compiled["AidMeshVerifiedSettlementGateway"].abi,
    )
    settlement = web3.eth.contract(
        address=addresses["settlement_address"],
        abi=compiled["AidMeshSettlement"].abi,
    )
    verifier_code = bytes(web3.eth.get_code(addresses["verifier_address"]))
    gateway_code = bytes(web3.eth.get_code(addresses["gateway_address"]))
    settlement_code = bytes(web3.eth.get_code(addresses["settlement_address"]))
    if not verifier_code or not gateway_code or not settlement_code:
        raise AssertionError("one or more deployed contracts have empty runtime code")
    if Web3.to_checksum_address(gateway.functions.owner().call()) != sender.account.address:
        raise ValueError("campaign key is not gateway owner")
    if Web3.to_checksum_address(settlement.functions.owner().call()) != sender.account.address:
        raise ValueError("campaign key is not settlement owner")
    if Web3.to_checksum_address(gateway.functions.settlement().call()) != addresses[
        "settlement_address"
    ]:
        raise AssertionError("gateway is not linked to manifest settlement")

    registry = replace(
        load_e2e_registry(root / "configs" / "e2e" / "registry.yaml"),
        registry_id=f"SEPOLIA_PARITY_{run_id}",
        contexts=("sepolia",),
        seeds=(0,),
        voucher_sample_per_context=2,
    )
    campaign_id = int(
        stable_hash(run_id, domain="aidmesh/sepolia/campaign-id/v1")[:30], 16
    )
    header = _build_header(
        campaign_id=campaign_id,
        sites=("sepolia-site",),
        cap=2,
        start_us=0,
        end_us=1,
    )
    policy = {
        "schema": "aidmesh/proof-policy/v2",
        "risk_cell": header.risk_cell,
        "fallback_categories": list(header.fallback_categories),
        "fallback_window": registry.fallback_window,
        "value": registry.value,
        "registered_cap": 2,
        "risk_right_semantics": "conserved-transferable-or-static",
        "settlement_semantics": (
            "proof-and-authorization-commitment-gated-accounting-conformance"
        ),
        "public_chain_profile": "sepolia-representative-parity-v1",
        "run_id": run_id,
        "confirmatory_plan_sha256": confirmatory_plan_sha256,
    }
    committee = {
        "committee_id": header.committee.committee_id,
        "members": list(header.committee.members),
        "threshold": header.committee.threshold,
        "byzantine_bound": header.committee.byzantine_bound,
    }
    domain_context = {
        "chainId": SEPOLIA_CHAIN_ID,
        "gatewayAddress": int(addresses["gateway_address"], 16),
        "settlementAddress": int(addresses["settlement_address"], 16),
        "headerHash": _field_commitment(
            header.header_hash, domain="aidmesh/e2e/full-header-field/v2"
        ),
        "policyHash": _field_commitment(
            policy, domain="aidmesh/e2e/full-policy-field/v2"
        ),
        "committeeHash": _field_commitment(
            committee, domain="aidmesh/e2e/committee-field/v2"
        ),
    }
    proof_root = output / "proof_bundle"
    voucher_keys = (f"{run_id}-voucher-quorum", f"{run_id}-voucher-fallback")
    artifacts = prepare_proof_cache(
        root,
        registry=registry,
        context="sepolia",
        seed=0,
        domain_context=domain_context,
        voucher_keys=voucher_keys,
        output=proof_root,
    )
    artifact = artifacts[(voucher_keys[0], "quorum")]
    fallback_artifact = artifacts[(voucher_keys[1], "fallback")]
    a, b, c, public = artifact.calldata
    fallback_a, fallback_b, fallback_c, fallback_public = fallback_artifact.calldata
    if public[11:17] != [
        domain_context["chainId"],
        domain_context["gatewayAddress"],
        domain_context["settlementAddress"],
        domain_context["headerHash"],
        domain_context["policyHash"],
        domain_context["committeeHash"],
    ]:
        raise AssertionError("generated public inputs do not match Sepolia domain")
    if fallback_public[11:17] != public[11:17]:
        raise AssertionError("fallback proof deployment domain differs from quorum proof")
    if fallback_public[8] != 1 or fallback_public[9] <= 0:
        raise AssertionError("fallback proof does not expose the registered fallback mode")
    if not gateway.functions.verifier().call() == addresses["verifier_address"]:
        raise AssertionError("gateway verifier differs from deployment manifest")

    domain = bytes(gateway.functions.proofDomain(public).call())
    if bytes(gateway.functions.proofDomain(fallback_public).call()) != domain:
        raise AssertionError("quorum and fallback proofs do not share one safety domain")

    quorum_evidence_hash = Web3.keccak(
        text=f"aidmesh|{run_id}|representative-quorum-authorization-evidence"
    )
    fallback_evidence_hash = Web3.keccak(
        text=f"aidmesh|{run_id}|representative-fallback-authorization-evidence"
    )

    def bound_receipt(
        receipt_id: bytes,
        *,
        statement: list[int] = public,
        evidence_hash: bytes = quorum_evidence_hash,
        signer: Any = sender.account,
        site: str | None = None,
    ) -> tuple[tuple[bytes, str, bytes, bytes], bytes]:
        bound_site = site or sender.account.address
        statement_hash = gateway.functions.proofStatementHash(statement).call()
        authorization = gateway.functions.authorizationCommitment(
            statement, receipt_id, bound_site, evidence_hash
        ).call()
        payload = gateway.functions.receiptPayload(
            domain, statement_hash, receipt_id, bound_site, authorization
        ).call()
        signature = bytes(
            Account.sign_message(
                encode_defunct(primitive=bytes(payload)),
                private_key=signer.key,
            ).signature
        )
        return (receipt_id, bound_site, evidence_hash, signature), bytes(authorization)

    first_receipt = Web3.keccak(text=f"aidmesh|{run_id}|valid")
    fallback_receipt = Web3.keccak(text=f"aidmesh|{run_id}|valid-fallback")
    replay_receipt = Web3.keccak(text=f"aidmesh|{run_id}|replay")
    first_bound_receipt, first_authorization = bound_receipt(first_receipt)
    fallback_bound_receipt, fallback_authorization = bound_receipt(
        fallback_receipt,
        statement=fallback_public,
        evidence_hash=fallback_evidence_hash,
    )
    replay_bound_receipt, _ = bound_receipt(replay_receipt)
    campaign_state = settlement.functions.campaigns(domain).call()
    recovered_from_chain = bool(campaign_state[4])
    recovery_event_sources: dict[str, str] = {}
    if not recovered_from_chain:
        transactions = [
            sender.send(
                settlement.functions.registerCampaign(
                    domain,
                    public[14],
                    public[15],
                    public[16],
                    [sender.account.address],
                ),
                label="register_campaign",
            ),
            sender.send(
                settlement.functions.fundCampaign(domain, 3),
                label="fund_campaign",
            ),
        ]
        transactions.append(
            sender.send(
                gateway.functions.verifyAndSettle(
                    a,
                    b,
                    c,
                    public,
                    first_bound_receipt,
                ),
                label="valid_verify_and_settle",
            )
        )
        transactions.append(
            sender.send(
                gateway.functions.verifyAndSettle(
                    fallback_a,
                    fallback_b,
                    fallback_c,
                    fallback_public,
                    fallback_bound_receipt,
                ),
                label="valid_fallback_verify_and_settle",
            )
        )
        transactions.append(
            sender.send(
                gateway.functions.verifyAndSettle(
                    a,
                    b,
                    c,
                    public,
                    replay_bound_receipt,
                ),
                label="duplicate_nullifier_replay",
            )
        )
        _write_json(
            output / "sepolia_transaction_checkpoint.json",
            {
                "schema_version": 1,
                "run_id": run_id,
                "domain": "0x" + domain.hex(),
                "transactions": transactions,
            },
        )
    else:
        deployment_blocks = [
            int(row["block_number"])
            for row in manifest["deployments"]
            if row.get("block_number") is not None
        ]
        from_block = min(deployment_blocks)
        # A complete parity run uses five consecutive transactions immediately
        # after deployment.  Bound the recovery scan to avoid an unbounded RPC
        # query if evidence is reconstructed much later.
        to_block = min(int(web3.eth.block_number), max(deployment_blocks) + 128)
        try:
            registered_logs = list(
                settlement.events.CampaignRegistered().get_logs(
                    from_block=from_block,
                    to_block=to_block,
                    argument_filters={"domain": domain},
                )
            )
            funded_logs = list(
                settlement.events.CampaignFunded().get_logs(
                    from_block=from_block,
                    to_block=to_block,
                    argument_filters={"domain": domain},
                )
            )
            bound_logs = list(
                gateway.events.SettlementBound().get_logs(
                    from_block=from_block,
                    to_block=to_block,
                    argument_filters={"domain": domain},
                )
            )
            registration_source = funding_source = settlement_source = "eth_getLogs"
        except Exception:
            registered_logs, funded_logs, bound_logs = (
                _scan_campaign_events_from_receipts(
                    web3,
                    settlement=settlement,
                    gateway=gateway,
                    from_block=from_block,
                    to_block=to_block,
                    domain=domain,
                )
            )
            registration_source = funding_source = settlement_source = (
                "block_receipt_scan"
            )
        expected_receipts = {
            bytes(first_receipt): ("valid_verify_and_settle", True),
            bytes(fallback_receipt): ("valid_fallback_verify_and_settle", True),
            bytes(replay_receipt): ("duplicate_nullifier_replay", False),
        }
        observed_bound = {
            bytes(log["args"]["receiptId"]): log for log in bound_logs
        }
        if (
            len(registered_logs) != 1
            or len(funded_logs) < 1
            or any(receipt not in observed_bound for receipt in expected_receipts)
            or any(
                bool(observed_bound[receipt]["args"]["settled"]) != expected_settled
                for receipt, (_, expected_settled) in expected_receipts.items()
                if receipt in observed_bound
            )
        ):
            raise RuntimeError(
                "existing Sepolia domain is only partially complete; preserve it and "
                "use a new RunId rather than resubmitting ambiguous transactions"
            )
        labeled_logs = [
            ("register_campaign", registered_logs[0]),
            ("fund_campaign", funded_logs[0]),
            *[
                (label, observed_bound[receipt])
                for receipt, (label, _) in expected_receipts.items()
            ],
        ]
        transactions = []
        for label, log in labeled_logs:
            tx_hash = log["transactionHash"]
            receipt = web3.eth.get_transaction_receipt(tx_hash)
            evidence = _receipt_evidence(web3, tx_hash, receipt)
            evidence.update(
                {
                    "label": label,
                    "submitted_unix_ns": None,
                    "receipt_wait_ms": None,
                    "evidence_source": "recovered_from_public_chain_logs",
                }
            )
            transactions.append(evidence)
        recovery_event_sources = {
            "registration": registration_source,
            "funding": funding_source,
            "settlement": settlement_source,
        }
        _write_json(
            output / "sepolia_transaction_checkpoint.json",
            {
                "schema_version": 1,
                "run_id": run_id,
                "domain": "0x" + domain.hex(),
                "recovered_from_chain": True,
                "event_sources": recovery_event_sources,
                "transactions": transactions,
            },
        )

    credit_after_valid = 1
    credit_after_fallback = int(
        settlement.functions.credits(domain, sender.account.address).call()
    )
    credit_after_replay = credit_after_fallback

    controls: list[dict[str, Any]] = []

    def rejected(
        name: str,
        args: tuple[Any, ...],
        expected: str,
    ) -> None:
        observed = "accepted"
        error = ""
        block = int(web3.eth.block_number)
        try:
            gateway.functions.verifyAndSettle(*args).call(
                {"from": sender.account.address}, block_identifier=block
            )
        except Exception as exception:
            observed = "rejected"
            error = str(exception)
        controls.append(
            {
                "case": name,
                "execution": "eth_call_at_fixed_block",
                "block_number": block,
                "expected_revert_fragment": expected,
                "observed": observed,
                "passed": observed == "rejected" and expected in error,
                "error": error[:500],
            }
        )

    for name, index, expected in (
        ("cross_chain_replay", 11, "WRONG_CHAIN"),
        ("cross_gateway_replay", 12, "WRONG_GATEWAY"),
        ("cross_settlement_replay", 13, "WRONG_SETTLEMENT"),
        ("mutated_header", 14, "INVALID_PROOF"),
        ("mutated_policy", 15, "INVALID_PROOF"),
        ("mutated_committee", 16, "INVALID_PROOF"),
    ):
        mutated = list(public)
        mutated[index] += 1
        receipt_id = Web3.keccak(text=f"aidmesh|{run_id}|{name}")
        rejected(
            name,
            (
                a,
                b,
                c,
                mutated,
                bound_receipt(receipt_id)[0],
            ),
            expected,
        )
    malformed_a = list(a)
    malformed_a[0] += 1
    bad_proof_receipt = Web3.keccak(text=f"aidmesh|{run_id}|bad-proof")
    rejected(
        "mutated_proof_point",
        (
            malformed_a,
            b,
            c,
            public,
            bound_receipt(bad_proof_receipt)[0],
        ),
        "execution reverted",
    )
    attacker = Account.create()
    wrong_signature_receipt = Web3.keccak(text=f"aidmesh|{run_id}|wrong-signature")
    rejected(
        "wrong_site_signature",
        (
            a,
            b,
            c,
            public,
            bound_receipt(wrong_signature_receipt, signer=attacker)[0],
        ),
        "WRONG_SITE_SIGNATURE",
    )
    unauthorized_receipt = Web3.keccak(text=f"aidmesh|{run_id}|unauthorized")
    rejected(
        "unauthorized_site",
        (
            a,
            b,
            c,
            public,
            bound_receipt(
                unauthorized_receipt, signer=attacker, site=attacker.address
            )[0],
        ),
        "UNAUTHORIZED_SITE",
    )
    rebound_receipt = Web3.keccak(text=f"aidmesh|{run_id}|rebound-new")
    old_receipt = Web3.keccak(text=f"aidmesh|{run_id}|rebound-old")
    old_bound_receipt, _ = bound_receipt(old_receipt)
    rejected(
        "receipt_id_rebinding",
        (
            a,
            b,
            c,
            public,
            (
                rebound_receipt,
                old_bound_receipt[1],
                old_bound_receipt[2],
                old_bound_receipt[3],
            ),
        ),
        "WRONG_SITE_SIGNATURE",
    )
    authorization_receipt = Web3.keccak(text=f"aidmesh|{run_id}|auth-rebind")
    signed_authorization_receipt, _ = bound_receipt(authorization_receipt)
    rebound_evidence = Web3.keccak(text=f"aidmesh|{run_id}|different-evidence")
    rejected(
        "authorization_evidence_rebinding",
        (
            a,
            b,
            c,
            public,
            (
                signed_authorization_receipt[0],
                signed_authorization_receipt[1],
                rebound_evidence,
                signed_authorization_receipt[3],
            ),
        ),
        "WRONG_SITE_SIGNATURE",
    )
    zero_evidence_receipt = Web3.keccak(text=f"aidmesh|{run_id}|zero-evidence")
    signed_zero_control, _ = bound_receipt(zero_evidence_receipt)
    rejected(
        "zero_authorization_evidence",
        (
            a,
            b,
            c,
            public,
            (
                signed_zero_control[0],
                signed_zero_control[1],
                bytes(32),
                signed_zero_control[3],
            ),
        ),
        "ZERO_AUTHORIZATION_EVIDENCE",
    )
    if not all(control["passed"] for control in controls):
        raise AssertionError(f"Sepolia negative control failed: {controls}")
    if (
        credit_after_valid != 1
        or credit_after_fallback != 2
        or credit_after_replay != credit_after_fallback
    ):
        raise AssertionError("Sepolia duplicate-nullifier accounting invariant failed")
    winner = bytes(
        settlement.functions.winningReceipt(domain, bytes.fromhex(f"{public[7]:064x}")).call()
    )
    escrow = int(settlement.functions.campaigns(domain).call()[3])
    fallback_winner = bytes(
        settlement.functions.winningReceipt(
            domain, bytes.fromhex(f"{fallback_public[7]:064x}")
        ).call()
    )
    winning_authorization = bytes(
        settlement.functions.winningAuthorization(
            domain, bytes.fromhex(f"{public[7]:064x}")
        ).call()
    )
    fallback_winning_authorization = bytes(
        settlement.functions.winningAuthorization(
            domain, bytes.fromhex(f"{fallback_public[7]:064x}")
        ).call()
    )
    if (
        winner != bytes(first_receipt)
        or fallback_winner != bytes(fallback_receipt)
        or winning_authorization != first_authorization
        or fallback_winning_authorization != fallback_authorization
        or escrow != 1
    ):
        raise AssertionError("Sepolia final settlement state differs from expected state")

    target_block = transactions[-1]["block_number"] + confirmations - 1
    deadline = time.monotonic() + 600
    while int(web3.eth.block_number) < target_block:
        if time.monotonic() >= deadline:
            raise TimeoutError("confirmation-depth wait exceeded 600 seconds")
        time.sleep(6)
    final_block = int(web3.eth.block_number)
    for transaction in transactions:
        transaction["observed_confirmation_depth"] = (
            final_block - transaction["block_number"] + 1
        )
    report = {
        "schema_version": 1,
        "status": "validated",
        "run_id": run_id,
        "scope": "representative public-chain P1-v2 parity; not a full trace matrix",
        "preflight": preflight,
        "deployment_manifest": str(deployment_manifest),
        "deployment_manifest_sha256": _sha256(deployment_manifest),
        "addresses": addresses,
        "runtime_code_sha256": {
            "verifier": hashlib.sha256(verifier_code).hexdigest(),
            "gateway": hashlib.sha256(gateway_code).hexdigest(),
            "settlement": hashlib.sha256(settlement_code).hexdigest(),
        },
        "domain_context": domain_context,
        "proofs": [{
            "profile": registry.profile,
            "mode": artifact.mode,
            "proof_ms": artifact.proof_ms,
            "proof_sha256": artifact.proof_sha256,
            "public_sha256": artifact.public_sha256,
        }, {
            "profile": registry.profile,
            "mode": fallback_artifact.mode,
            "proof_ms": fallback_artifact.proof_ms,
            "proof_sha256": fallback_artifact.proof_sha256,
            "public_sha256": fallback_artifact.public_sha256,
        }],
        "proving_toolchain": {
            "snarkjs_version": _node_package_version(root, "snarkjs"),
            "srs_scope": "single-operator development zkey; experimental only",
        },
        "transactions": transactions,
        "recovered_from_public_chain": recovered_from_chain,
        "recovery_event_sources": recovery_event_sources,
        "negative_controls": controls,
        "state": {
            "credit_after_valid": credit_after_valid,
            "credit_after_fallback": credit_after_fallback,
            "credit_after_replay": credit_after_replay,
            "escrow_after_replay": escrow,
            "winning_receipt": "0x" + winner.hex(),
            "fallback_winning_receipt": "0x" + fallback_winner.hex(),
            "winning_authorization": "0x" + winning_authorization.hex(),
            "fallback_winning_authorization": (
                "0x" + fallback_winning_authorization.hex()
            ),
            "duplicate_created_no_additional_credit": credit_after_replay
            == credit_after_fallback,
        },
        "confirmation_rule": {
            "requested_depth": confirmations,
            "target_block": target_block,
            "observed_head": final_block,
            "satisfied": final_block >= target_block,
        },
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "web3": importlib.metadata.version("web3"),
        },
        "limitations": [
            "The public campaign is a representative parity subset, not the held-out trace matrix.",
            "Negative controls use eth_call at a fixed public block and are not mined failures.",
            "One disposable Sepolia key serves owner, relayer and authorized-site roles.",
            "Sepolia has a permissioned validator set and is not Mainnet evidence.",
            "The development zkey does not establish production ceremony trust.",
            "The EVM binds authorization evidence but validates it off-chain.",
        ],
    }
    report_path = output / "sepolia_parity_report.json"
    _write_json(report_path, report)
    checksum_path = output / "CHECKSUMS.sha256"
    files = sorted(
        path
        for path in output.rglob("*")
        if path.is_file() and path != checksum_path
    )
    checksum_path.write_text(
        "".join(
            f"{_sha256(path)}  {path.relative_to(output).as_posix()}\n"
            for path in files
        ),
        encoding="ascii",
    )
    return report_path


def run_sepolia_parity_from_env(
    root: Path,
    *,
    deployment_manifest: Path,
    output: Path,
    run_id: str,
    confirmations: int,
    rpc_env: str = "SEPOLIA_RPC_URL",
    key_env: str = "SEPOLIA_PRIVATE_KEY",
) -> Path:
    rpc_url = os.environ.get(rpc_env)
    private_key = os.environ.get(key_env)
    if not rpc_url or not private_key:
        raise ValueError(f"missing {rpc_env} or {key_env}")
    return run_sepolia_parity_campaign(
        root,
        rpc_url=rpc_url,
        private_key=private_key,
        deployment_manifest=deployment_manifest,
        output=output,
        run_id=run_id,
        confirmations=confirmations,
    )


def _load_locked_plan(plan: Path, checksum: Path) -> tuple[dict[str, Any], str]:
    import yaml

    observed = _sha256(plan)
    expected_line = checksum.read_text(encoding="ascii").strip()
    expected = expected_line.split()[0].lower()
    if observed != expected:
        raise ValueError("locked Sepolia plan checksum mismatch")
    cancellation = plan.with_name("campaign_plan.cancelled.json")
    if cancellation.exists():
        cancelled = json.loads(cancellation.read_text(encoding="utf-8"))
        if (
            cancelled.get("status") == "cancelled_before_registered_execution"
            and cancelled.get("plan_sha256") == observed
        ):
            raise ValueError(
                "Sepolia multi-window plan was cancelled before registered execution"
            )
    raw = yaml.safe_load(plan.read_text(encoding="utf-8"))
    if raw.get("status") != "LOCKED":
        raise ValueError("Sepolia plan is not LOCKED")
    runs = raw.get("registered_runs")
    if not isinstance(runs, list) or len(runs) != int(raw["design"]["total_runs"]):
        raise ValueError("locked Sepolia plan run matrix is malformed")
    run_ids = [str(row["run_id"]) for row in runs]
    if len(run_ids) != len(set(run_ids)) or any("REPLACE" in value for value in run_ids):
        raise ValueError("locked Sepolia plan run IDs are invalid")
    project_root = plan.resolve().parents[2]
    registered_output = (project_root / str(raw["output_root"])).resolve()
    collisions: list[str] = []
    for report_path in (project_root / "results" / "testnet").rglob(
        "sepolia_parity_report.json"
    ):
        if registered_output in report_path.resolve().parents:
            continue
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if str(report.get("run_id")) in run_ids:
            collisions.append(str(report_path.relative_to(project_root)))
    if collisions:
        raise ValueError(
            "locked Sepolia run ID collides with prior evidence: "
            + ", ".join(sorted(collisions))
        )
    return raw, observed


def check_sepolia_plan(plan: Path, checksum: Path) -> dict[str, Any]:
    raw, plan_hash = _load_locked_plan(plan, checksum)
    now = datetime.now(UTC)
    windows: list[dict[str, Any]] = []
    for window in sorted({int(row["window"]) for row in raw["registered_runs"]}):
        rows = [row for row in raw["registered_runs"] if int(row["window"]) == window]
        opened = datetime.fromisoformat(str(rows[0]["window_open_utc"]).replace("Z", "+00:00"))
        closed = datetime.fromisoformat(
            str(rows[0]["window_close_utc"]).replace("Z", "+00:00")
        )
        windows.append(
            {
                "window": window,
                "open_utc": opened.isoformat(),
                "close_utc": closed.isoformat(),
                "currently_open": opened <= now <= closed,
                "run_ids": [str(row["run_id"]) for row in rows],
            }
        )
    return {
        "schema_version": 1,
        "status": "locked_valid",
        "plan_sha256": plan_hash,
        "checked_at_utc": now.isoformat(),
        "campaign_id": raw["campaign_id"],
        "runs": len(raw["registered_runs"]),
        "windows": windows,
    }


def run_sepolia_plan_window_from_env(
    root: Path,
    *,
    plan: Path,
    checksum: Path,
    window: int,
    analysis_output: Path,
    rpc_env: str = "SEPOLIA_RPC_URL",
    key_env: str = "SEPOLIA_PRIVATE_KEY",
) -> list[Path]:
    raw, plan_hash = _load_locked_plan(plan, checksum)
    rows = [
        row for row in raw["registered_runs"] if int(row["window"]) == int(window)
    ]
    if not rows:
        raise ValueError(f"window {window} is absent from locked Sepolia plan")
    now = datetime.now(UTC)
    opened = datetime.fromisoformat(
        str(rows[0]["window_open_utc"]).replace("Z", "+00:00")
    )
    closed = datetime.fromisoformat(
        str(rows[0]["window_close_utc"]).replace("Z", "+00:00")
    )
    if not opened <= now <= closed:
        raise ValueError(
            f"window {window} is closed; allowed UTC interval is "
            f"{opened.isoformat()} to {closed.isoformat()}"
        )
    rpc_url = os.environ.get(rpc_env)
    private_key = os.environ.get(key_env)
    if not rpc_url or not private_key:
        raise ValueError(f"missing {rpc_env} or {key_env}")
    deployment = root / str(raw["deployment_manifest"])
    output_root = root / str(raw["output_root"])
    completed: list[Path] = []
    for row in rows:
        run_id = str(row["run_id"])
        output = output_root / run_id
        report = output / "sepolia_parity_report.json"
        if report.exists():
            existing = json.loads(report.read_text(encoding="utf-8"))
            if (
                existing.get("status") != "validated"
                or existing.get("run_id") != run_id
                or existing.get("confirmatory_plan_sha256") != plan_hash
            ):
                raise AssertionError(f"invalid existing registered report: {report}")
            completed.append(report)
            continue
        completed.append(
            run_sepolia_parity_campaign(
                root,
                rpc_url=rpc_url,
                private_key=private_key,
                deployment_manifest=deployment,
                output=output,
                run_id=run_id,
                confirmations=int(raw["confirmation_depth"]),
                confirmatory_plan_sha256=plan_hash,
            )
        )
    analyze_sepolia_campaign(
        output_root,
        analysis_output,
        plan=plan,
        allow_partial=True,
    )
    return completed


def analyze_sepolia_campaign(
    input_root: Path,
    output: Path,
    *,
    plan: Path | None = None,
    allow_partial: bool = False,
) -> Path:
    reports = sorted(input_root.rglob("sepolia_parity_report.json"))
    if not reports:
        raise FileNotFoundError(f"no Sepolia parity reports under {input_root}")
    registered_ids: set[str] | None = None
    plan_hash: str | None = None
    planned_runs: int | None = None
    if plan is not None:
        import yaml

        raw_plan = yaml.safe_load(plan.read_text(encoding="utf-8"))
        if raw_plan.get("status") != "LOCKED":
            raise ValueError("Sepolia confirmatory plan must have status: LOCKED")
        registered = raw_plan.get("registered_runs")
        if not isinstance(registered, list) or not registered:
            raise ValueError("Sepolia confirmatory plan has no registered runs")
        registered_ids = {str(row["run_id"]) for row in registered}
        if len(registered_ids) != len(registered):
            raise ValueError("Sepolia confirmatory plan contains duplicate run IDs")
        if any("REPLACE" in run_id for run_id in registered_ids):
            raise ValueError("Sepolia confirmatory plan still contains placeholders")
        planned_runs = int(raw_plan["design"]["total_runs"])
        if planned_runs != len(registered_ids):
            raise ValueError("plan total_runs differs from registered run count")
        plan_hash = _sha256(plan)
    run_rows: list[dict[str, Any]] = []
    transaction_rows: list[dict[str, Any]] = []
    control_rows: list[dict[str, Any]] = []
    deployment_hashes: set[str] = set()
    runtime_hashes: set[str] = set()
    run_ids: set[str] = set()
    for path in reports:
        report = json.loads(path.read_text(encoding="utf-8"))
        run_id = str(report["run_id"])
        if run_id in run_ids:
            raise ValueError(f"duplicate Sepolia run_id: {run_id}")
        run_ids.add(run_id)
        if report.get("status") != "validated":
            raise AssertionError(f"non-validated Sepolia report: {path}")
        if report["preflight"]["chain_id"] != SEPOLIA_CHAIN_ID:
            raise AssertionError(f"wrong chain in {path}")
        deployment_hashes.add(str(report["deployment_manifest_sha256"]))
        runtime_hashes.add(
            stable_hash(
                report["runtime_code_sha256"],
                domain="aidmesh/sepolia/runtime-set/v1",
            )
        )
        state = report["state"]
        controls = report["negative_controls"]
        transactions = report["transactions"]
        run_rows.append(
            {
                "run_id": run_id,
                "transactions": len(transactions),
                "negative_controls": len(controls),
                "negative_controls_passed": sum(bool(row["passed"]) for row in controls),
                "credit_after_quorum": int(state["credit_after_valid"]),
                "credit_after_fallback": int(state["credit_after_fallback"]),
                "credit_after_replay": int(state["credit_after_replay"]),
                "duplicate_no_additional_credit": bool(
                    state["duplicate_created_no_additional_credit"]
                ),
                "confirmation_satisfied": bool(
                    report["confirmation_rule"]["satisfied"]
                ),
                "total_transaction_cost_wei": sum(
                    int(row["transaction_cost_wei"]) for row in transactions
                ),
            }
        )
        for row in transactions:
            transaction_rows.append({"run_id": run_id, **row})
        for row in controls:
            control_rows.append({"run_id": run_id, **row})
    unexpected_runs = (
        sorted(run_ids - registered_ids) if registered_ids is not None else []
    )
    missing_runs = (
        sorted(registered_ids - run_ids) if registered_ids is not None else []
    )
    if unexpected_runs:
        raise ValueError(
            "input contains runs absent from locked plan: "
            + ", ".join(unexpected_runs)
        )
    if missing_runs and not allow_partial:
        raise ValueError(
            f"confirmatory campaign incomplete: {len(run_ids)}/{planned_runs} runs; "
            "use --allow-partial only for progress inspection"
        )
    if len(deployment_hashes) != 1 or len(runtime_hashes) != 1:
        raise AssertionError("Sepolia runs do not share one deployment/runtime code set")

    runs = pd.DataFrame(run_rows).sort_values("run_id")
    transactions = pd.DataFrame(transaction_rows).sort_values(["run_id", "label"])
    controls = pd.DataFrame(control_rows).sort_values(["run_id", "case"])
    output.mkdir(parents=True, exist_ok=True)
    runs.to_csv(output / "sepolia_runs.csv", index=False, lineterminator="\n")
    transactions.to_csv(
        output / "sepolia_transactions.csv", index=False, lineterminator="\n"
    )
    controls.to_csv(
        output / "sepolia_negative_controls.csv", index=False, lineterminator="\n"
    )
    quantiles: list[dict[str, Any]] = []
    for label, group in transactions.groupby("label"):
        for metric in ("receipt_wait_ms", "gas_used", "effective_gas_price_wei"):
            values = pd.to_numeric(group[metric], errors="coerce").dropna()
            if values.empty:
                continue
            quantiles.append(
                {
                    "label": label,
                    "metric": metric,
                    "n": len(values),
                    "p50": float(values.quantile(0.50)),
                    "p95": float(values.quantile(0.95)),
                    "p99": float(values.quantile(0.99)),
                }
            )
    pd.DataFrame(quantiles).to_csv(
        output / "sepolia_transaction_quantiles.csv",
        index=False,
        lineterminator="\n",
    )
    invariant_passed = bool(
        runs["duplicate_no_additional_credit"].all()
        and runs["confirmation_satisfied"].all()
        and controls["passed"].all()
    )
    design_complete = registered_ids is not None and not missing_runs
    if registered_ids is None:
        status = "validated_unregistered_aggregate" if invariant_passed else "failed"
    elif design_complete:
        status = "validated_confirmatory" if invariant_passed else "failed"
    else:
        status = "incomplete"
    summary = {
        "schema_version": 1,
        "status": status,
        "registered_design": registered_ids is not None,
        "plan_sha256": plan_hash,
        "planned_runs": planned_runs,
        "observed_runs": len(run_ids),
        "missing_run_ids": missing_runs,
        "unexpected_run_ids": unexpected_runs,
        "runs": len(runs),
        "transactions": len(transactions),
        "negative_controls": len(controls),
        "negative_controls_passed": int(controls["passed"].sum()),
        "all_duplicate_replays_no_additional_credit": bool(
            runs["duplicate_no_additional_credit"].all()
        ),
        "all_confirmation_rules_satisfied": bool(
            runs["confirmation_satisfied"].all()
        ),
        "single_deployment_manifest": len(deployment_hashes) == 1,
        "single_runtime_code_set": len(runtime_hashes) == 1,
        "total_transaction_cost_wei": int(runs["total_transaction_cost_wei"].sum()),
        "claim_scope": (
            "representative public-EVM parity; "
            "local held-out trace matrix remains primary"
        ),
    }
    summary_path = output / "sepolia_campaign_summary.json"
    _write_json(summary_path, summary)
    if not invariant_passed:
        raise AssertionError(f"Sepolia aggregate invariants failed: {summary}")
    return summary_path
