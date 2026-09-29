"""Dual attestation after durable recovery, not signature-format conversion.

The same configured participant owns Ed25519 and EVM keys. Export is permitted
only for an authenticated terminal certificate matching its persisted commit.
This one-shot bridge maps transfer IDs to freshly issued, nonce-zero EVM rights.
"""

import json

from eth_abi import encode
from eth_utils import keccak

from aidmesh.crypto import evm_wire
from aidmesh.protocol.terminal_recovery import canonical, digest


def transfer_hash(domain, certificate, context):
    right, transfer, source, destination, epoch, nonce = certificate[:6]
    return keccak(
        encode(
            [
                "bytes32",
                "uint256",
                "address",
                "address",
                "bytes32",
                "bytes32",
                "bytes32",
                "address",
                "address",
                "uint64",
                "uint64",
            ],
            [
                keccak(text="AIDMESH_RECOVERY_TRANSFER_EVM_V1"),
                *context,
                domain,
                right,
                transfer,
                source,
                destination,
                epoch,
                nonce,
            ],
        )
    )


def commit_hash(domain, certificate, context):
    return keccak(
        encode(
            ["bytes32", "bytes32", "uint64", "bool"],
            [
                keccak(text="AIDMESH_RECOVERY_COMMIT_EVM_V1"),
                transfer_hash(domain, certificate, context),
                certificate[6],
                certificate[7],
            ],
        )
    )


def authorization_hash(public, receipt, certificate, context):
    return keccak(
        encode(
            ["bytes32", "bytes32", "bytes32", "bytes32", "bytes32", "address"],
            [
                keccak(text="AIDMESH_RECOVERY_RELEASE_EVM_V1"),
                commit_hash(evm_wire.proof_domain(public), certificate, context),
                evm_wire.proof_statement_hash(public),
                receipt[2],
                receipt[3],
                receipt[1],
            ],
        )
    )


def attest_terminal(replica, transfer, commits, *, context, sign_evm):
    body = transfer["body"]
    if (
        canonical(body) != canonical(replica.rules.context)
        or transfer["signer"] != body["source"]
        or not replica.rules.verify(transfer["signer"], digest(body), transfer["signature"])
    ):
        raise ValueError("transfer binding/authentication")
    view, decision = replica.rules.certificate(commits, "commit")
    row = replica.db.execute("SELECT body FROM signed WHERE key=?", (f"commit:{view}",)).fetchone()
    if row is None or json.loads(row[0]) != replica.rules.message("commit", view, decision):
        raise ValueError("no matching durable local commit")
    replica.decide(commits)
    right = bytes.fromhex(body["transfer"])
    domain = bytes.fromhex(body["domain"])
    if len(right) != 32 or len(domain) != 32:
        raise ValueError("one-shot EVM right/domain must be bytes32")
    certificate = [
        right,
        bytes.fromhex(digest(body)),
        body["source"],
        body["destination"],
        body["epoch"],
        1,
        view,
        decision == "accept",
        b"",
        [],
    ]
    return certificate, sign_evm(replica.member, commit_hash(domain, certificate, context))


def attest_closed_receipt(
    receipt, voucher, certificate, *, release_site, context, verify_ed, sign_evm
):
    body = receipt["body"]
    statement = body["statement"]
    slot = statement["slot"]
    public = voucher["public"]
    recipient = certificate[3] if certificate[7] else certificate[2]
    persisted = release_site.db.execute(
        "SELECT status,receipt FROM release_claims WHERE slot=?", (body["release_id"],)
    ).fetchone()
    if (
        release_site.site != recipient
        or persisted is None
        or persisted[0] != "closed"
        or persisted[1] != canonical(receipt)
    ):
        raise ValueError("no matching durable closed receipt")
    domain = evm_wire.proof_domain(public)
    if (
        body["profile"] != "aidmesh/recovered-receipt/v1"
        or statement["profile"] != "aidmesh/recovered-release/v1"
        or body["authorization_digest"] != digest(voucher)
        or body["release_id"] != digest(dict(profile="aidmesh/release-slot/v1", slot=slot))
        or receipt["signer"] != recipient
        or slot["site_id"] != recipient
        or not verify_ed(recipient, digest(body), receipt["signature"])
        or not body["acknowledgment"]
        or statement["mode"] != "fallback"
        or slot["domain_id"] != domain.hex()
        or int(slot["nullifier"]) != public[7]
        or slot["epoch"] != public[1]
        or statement["value"] != public[4]
        or public[8] != 1
        or public[11:14] != [context[0], int(context[1], 16), int(context[2], 16)]
    ):
        raise ValueError("closed receipt/authorization binding")
    release = bytes.fromhex(body["release_id"])
    bound = [
        bytes.fromhex(digest(body)),
        recipient,
        release,
        evm_wire.receipt_skeleton(public, release, recipient, certificate[4]),
        bytes(32),
        b"",
    ]
    bound[4] = authorization_hash(public, bound, certificate, context)
    bound[5] = sign_evm(
        recipient,
        evm_wire.receipt_payload(domain, evm_wire.proof_statement_hash(public), *bound[:5]),
    )
    return bound
