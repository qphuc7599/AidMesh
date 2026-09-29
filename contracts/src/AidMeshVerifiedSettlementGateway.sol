// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.36;

interface IAidMeshGroth16Verifier {
    function verifyProof(
        uint256[2] calldata a,
        uint256[2][2] calldata b,
        uint256[2] calldata c,
        uint256[17] calldata input
    ) external view returns (bool);
}

interface IAidMeshSettlement {
    function settleVerified(
        bytes32 domain,
        bytes32 nullifier,
        bytes32 receiptId,
        address site,
        uint256 value,
        bytes32 authorizationCommitment
    ) external returns (bool);

    function contextMatches(
        bytes32 domain,
        uint256 headerHash,
        uint256 policyHash,
        uint256 committeeHash
    ) external view returns (bool);
}

/// @title AidMeshVerifiedSettlementGateway
/// @notice Binds an exact P1 proof and path-authorization commitment to a
///         site-signed receipt and the resulting settlement record.
/// @dev The EVM commits to the exact quorum-certificate or fallback-debit evidence
///      hash but does not validate the off-chain evidence represented by that hash.
contract AidMeshVerifiedSettlementGateway {
    struct BoundReceipt {
        bytes32 receiptId;
        address site;
        bytes32 authorizationEvidenceHash;
        bytes siteSignature;
    }

    bytes32 public constant PROTOCOL_DOMAIN =
        keccak256("AIDMESH_PROOF_GATED_ACCOUNTING_V2");
    bytes32 public constant AUTHORIZATION_DOMAIN =
        keccak256("AIDMESH_PATH_AUTHORIZATION_COMMITMENT_V1");
    IAidMeshGroth16Verifier public immutable verifier;
    IAidMeshSettlement public settlement;
    address public immutable owner;
    bool private entered;
    uint256 private constant SECP256K1N_DIV_2 =
        0x7fffffffffffffffffffffffffffffff5d576e7357a4501ddfe92f46681b20a0;

    modifier nonReentrant() {
        require(!entered, "REENTRANT_CALL");
        entered = true;
        _;
        entered = false;
    }

    event SettlementBound(
        bytes32 indexed domain,
        bytes32 indexed nullifier,
        bytes32 indexed receiptId,
        address site,
        uint256 value,
        bytes32 statementHash,
        bytes32 authorizationCommitment,
        bool settled
    );

    constructor(address verifierAddress) {
        require(verifierAddress != address(0), "ZERO_VERIFIER");
        owner = msg.sender;
        verifier = IAidMeshGroth16Verifier(verifierAddress);
    }

    function setSettlement(address settlementAddress) external {
        require(msg.sender == owner, "ONLY_OWNER");
        require(address(settlement) == address(0), "SETTLEMENT_ALREADY_SET");
        require(settlementAddress != address(0), "ZERO_SETTLEMENT");
        settlement = IAidMeshSettlement(settlementAddress);
    }

    /// @notice Safety domain including protocol, deployment, header and policy anchors.
    function proofDomain(uint256[17] calldata input) public pure returns (bytes32) {
        return
            keccak256(
                abi.encode(
                    PROTOCOL_DOMAIN,
                    input[0],
                    input[1],
                    input[2],
                    input[5],
                    input[6],
                    input[11],
                    input[12],
                    input[13],
                    input[14],
                    input[15],
                    input[16]
                )
            );
    }

    function proofStatementHash(
        uint256[17] calldata input
    ) public pure returns (bytes32) {
        return keccak256(abi.encode(PROTOCOL_DOMAIN, input));
    }

    function receiptPayload(
        bytes32 domain,
        bytes32 statementHash,
        bytes32 receiptId,
        address site,
        bytes32 boundAuthorization
    ) public pure returns (bytes32) {
        return
            keccak256(
                abi.encode(
                    domain,
                    statementHash,
                    receiptId,
                    site,
                    boundAuthorization
                )
            );
    }

    function authorizationCommitment(
        uint256[17] calldata input,
        bytes32 receiptId,
        address site,
        bytes32 evidenceHash
    ) public pure returns (bytes32) {
        require(evidenceHash != bytes32(0), "ZERO_AUTHORIZATION_EVIDENCE");
        return
            keccak256(
                abi.encode(
                    AUTHORIZATION_DOMAIN,
                    proofDomain(input),
                    proofStatementHash(input),
                    bytes32(input[7]),
                    receiptId,
                    site,
                    input[4],
                    input[8],
                    input[9],
                    input[10],
                    evidenceHash
                )
            );
    }

    function _recover(bytes32 payload, bytes calldata signature) internal pure returns (address) {
        require(signature.length == 65, "BAD_SIGNATURE_LENGTH");
        bytes32 r;
        bytes32 s;
        uint8 v;
        assembly {
            r := calldataload(signature.offset)
            s := calldataload(add(signature.offset, 32))
            v := byte(0, calldataload(add(signature.offset, 64)))
        }
        if (v < 27) {
            v += 27;
        }
        require(v == 27 || v == 28, "BAD_SIGNATURE_V");
        require(uint256(s) <= SECP256K1N_DIV_2, "NON_CANONICAL_SIGNATURE_S");
        bytes32 digest = keccak256(
            abi.encodePacked("\x19Ethereum Signed Message:\n32", payload)
        );
        address signer = ecrecover(digest, v, r, s);
        require(signer != address(0), "BAD_SIGNATURE");
        return signer;
    }

    function verifyAndSettle(
        uint256[2] calldata a,
        uint256[2][2] calldata b,
        uint256[2] calldata c,
        uint256[17] calldata input,
        BoundReceipt calldata receipt
    ) external nonReentrant returns (bool settled) {
        require(address(settlement) != address(0), "SETTLEMENT_NOT_SET");
        require(receipt.receiptId != bytes32(0), "ZERO_RECEIPT");
        require(input[11] == block.chainid, "WRONG_CHAIN");
        require(input[12] == uint256(uint160(address(this))), "WRONG_GATEWAY");
        require(
            input[13] == uint256(uint160(address(settlement))),
            "WRONG_SETTLEMENT"
        );
        require(verifier.verifyProof(a, b, c, input), "INVALID_PROOF");

        bytes32 domain = proofDomain(input);
        require(
            settlement.contextMatches(domain, input[14], input[15], input[16]),
            "UNREGISTERED_CONTEXT"
        );
        bytes32 statementHash = proofStatementHash(input);
        bytes32 nullifier = bytes32(input[7]);
        uint256 value = input[4];
        require(nullifier != bytes32(0) && value > 0, "BAD_PUBLIC_INPUT");
        bytes32 boundAuthorization = authorizationCommitment(
            input,
            receipt.receiptId,
            receipt.site,
            receipt.authorizationEvidenceHash
        );
        bytes32 payload = receiptPayload(
            domain,
            statementHash,
            receipt.receiptId,
            receipt.site,
            boundAuthorization
        );
        require(
            _recover(payload, receipt.siteSignature) == receipt.site,
            "WRONG_SITE_SIGNATURE"
        );

        settled = settlement.settleVerified(
            domain,
            nullifier,
            receipt.receiptId,
            receipt.site,
            value,
            boundAuthorization
        );
        emit SettlementBound(
            domain,
            nullifier,
            receipt.receiptId,
            receipt.site,
            value,
            statementHash,
            boundAuthorization,
            settled
        );
    }
}
