"""Normative ABI/Keccak V2 wire digests, computed without a contract call.

The JSON/SHA-256 protocol model remains a distinct reference profile. These
functions implement the EVM gateway profile; signatures cannot cross profiles.
"""

from __future__ import annotations

from eth_abi import encode
from eth_utils import keccak

PROTOCOL = keccak(text="AIDMESH_PROOF_GATED_ACCOUNTING_V2")
SEMANTIC = keccak(text="AIDMESH_SEMANTIC_AUTHORIZATION_V2")
SKELETON = keccak(text="AIDMESH_RECEIPT_SKELETON_V1")


def _inputs(values):
    values = tuple(values)
    if len(values) != 17 or any(type(v) is not int or not 0 <= v < 2**256 for v in values):
        raise ValueError("expected 17 uint256 public inputs")
    return values


def proof_domain(values):
    values = _inputs(values)
    indices = (0, 1, 2, 5, 6, 11, 12, 13, 14, 15, 16)
    return keccak(
        encode(["bytes32"] + ["uint256"] * len(indices), [PROTOCOL, *(values[i] for i in indices)])
    )


def proof_statement_hash(values):
    return keccak(encode(["bytes32", "uint256[17]"], [PROTOCOL, _inputs(values)]))


def release_intent_id(values, site, mode=0):
    return keccak(
        encode(
            ["bytes32", "bytes32", "bytes32", "address", "uint8"],
            [
                keccak(text="AIDMESH_RELEASE_ID_V2"),
                proof_domain(values),
                proof_statement_hash(values),
                site,
                mode,
            ],
        )
    )


def receipt_skeleton(values, release_id, site, epoch):
    values = _inputs(values)
    return keccak(
        encode(
            ["bytes32"] * 6 + ["address", "uint256", "uint64"],
            [
                SEMANTIC,
                SKELETON,
                proof_domain(values),
                proof_statement_hash(values),
                values[7].to_bytes(32, "big"),
                bytes(release_id),
                site,
                values[4],
                epoch,
            ],
        )
    )


def authorization_intent(values, release_id, skeleton_hash, site, epoch):
    values = _inputs(values)
    return keccak(
        encode(
            ["bytes32", "uint8"] + ["bytes32"] * 5 + ["address", "uint256", "uint64"],
            [
                SEMANTIC,
                0,
                proof_domain(values),
                proof_statement_hash(values),
                values[7].to_bytes(32, "big"),
                bytes(release_id),
                bytes(skeleton_hash),
                site,
                values[4],
                epoch,
            ],
        )
    )


def receipt_id(release_id, released_at, evidence_hash):
    """Site-side commitment to final evidence; verify this relation before relaying."""
    return keccak(
        encode(
            ["bytes32", "bytes32", "uint256", "bytes32"],
            [
                keccak(text="AIDMESH_FINAL_RECEIPT_V2"),
                bytes(release_id),
                released_at,
                bytes(evidence_hash),
            ],
        )
    )


def receipt_payload(
    domain, statement_hash, receipt_id, site, release_id, skeleton_hash, authorization_hash
):
    return keccak(
        encode(
            ["bytes32"] * 4 + ["address"] + ["bytes32"] * 3,
            [
                SEMANTIC,
                bytes(domain),
                bytes(statement_hash),
                bytes(receipt_id),
                site,
                bytes(release_id),
                bytes(skeleton_hash),
                bytes(authorization_hash),
            ],
        )
    )


def right_transition_statement(domain, right_id, source, destination, epoch, nonce):
    return keccak(
        encode(
            ["bytes32"] * 3 + ["address"] * 2 + ["uint64"] * 2,
            [
                keccak(text="AIDMESH_RIGHT_TRANSITION_V1"),
                bytes(domain),
                bytes(right_id),
                source,
                destination,
                epoch,
                nonce,
            ],
        )
    )


def right_authorization_hash(values, release_id, skeleton_hash, site, right_id, epoch, transitions):
    values = _inputs(values)
    result = keccak(
        encode(
            ["bytes32", "uint8"] + ["bytes32"] * 5 + ["address", "uint256", "bytes32", "uint64"],
            [
                SEMANTIC,
                1,
                proof_domain(values),
                proof_statement_hash(values),
                values[7].to_bytes(32, "big"),
                bytes(release_id),
                bytes(skeleton_hash),
                site,
                values[4],
                bytes(right_id),
                epoch,
            ],
        )
    )
    for source, destination, transition_epoch, nonce, *_ in transitions:
        result = keccak(
            encode(
                ["bytes32", "address", "address", "uint64", "uint64"],
                [result, source, destination, transition_epoch, nonce],
            )
        )
    return result
