// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.36;

contract AlwaysTrueGroth16Verifier {
    function verifyProof(
        uint256[2] calldata,
        uint256[2][2] calldata,
        uint256[2] calldata,
        uint256[17] calldata
    ) external pure returns (bool) {
        return true;
    }
}
