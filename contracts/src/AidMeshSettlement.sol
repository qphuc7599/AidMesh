// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.36;

/// @title AidMeshSettlement
/// @notice Accounting state for proof- and authorization-commitment-gated settlement.
/// @dev Quorum certificates and fallback-right debits remain outside this contract's
///      validation boundary, but their exact commitment is bound to the site signature,
///      winning receipt, and settlement event by the verifier gateway.
contract AidMeshSettlement {
    struct SettlementItem {
        bytes32 domain;
        bytes32 nullifier;
        bytes32 receiptId;
        address site;
        uint256 value;
        bytes32 authorizationCommitment;
    }

    struct Campaign {
        uint256 headerHash;
        uint256 policyHash;
        uint256 committeeHash;
        uint256 escrow;
        bool active;
    }

    address public immutable owner;
    address public immutable verifierAdapter;

    mapping(bytes32 => Campaign) public campaigns;
    mapping(bytes32 => mapping(address => bool)) public authorizedSites;
    mapping(bytes32 => mapping(bytes32 => bytes32)) public winningReceipt;
    mapping(bytes32 => mapping(bytes32 => bytes32)) public winningAuthorization;
    mapping(bytes32 => mapping(bytes32 => bytes32)) public batchAuthorizationRoots;
    mapping(bytes32 => mapping(address => uint256)) public credits;

    event CampaignRegistered(
        bytes32 indexed domain,
        uint256 headerHash,
        uint256 policyHash,
        uint256 committeeHash
    );
    event CampaignFunded(bytes32 indexed domain, uint256 amount, uint256 escrowAfter);
    event ReceiptSettled(
        bytes32 indexed domain,
        bytes32 indexed nullifier,
        bytes32 indexed receiptId,
        address site,
        uint256 value,
        bytes32 authorizationCommitment
    );
    event ReceiptConflict(
        bytes32 indexed domain,
        bytes32 indexed nullifier,
        bytes32 indexed losingReceiptId,
        bytes32 winningReceiptId
    );
    event BatchAuthorizationCommitted(bytes32 indexed domain,
        bytes32 indexed nullifierListHash, bytes32 authorizationListHash);

    modifier onlyOwner() {
        require(msg.sender == owner, "ONLY_OWNER");
        _;
    }

    modifier onlyVerifierAdapter() {
        require(msg.sender == verifierAdapter, "ONLY_ADAPTER");
        _;
    }

    constructor(address adapter) {
        require(adapter != address(0), "ZERO_ADAPTER");
        owner = msg.sender;
        verifierAdapter = adapter;
    }

    function registerCampaign(
        bytes32 domain,
        uint256 headerHash,
        uint256 policyHash,
        uint256 committeeHash,
        address[] calldata sites
    ) external onlyOwner {
        require(
            domain != bytes32(0) &&
                headerHash != 0 &&
                policyHash != 0 &&
                committeeHash != 0,
            "ZERO_ID"
        );
        require(!campaigns[domain].active, "DOMAIN_EXISTS");
        require(sites.length > 0, "NO_SITES");
        campaigns[domain] = Campaign(headerHash, policyHash, committeeHash, 0, true);
        for (uint256 i = 0; i < sites.length; i++) {
            require(sites[i] != address(0), "ZERO_SITE");
            require(!authorizedSites[domain][sites[i]], "DUPLICATE_SITE");
            authorizedSites[domain][sites[i]] = true;
        }
        emit CampaignRegistered(domain, headerHash, policyHash, committeeHash);
    }

    function contextMatches(
        bytes32 domain,
        uint256 headerHash,
        uint256 policyHash,
        uint256 committeeHash
    ) external view returns (bool) {
        Campaign storage campaign = campaigns[domain];
        return
            campaign.active &&
            campaign.headerHash == headerHash &&
            campaign.policyHash == policyHash &&
            campaign.committeeHash == committeeHash;
    }

    function fundCampaign(bytes32 domain, uint256 amount) external onlyOwner {
        Campaign storage campaign = campaigns[domain];
        require(campaign.active, "UNKNOWN_DOMAIN");
        require(amount > 0, "ZERO_AMOUNT");
        campaign.escrow += amount;
        emit CampaignFunded(domain, amount, campaign.escrow);
    }

    function settleVerified(
        bytes32 domain,
        bytes32 nullifier,
        bytes32 receiptId,
        address site,
        uint256 value,
        bytes32 authorizationCommitment
    ) public onlyVerifierAdapter returns (bool settled) {
        Campaign storage campaign = campaigns[domain];
        require(campaign.active, "UNKNOWN_DOMAIN");
        require(nullifier != bytes32(0) && receiptId != bytes32(0), "ZERO_RECEIPT");
        require(authorizedSites[domain][site], "UNAUTHORIZED_SITE");
        require(value > 0, "ZERO_VALUE");
        require(authorizationCommitment != bytes32(0), "ZERO_AUTHORIZATION");

        bytes32 winner = winningReceipt[domain][nullifier];
        if (winner != bytes32(0)) {
            emit ReceiptConflict(domain, nullifier, receiptId, winner);
            return false;
        }
        require(campaign.escrow >= value, "INSUFFICIENT_ESCROW");

        winningReceipt[domain][nullifier] = receiptId;
        winningAuthorization[domain][nullifier] = authorizationCommitment;
        campaign.escrow -= value;
        credits[domain][site] += value;
        emit ReceiptSettled(
            domain,
            nullifier,
            receiptId,
            site,
            value,
            authorizationCommitment
        );
        return true;
    }

    function settleVerifiedBatch(
        bytes32 domain,
        bytes32[] calldata nullifiers,
        bytes32[] calldata receiptIds,
        bytes32[] calldata authorizationCommitments,
        address[] calldata creditSites,
        uint256[] calldata creditValues,
        uint256 creditSiteCount
    ) external onlyVerifierAdapter returns (bool settled) {
        uint256 length = nullifiers.length;
        require(length > 0 && length <= 100 && receiptIds.length == length &&
            authorizationCommitments.length == length &&
            creditSites.length == length && creditValues.length == length &&
            creditSiteCount > 0 && creditSiteCount <= length, "BAD_BATCH");
        Campaign storage campaign = campaigns[domain];
        require(campaign.active, "UNKNOWN_DOMAIN");
        uint256 total;
        for (uint256 i = 0; i < length; i++) {
            require(nullifiers[i] != bytes32(0) && receiptIds[i] != bytes32(0),
                "ZERO_RECEIPT");
            require(authorizationCommitments[i] != bytes32(0), "BAD_BATCH_ITEM");
            require(winningReceipt[domain][nullifiers[i]] == bytes32(0),
                "RECEIPT_CONFLICT");
            winningReceipt[domain][nullifiers[i]] = receiptIds[i];
        }
        bytes32 nullifierListHash = keccak256(abi.encode(nullifiers));
        bytes32 authorizationListHash = keccak256(abi.encode(authorizationCommitments));
        batchAuthorizationRoots[domain][nullifierListHash] = authorizationListHash;
        emit BatchAuthorizationCommitted(domain,nullifierListHash,authorizationListHash);
        for (uint256 i = 0; i < creditSiteCount; i++) {
            require(authorizedSites[domain][creditSites[i]] && creditValues[i] > 0,
                "BAD_CREDIT_TOTAL");
            total += creditValues[i];
        }
        require(campaign.escrow >= total, "INSUFFICIENT_ESCROW");
        campaign.escrow -= total;
        for (uint256 i = 0; i < creditSiteCount; i++) {
            credits[domain][creditSites[i]] += creditValues[i];
        }
        return true;
    }

    function settleVerifiedBatch(
        SettlementItem[] calldata items
    ) external onlyVerifierAdapter {
        for (uint256 i = 0; i < items.length; i++) {
            SettlementItem calldata item = items[i];
            settleVerified(
                item.domain,
                item.nullifier,
                item.receiptId,
                item.site,
                item.value,
                item.authorizationCommitment
            );
        }
    }
}
