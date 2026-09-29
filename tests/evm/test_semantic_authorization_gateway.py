from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from eth_tester.exceptions import TransactionFailed
from web3 import Web3
from web3.providers.eth_tester import EthereumTesterProvider

from aidmesh.crypto import evm_wire
from aidmesh.experiments.solidity import solc_command

ROOT = Path(__file__).resolve().parents[2]
GATEWAY = ROOT / "contracts" / "src" / "AidMeshSemanticAuthorizationGateway.sol"
SETTLEMENT = ROOT / "contracts" / "src" / "AidMeshSettlement.sol"
VERIFIER = ROOT / "contracts" / "test" / "AlwaysTrueGroth16Verifier.sol"


def _field(label: str) -> int:
    return int.from_bytes(Web3.keccak(text=label), "big")


def _compile() -> dict[str, tuple[list[dict[str, Any]], str]]:
    completed = subprocess.run(
        [
            solc_command(ROOT),
            "--evm-version",
            "prague",
            "--via-ir",
            "--optimize",
            "--combined-json",
            "abi,bin",
            str(GATEWAY),
            str(SETTLEMENT),
            str(VERIFIER),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    artifacts = json.loads(completed.stdout)["contracts"]
    result: dict[str, tuple[list[dict[str, Any]], str]] = {}
    for identifier, artifact in artifacts.items():
        name = identifier.rsplit(":", maxsplit=1)[-1]
        if not artifact["bin"]:
            continue
        abi = artifact["abi"] if isinstance(artifact["abi"], list) else json.loads(artifact["abi"])
        result[name] = (abi, artifact["bin"])
    return result


def _deploy(web3: Web3, artifact: tuple[list[dict[str, Any]], str], sender: str, *args):
    abi, bytecode = artifact
    transaction = (
        web3.eth.contract(abi=abi, bytecode=bytecode).constructor(*args).transact({"from": sender})
    )
    address = web3.eth.wait_for_transaction_receipt(transaction).contractAddress
    return web3.eth.contract(address=address, abi=abi)


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

    def sign(self, address: str, digest: bytes) -> bytes:
        signed = Account.sign_message(encode_defunct(primitive=digest), self.keys[address])
        return bytes(signed.signature)

    @property
    def proof(self):
        return [0, 0], [[0, 0], [0, 0]], [0, 0]


@pytest.fixture(scope="module")
def artifacts():
    return _compile()


@pytest.fixture()
def system(artifacts) -> _System:
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
    settlement = _deploy(web3, artifacts["AidMeshSettlement"], owner, gateway.address)
    gateway.functions.setSettlement(settlement.address).transact({"from": owner})

    inputs = [0] * 17
    inputs[0] = _field("root")
    inputs[1] = _field("schema")
    inputs[2] = _field("issuer")
    inputs[4] = 1
    inputs[5] = _field("campaign")
    inputs[6] = _field("expiry")
    inputs[7] = _field("nullifier")
    inputs[11] = web3.eth.chain_id
    inputs[12] = int(gateway.address, 16)
    inputs[13] = int(settlement.address, 16)
    inputs[14] = _field("header")
    inputs[15] = _field("policy")
    epoch = 7
    inputs[16] = gateway.functions.semanticCommitteeHash(epoch, 2, 3, list(witnesses)).call()
    domain = gateway.functions.proofDomain(inputs).call()
    settlement.functions.registerCampaign(
        domain,
        inputs[14],
        inputs[15],
        inputs[16],
        [site_a, site_b],
    ).transact({"from": owner})
    settlement.functions.fundCampaign(domain, 20).transact({"from": owner})
    gateway.functions.registerAuthorizationDomain(
        domain, epoch, 2, inputs[16], 3, list(witnesses)
    ).transact({"from": owner})
    return _System(
        web3,
        owner,
        witnesses,
        site_a,
        site_b,
        attacker,
        keys,
        verifier,
        gateway,
        settlement,
        inputs,
        domain,
        epoch,
    )


def _quorum_receipt(system: _System, *, receipt_id: bytes, site: str, inputs=None):
    statement_inputs = inputs or system.inputs
    release_intent_id = Web3.keccak(receipt_id + b"/release-intent/v1")
    receipt_skeleton_hash = evm_wire.receipt_skeleton(
        statement_inputs, release_intent_id, site, system.epoch
    )
    authorization_hash = evm_wire.authorization_intent(
        statement_inputs,
        release_intent_id,
        receipt_skeleton_hash,
        site,
        system.epoch,
    )
    statement_hash = evm_wire.proof_statement_hash(statement_inputs)
    receipt_payload = evm_wire.receipt_payload(
        system.domain,
        statement_hash,
        receipt_id,
        site,
        release_intent_id,
        receipt_skeleton_hash,
        authorization_hash,
    )
    receipt = (
        receipt_id,
        site,
        release_intent_id,
        receipt_skeleton_hash,
        authorization_hash,
        system.sign(site, receipt_payload),
    )
    signatures = [system.sign(witness, authorization_hash) for witness in system.witnesses[:2]]
    return receipt, signatures


@pytest.mark.parametrize("accept", [True, False])
def test_recovery_terminal_settlement_and_negative_controls(system, accept):
    s = system
    g = s.gateway.functions
    g.enableRecovery(s.domain, 0).transact({"from": s.owner})
    right = Web3.keccak(text="recovery-right")
    g.issueRight(s.domain, right, s.site_a, 1).transact({"from": s.owner})
    inputs = list(s.inputs)
    inputs[8] = 1  # semantic tests use the documented always-true proof fixture
    cert = [right, Web3.keccak(text="transfer"), s.site_a, s.site_b, s.epoch, 1, 2, accept, b"", []]
    cert[8] = s.sign(s.site_a, g.recoveryTransferHash(s.domain, cert).call())
    commit = g.recoveryCommitHash(s.domain, cert).call()
    cert[9] = [s.sign(w, commit) for w in s.witnesses[:2]]
    site = s.site_b if accept else s.site_a
    receipt_id = Web3.keccak(text="recovery-receipt")
    release = Web3.keccak(text="recovery-release")
    skeleton = g.receiptSkeleton(inputs, release, site, s.epoch).call()
    receipt = [receipt_id, site, release, skeleton, bytes(32), b""]
    receipt[4] = g.recoveryAuthorizationHash(inputs, receipt, cert).call()
    payload = g.receiptPayload(s.domain, g.proofStatementHash(inputs).call(), *receipt[:5]).call()
    receipt[5] = s.sign(site, payload)

    def send(certificate=cert, bound_receipt=receipt, public=inputs):
        return g.verifyRecoveryAndSettle(*s.proof, public, bound_receipt, certificate)

    mutations = {
        0: Web3.keccak(text="other-right"),
        1: Web3.keccak(text="other-transfer"),
        2: s.attacker,
        3: s.attacker,
        4: s.epoch + 1,
        5: 2,
        6: 3,
        7: not accept,
        8: s.sign(s.attacker, commit),
        9: cert[9][:1],
    }
    for index, value in mutations.items():
        bad = list(cert)
        bad[index] = value
        with pytest.raises(TransactionFailed):
            send(certificate=bad).call({"from": s.attacker})
    bad = list(cert)
    bad[9] = [cert[9][0], cert[9][0]]
    with pytest.raises(TransactionFailed):
        send(certificate=bad).call({"from": s.attacker})
    assert not g.rights(right).call()[-1]
    assert send().call({"from": s.attacker})
    send().transact({"from": s.attacker})
    assert g.rights(right).call()[-1]
    assert s.settlement.functions.credits(s.domain, site).call() == 1
    with pytest.raises(TransactionFailed):
        send().call({"from": s.attacker})
    assert s.settlement.functions.credits(s.domain, site).call() == 1


def test_recovery_rejects_unsafe_quorum_configuration(system):
    with pytest.raises(TransactionFailed, match="UNSAFE_RECOVERY_QUORUM"):
        system.gateway.functions.enableRecovery(system.domain, 1).call({"from": system.owner})


def test_dual_attestation_requires_durable_commit_and_closed_receipt(system, tmp_path):
    from copy import deepcopy

    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from aidmesh.protocol.recovered_release import RecoveredReleaseSite
    from aidmesh.protocol.recovery_evm_bridge import attest_closed_receipt, attest_terminal
    from aidmesh.protocol.release import ReleaseSlotKey
    from aidmesh.protocol.terminal_recovery import DurableTerminalReplica

    s = system
    members = tuple(sorted(s.witnesses))
    sites = (s.site_a, s.site_b)
    keys = {m: Ed25519PrivateKey.generate() for m in (*members, *sites)}

    def sign(m, d):
        return keys[m].sign(bytes.fromhex(d)).hex()

    def verify(m, d, sig):
        try:
            keys[m].public_key().verify(bytes.fromhex(sig), bytes.fromhex(d))
            return True
        except (InvalidSignature, ValueError):
            return False

    public = list(s.inputs)
    public[1] = s.epoch
    public[8] = 1
    domain = evm_wire.proof_domain(public)
    context = (s.web3.eth.chain_id, s.gateway.address, s.settlement.address)

    def site(name):
        return RecoveredReleaseSite(
            tmp_path / f"{name}.db",
            site=name,
            domain=domain.hex(),
            epoch=s.epoch,
            initial=int(name == s.site_a),
            sites=sites,
            witnesses=members,
            faults=0,
            sign=sign,
            verify=verify,
        )

    a, b = site(s.site_a), site(s.site_b)
    payload = a.begin(Web3.keccak(text="bridge-right").hex(), s.site_b, 1)
    transfer = json.loads(payload)
    rules = b.recovery_rules(payload)
    replicas = [DurableTerminalReplica(tmp_path / f"{m}.db", rules, m, sign) for m in members]
    reports = [r.change_view(1) for r in replicas]
    proposal = replicas[1].propose(1, "accept", reports)
    prepared = [r.prepare(proposal) for r in replicas]
    commits = [r.commit(prepared) for r in replicas[:2]]
    with pytest.raises(ValueError, match="durable local commit"):
        attest_terminal(replicas[2], transfer, commits, context=context, sign_evm=s.sign)
    replicas[0].close()
    replicas[0] = DurableTerminalReplica(tmp_path / f"{members[0]}.db", rules, members[0], sign)
    cert, share = attest_terminal(replicas[0], transfer, commits, context=context, sign_evm=s.sign)
    assert s.sign(members[0], s.gateway.functions.recoveryCommitHash(domain, cert).call()) == share
    a.finalize(payload, commits)
    b.finalize(payload, commits)
    voucher = dict(zip(("a", "b", "c"), s.proof, strict=True))
    voucher["public"] = public
    # Explicit proof fixture: bridge tests authentication/binding, not P1 soundness.
    receipt = b.release(
        ReleaseSlotKey(domain.hex(), s.epoch, str(public[7]), s.site_b),
        value=1,
        mode="fallback",
        authorization=voucher,
        verify_authorization=lambda *_: True,
        actuator=lambda *_: "synthetic-ack",
    )["receipt"]
    bound = attest_closed_receipt(
        receipt, voucher, cert, release_site=b, context=context, verify_ed=verify, sign_evm=s.sign
    )
    assert bound[4] == s.gateway.functions.recoveryAuthorizationHash(public, bound, cert).call()
    bad = deepcopy(receipt)
    bad["body"]["acknowledgment"] = "altered"
    with pytest.raises(ValueError, match="closed receipt"):
        attest_closed_receipt(
            bad, voucher, cert, release_site=b, context=context, verify_ed=verify, sign_evm=s.sign
        )
    a.close()
    b.close()
    for r in replicas:
        r.close()


@pytest.mark.evm
def test_quorum_semantics_are_verified_without_trusting_the_relayer(system: _System) -> None:
    receipt_id = Web3.keccak(text="semantic-quorum-receipt")
    receipt, signatures = _quorum_receipt(system, receipt_id=receipt_id, site=system.site_a)
    a, b, c = system.proof
    system.gateway.functions.verifyQuorumAndSettle(
        a, b, c, system.inputs, receipt, system.epoch, signatures
    ).transact({"from": system.attacker})

    nullifier = system.inputs[7].to_bytes(32, "big")
    assert system.settlement.functions.winningReceipt(system.domain, nullifier).call() == receipt_id
    assert system.settlement.functions.credits(system.domain, system.site_a).call() == 1


@pytest.mark.evm
def test_independent_wire_profile_covers_every_public_input(system: _System) -> None:
    original = list(system.inputs)
    for index in range(17):
        values = original.copy()
        values[index] += 1
        assert evm_wire.proof_domain(values) == system.gateway.functions.proofDomain(values).call()
        assert evm_wire.proof_statement_hash(values) == (
            system.gateway.functions.proofStatementHash(values).call()
        )
        assert evm_wire.proof_statement_hash(values) != evm_wire.proof_statement_hash(original)


@pytest.mark.evm
def test_quorum_rejects_insufficient_duplicate_foreign_stale_and_mutated_evidence(
    system: _System,
) -> None:
    with pytest.raises(TransactionFailed):
        system.gateway.functions.registerAuthorizationDomain(
            Web3.keccak(text="misbound-domain"),
            system.epoch,
            2,
            _field("wrong-committee"),
            3,
            list(system.witnesses),
        ).transact({"from": system.owner})
    receipt_id = Web3.keccak(text="negative-quorum-receipt")
    receipt, signatures = _quorum_receipt(system, receipt_id=receipt_id, site=system.site_a)
    a, b, c = system.proof
    with pytest.raises(TransactionFailed):
        system.gateway.functions.verifyQuorumAndSettle(
            a, b, c, system.inputs, receipt, system.epoch, signatures[:1]
        ).transact({"from": system.attacker})
    with pytest.raises(TransactionFailed):
        system.gateway.functions.verifyQuorumAndSettle(
            a, b, c, system.inputs, receipt, system.epoch, [signatures[0], signatures[0]]
        ).transact({"from": system.attacker})
    foreign = system.sign(system.attacker, receipt[4])
    with pytest.raises(TransactionFailed):
        system.gateway.functions.verifyQuorumAndSettle(
            a, b, c, system.inputs, receipt, system.epoch, [signatures[0], foreign]
        ).transact({"from": system.attacker})
    with pytest.raises(TransactionFailed):
        system.gateway.functions.verifyQuorumAndSettle(
            a, b, c, system.inputs, receipt, system.epoch + 1, signatures
        ).transact({"from": system.attacker})
    mutated = list(system.inputs)
    mutated[7] = _field("substituted-nullifier")
    with pytest.raises(TransactionFailed):
        system.gateway.functions.verifyQuorumAndSettle(
            a, b, c, mutated, receipt, system.epoch, signatures
        ).transact({"from": system.attacker})


@pytest.mark.evm
def test_transfer_chain_is_threshold_authorized_cap_limited_and_single_use(
    system: _System,
) -> None:
    right_id = Web3.keccak(text="right-1")
    system.gateway.functions.issueRight(system.domain, right_id, system.site_a, 1).transact(
        {"from": system.owner}
    )
    transition_hash = evm_wire.right_transition_statement(
        system.domain,
        right_id,
        system.site_a,
        system.site_b,
        system.epoch,
        1,
    )
    transition = (
        system.site_a,
        system.site_b,
        system.epoch,
        1,
        [system.sign(witness, transition_hash) for witness in system.witnesses[:2]],
    )
    receipt_id = Web3.keccak(text="right-receipt")
    release_intent_id = Web3.keccak(text="right-release-intent")
    receipt_skeleton_hash = evm_wire.receipt_skeleton(
        system.inputs,
        release_intent_id,
        system.site_b,
        system.epoch,
    )
    authorization_hash = evm_wire.right_authorization_hash(
        system.inputs,
        release_intent_id,
        receipt_skeleton_hash,
        system.site_b,
        right_id,
        system.epoch,
        [transition],
    )
    statement_hash = evm_wire.proof_statement_hash(system.inputs)
    receipt_payload = evm_wire.receipt_payload(
        system.domain,
        statement_hash,
        receipt_id,
        system.site_b,
        release_intent_id,
        receipt_skeleton_hash,
        authorization_hash,
    )
    receipt = (
        receipt_id,
        system.site_b,
        release_intent_id,
        receipt_skeleton_hash,
        authorization_hash,
        system.sign(system.site_b, receipt_payload),
    )
    a, b, c = system.proof
    system.gateway.functions.verifyRightAndSettle(
        a,
        b,
        c,
        system.inputs,
        receipt,
        right_id,
        system.epoch,
        [transition],
    ).transact({"from": system.attacker})
    right = system.gateway.functions.rights(right_id).call()
    assert right[1] == system.site_b
    assert right[4] == 1
    assert right[5] is True
    with pytest.raises(TransactionFailed):
        system.gateway.functions.verifyRightAndSettle(
            a,
            b,
            c,
            system.inputs,
            receipt,
            right_id,
            system.epoch,
            [],
        ).transact({"from": system.attacker})

    system.gateway.functions.issueRight(
        system.domain, Web3.keccak(text="right-2"), system.site_a, 2
    ).transact({"from": system.owner})
    with pytest.raises(TransactionFailed):
        system.gateway.functions.issueRight(
            system.domain, Web3.keccak(text="right-over-cap"), system.site_a, 1
        ).transact({"from": system.owner})
