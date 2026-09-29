// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.36;

interface ISemanticHarnessVerifier {
    function verifyProof(
        uint256[2] calldata a,
        uint256[2][2] calldata b,
        uint256[2] calldata c,
        uint256[17] calldata input
    ) external view returns (bool);
}

interface ISemanticHarnessSettlement {
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

/// @notice Explicitly insecure digest-only negative control. Never deploy.
contract VulnerableDigestOnlyGateway {
    bytes32 public constant PROTOCOL_DOMAIN =
        keccak256("AIDMESH_PROOF_GATED_ACCOUNTING_V2");

    ISemanticHarnessVerifier public immutable verifier;
    ISemanticHarnessSettlement public settlement;
    address public immutable owner;

    constructor(address verifierAddress) {
        verifier = ISemanticHarnessVerifier(verifierAddress);
        owner = msg.sender;
    }

    function setSettlement(address settlementAddress) external {
        require(msg.sender == owner && address(settlement) == address(0));
        settlement = ISemanticHarnessSettlement(settlementAddress);
    }

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

    function settleWithoutSemanticAuthorization(
        uint256[2] calldata a,
        uint256[2][2] calldata b,
        uint256[2] calldata c,
        uint256[17] calldata input,
        bytes32 receiptId,
        address site,
        bytes32 attackerChosenAuthorizationHash
    ) external returns (bool) {
        require(input[11] == block.chainid);
        require(input[12] == uint256(uint160(address(this))));
        require(input[13] == uint256(uint160(address(settlement))));
        require(verifier.verifyProof(a, b, c, input));
        bytes32 domain = proofDomain(input);
        require(settlement.contextMatches(domain, input[14], input[15], input[16]));
        return
            settlement.settleVerified(
                domain,
                bytes32(input[7]),
                receiptId,
                site,
                input[4],
                attackerChosenAuthorizationHash
            );
    }
}

contract ReentrantSemanticSettlementProbe {
    address public gateway;
    bytes public reentryPayload;
    bool public attempted;
    bool public reentrySucceeded;

    constructor(address gatewayAddress) {
        gateway = gatewayAddress;
    }

    function setReentryPayload(bytes calldata payload) external {
        reentryPayload = payload;
    }

    function contextMatches(
        bytes32,
        uint256,
        uint256,
        uint256
    ) external pure returns (bool) {
        return true;
    }

    function settleVerified(
        bytes32,
        bytes32,
        bytes32,
        address,
        uint256,
        bytes32
    ) external returns (bool) {
        attempted = true;
        (reentrySucceeded, ) = gateway.call(reentryPayload);
        return true;
    }
}

contract RevertingSemanticSettlementProbe {
    function contextMatches(
        bytes32,
        uint256,
        uint256,
        uint256
    ) external pure returns (bool) {
        return true;
    }

    function settleVerified(
        bytes32,
        bytes32,
        bytes32,
        address,
        uint256,
        bytes32
    ) external pure returns (bool) {
        revert("INJECTED_SETTLEMENT_FAILURE");
    }
}
