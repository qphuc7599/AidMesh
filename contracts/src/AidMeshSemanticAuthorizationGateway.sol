// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.36;

interface IAidMeshSemanticVerifier {
    function verifyProof(
        uint256[2] calldata a,
        uint256[2][2] calldata b,
        uint256[2] calldata c,
        uint256[17] calldata input
    ) external view returns (bool);
}

interface IAidMeshSemanticSettlement {
    function settleVerified(
        bytes32 domain,
        bytes32 nullifier,
        bytes32 receiptId,
        address site,
        uint256 value,
        bytes32 authorizationCommitment
    ) external returns (bool);

    function settleVerifiedBatch(
        bytes32 domain,
        bytes32[] calldata nullifiers,
        bytes32[] calldata receiptIds,
        bytes32[] calldata authorizationCommitments,
        address[] calldata creditSites,
        uint256[] calldata creditValues,
        uint256 creditSiteCount
    ) external returns (bool);

    function contextMatches(
        bytes32 domain,
        uint256 headerHash,
        uint256 policyHash,
        uint256 committeeHash
    ) external view returns (bool);
}

/// @title AidMeshSemanticAuthorizationGateway
/// @notice Verifies quorum or transferable-right semantics before settlement.
/// @dev No trusted adapter supplies an opaque authorization verdict. Anyone may
///      relay a transaction, while the contract reconstructs the exact statement,
///      validates threshold membership or the right transition chain, checks the
///      site signature, and then invokes first-valid-wins accounting.
contract AidMeshSemanticAuthorizationGateway {
    enum AuthorizationMode {
        Quorum,
        TransferableRight
    }

    struct BoundReceipt {
        bytes32 receiptId;
        address site;
        bytes32 releaseIntentId;
        bytes32 receiptSkeletonHash;
        bytes32 authorizationHash;
        bytes siteSignature;
    }

    struct AuthorizationDomain {
        uint64 epoch;
        uint16 threshold;
        uint16 witnessCount;
        uint256 committeeHash;
        uint256 honestSiteRiskCap;
        uint256 issuedRightValue;
        bool active;
    }

    struct RightState {
        bytes32 domain;
        address owner;
        uint256 value;
        uint64 epoch;
        uint64 nonce;
        bool spent;
    }

    struct RightTransition {
        address from;
        address to;
        uint64 epoch;
        uint64 nonce;
        bytes[] witnessSignatures;
    }

    bytes32 public constant PROTOCOL_DOMAIN =
        keccak256("AIDMESH_PROOF_GATED_ACCOUNTING_V2");
    bytes32 public constant SEMANTIC_AUTHORIZATION_DOMAIN =
        keccak256("AIDMESH_SEMANTIC_AUTHORIZATION_V2");
    bytes32 public constant RIGHT_TRANSITION_DOMAIN =
        keccak256("AIDMESH_RIGHT_TRANSITION_V1");
    uint256 private constant SECP256K1N_DIV_2 =
        0x7fffffffffffffffffffffffffffffff5d576e7357a4501ddfe92f46681b20a0;
    uint256 private constant SNARK_SCALAR_FIELD =
        21888242871839275222246405745257275088548364400416034343698204186575808495617;

    IAidMeshSemanticVerifier public immutable verifier;
    IAidMeshSemanticSettlement public settlement;
    address public immutable owner;
    bool private entered;

    mapping(bytes32 => AuthorizationDomain) public authorizationDomains;
    mapping(bytes32 => mapping(address => bool)) public authorizedWitnesses;
    mapping(bytes32 => RightState) public rights;
    mapping(bytes32 => bool) public recoveryEnabled;
    mapping(bytes32 => uint16) public recoveryFaultBounds;
    mapping(bytes32 => RecoveryBatch) public recoveryBatches;
    mapping(bytes32 => bytes32) public rightStagedBatch;
    mapping(bytes32 => bytes32) public nullifierStagedBatch;
    mapping(bytes32 => bytes32[]) private stagedRecoveryDigests;
    event RecoveryConfigured(bytes32 indexed domain, uint16 faults);
    event RecoveryReconciled(bytes32 indexed domain, bytes32 indexed rightId,
        bytes32 indexed transferId, uint64 recoveryView, bool accepted, bool credited);
    event RecoveryBatchOpened(bytes32 indexed batchId, bytes32 indexed domain,
        uint16 expectedCount, uint256 expectedValue, uint64 deadlineBlock);
    event RecoveryBatchStaged(bytes32 indexed batchId, uint16 addedCount,
        uint16 stagedCount, uint256 stagedValue);
    event RecoveryBatchCommitted(bytes32 indexed batchId, uint16 count,
        uint256 value);
    event RecoveryBatchCancelled(bytes32 indexed batchId);

    struct RecoveryCertificate {
        bytes32 rightId;
        bytes32 transferId;
        address source;
        address destination;
        uint64 epoch;
        uint64 nonce;
        uint64 recoveryView;
        bool accept;
        bytes sourceSignature;
        bytes[] commitSignatures;
    }

    struct RecoverySettlementItem {
        uint256[2] a;
        uint256[2][2] b;
        uint256[2] c;
        uint256[17] input;
        BoundReceipt receipt;
        RecoveryCertificate recovery;
    }

    struct RecoveryBatch {
        bytes32 domain;
        uint16 expectedCount;
        uint16 stagedCount;
        uint256 expectedValue;
        uint256 stagedValue;
        uint64 deadlineBlock;
        bool committed;
    }

    struct StagedRecovery {
        bytes32 domain;
        bytes32 rightId;
        bytes32 transferId;
        bytes32 nullifier;
        bytes32 receiptId;
        address recipient;
        uint256 value;
        bytes32 authorization;
        uint64 nonce;
        uint64 recoveryView;
        bool accept;
    }

    function verifyRecoveryBatchAndSettle(RecoverySettlementItem[] calldata items)
        external nonReentrant returns (bool[] memory credited)
    {
        require(items.length > 0 && items.length <= 100, "BAD_BATCH_SIZE");
        credited = new bool[](items.length);
        for (uint256 i = 0; i < items.length; i++) {
            RecoverySettlementItem calldata item = items[i];
            credited[i] = _verifyRecoveryAndSettle(item.a,item.b,item.c,item.input,
                item.receipt,item.recovery);
        }
    }

    /// @notice Open an EIP-7825-compatible atomic settlement batch. Expensive
    /// proof and certificate checks are staged in bounded transactions; no
    /// credit is created until finalizeRecoveryBatch commits every item.
    function openRecoveryBatch(bytes32 batchId, bytes32 domain, uint16 expectedCount,
        uint256 expectedValue, uint64 deadlineBlock) external onlyOwner
    {
        require(batchId != bytes32(0) && recoveryBatches[batchId].domain == bytes32(0),
            "BATCH_EXISTS");
        require(authorizationDomains[domain].active && recoveryEnabled[domain],
            "BAD_RECOVERY_DOMAIN");
        require(expectedCount > 0 && expectedCount <= 100 && expectedValue > 0,
            "BAD_BATCH_TARGET");
        require(deadlineBlock > block.number, "BAD_BATCH_DEADLINE");
        recoveryBatches[batchId] = RecoveryBatch(domain,expectedCount,0,
            expectedValue,0,deadlineBlock,false);
        emit RecoveryBatchOpened(batchId,domain,expectedCount,expectedValue,deadlineBlock);
    }

    function stageRecoveryBatch(bytes32 batchId, RecoverySettlementItem[] calldata items)
        external onlyOwner nonReentrant
    {
        RecoveryBatch storage batch = recoveryBatches[batchId];
        require(batch.domain != bytes32(0) && !batch.committed, "BAD_BATCH");
        require(block.number <= batch.deadlineBlock, "BATCH_EXPIRED");
        require(items.length > 0 &&
            uint256(batch.stagedCount) + items.length <= batch.expectedCount,
            "BAD_STAGE_SIZE");
        uint256 addedValue;
        for (uint256 i = 0; i < items.length; i++) {
            RecoverySettlementItem calldata item = items[i];
            (bytes32 domain,,address recipient,bytes32 authorization) =
                _validateRecoveryItem(item.a,item.b,item.c,item.input,
                    item.receipt,item.recovery);
            bytes32 nullifier = bytes32(item.input[7]);
            require(domain == batch.domain, "MIXED_BATCH_DOMAIN");
            require(rightStagedBatch[item.recovery.rightId] == bytes32(0),
                "RIGHT_ALREADY_STAGED");
            require(nullifierStagedBatch[nullifier] == bytes32(0),
                "NULLIFIER_ALREADY_STAGED");
            rightStagedBatch[item.recovery.rightId] = batchId;
            nullifierStagedBatch[nullifier] = batchId;
            stagedRecoveryDigests[batchId].push(keccak256(abi.encode(domain,
                item.recovery.rightId,item.recovery.transferId,nullifier,
                item.receipt.receiptId,recipient,item.input[4],authorization,
                item.recovery.nonce,item.recovery.recoveryView,item.recovery.accept)));
            addedValue += item.input[4];
        }
        batch.stagedCount += uint16(items.length);
        batch.stagedValue += addedValue;
        require(batch.stagedValue <= batch.expectedValue, "BATCH_VALUE_EXCEEDED");
        emit RecoveryBatchStaged(batchId,uint16(items.length),batch.stagedCount,
            batch.stagedValue);
    }

    function stagedRecoveryDigest(bytes32 batchId, uint256 index)
        external view returns (bytes32)
    {
        return stagedRecoveryDigests[batchId][index];
    }

    function finalizeRecoveryBatch(bytes32 batchId, StagedRecovery[] calldata records)
        external nonReentrant
        returns (bool committed)
    {
        RecoveryBatch storage batch = recoveryBatches[batchId];
        require(batch.domain != bytes32(0) && !batch.committed, "BAD_BATCH");
        require(block.number <= batch.deadlineBlock, "BATCH_EXPIRED");
        require(batch.stagedCount == batch.expectedCount &&
            batch.stagedValue == batch.expectedValue, "INCOMPLETE_BATCH");
        batch.committed = true;
        require(records.length == batch.expectedCount, "BAD_FINAL_RECORDS");
        bytes32[] memory nullifiers = new bytes32[](records.length);
        bytes32[] memory receiptIds = new bytes32[](records.length);
        bytes32[] memory authorizations = new bytes32[](records.length);
        address[] memory creditSites = new address[](records.length);
        uint256[] memory creditValues = new uint256[](records.length);
        uint256 creditSiteCount;
        for (uint256 i = 0; i < records.length; i++) {
            StagedRecovery calldata record = records[i];
            require(stagedRecoveryDigests[batchId][i] == keccak256(abi.encode(
                record.domain,record.rightId,record.transferId,record.nullifier,
                record.receiptId,record.recipient,record.value,record.authorization,
                record.nonce,record.recoveryView,record.accept)), "STAGED_DIGEST_MISMATCH");
            RightState storage right = rights[record.rightId];
            require(rightStagedBatch[record.rightId] == batchId &&
                nullifierStagedBatch[record.nullifier] == batchId,
                "LOST_BATCH_LOCK");
            require(right.domain == record.domain && !right.spent &&
                right.value == record.value && record.nonce == right.nonce + 1,
                "STALE_STAGED_RIGHT");
            nullifiers[i] = record.nullifier;
            receiptIds[i] = record.receiptId;
            authorizations[i] = record.authorization;
            uint256 siteIndex;
            while (siteIndex < creditSiteCount &&
                creditSites[siteIndex] != record.recipient) siteIndex++;
            if (siteIndex == creditSiteCount) {
                creditSites[creditSiteCount] = record.recipient;
                creditSiteCount++;
            }
            creditValues[siteIndex] += record.value;
        }
        require(settlement.settleVerifiedBatch(batch.domain,nullifiers,receiptIds,
            authorizations,creditSites,creditValues,creditSiteCount),
            "SETTLEMENT_CONFLICT");
        emit RecoveryBatchCommitted(batchId,batch.stagedCount,batch.stagedValue);
        return true;
    }

    function cancelExpiredRecoveryBatch(bytes32 batchId, StagedRecovery[] calldata records)
        external onlyOwner nonReentrant
    {
        RecoveryBatch storage batch = recoveryBatches[batchId];
        require(batch.domain != bytes32(0) && !batch.committed &&
            block.number > batch.deadlineBlock, "BATCH_NOT_CANCELLABLE");
        require(records.length == batch.stagedCount, "BAD_CANCEL_RECORDS");
        for (uint256 i = 0; i < records.length; i++) {
            require(stagedRecoveryDigests[batchId][i] == keccak256(abi.encode(
                records[i].domain,records[i].rightId,records[i].transferId,
                records[i].nullifier,records[i].receiptId,records[i].recipient,
                records[i].value,records[i].authorization,records[i].nonce,
                records[i].recoveryView,records[i].accept)), "STAGED_DIGEST_MISMATCH");
            delete rightStagedBatch[records[i].rightId];
            delete nullifierStagedBatch[records[i].nullifier];
        }
        delete stagedRecoveryDigests[batchId];
        delete recoveryBatches[batchId];
        emit RecoveryBatchCancelled(batchId);
    }

    /// @dev This EVM/secp256k1 profile must be signed by recovery participants.
    /// It is not a trusted conversion of an Ed25519 JSON certificate.
    function enableRecovery(bytes32 domain, uint16 faults) external onlyOwner {
        AuthorizationDomain storage d = authorizationDomains[domain];
        require(d.active && !recoveryEnabled[domain], "BAD_RECOVERY_DOMAIN");
        require(2 * uint256(d.threshold) > uint256(d.witnessCount) + faults,
            "UNSAFE_RECOVERY_QUORUM");
        require(uint256(d.threshold) + faults <= d.witnessCount, "NO_RECOVERY_LIVENESS_QUORUM");
        recoveryEnabled[domain] = true;
        recoveryFaultBounds[domain] = faults;
        emit RecoveryConfigured(domain, faults);
    }

    function recoveryTransferHash(bytes32 domain, RecoveryCertificate calldata r)
        public view returns (bytes32)
    {
        return keccak256(abi.encode(keccak256("AIDMESH_RECOVERY_TRANSFER_EVM_V1"),
            block.chainid, address(this), address(settlement), domain,
            r.rightId, r.transferId, r.source, r.destination, r.epoch, r.nonce));
    }

    function recoveryCommitHash(bytes32 domain, RecoveryCertificate calldata r)
        public view returns (bytes32)
    {
        return keccak256(abi.encode(keccak256("AIDMESH_RECOVERY_COMMIT_EVM_V1"),
            recoveryTransferHash(domain, r), r.recoveryView, r.accept));
    }

    function recoveryAuthorizationHash(uint256[17] calldata input,
        BoundReceipt calldata receipt, RecoveryCertificate calldata r)
        public view returns (bytes32)
    {
        return keccak256(abi.encode(keccak256("AIDMESH_RECOVERY_RELEASE_EVM_V1"),
            recoveryCommitHash(proofDomain(input), r), proofStatementHash(input),
            receipt.releaseIntentId, receipt.receiptSkeletonHash, receipt.site));
    }

    /// @notice Reconcile a terminal ACCEPT/RECLAIM receipt, consuming one issued right.
    /// @dev Terminal agreement is conditional on the recovery signers' protocol;
    /// the contract checks authenticated commits, not their off-chain view history.
    function verifyRecoveryAndSettle(
        uint256[2] calldata a, uint256[2][2] calldata b, uint256[2] calldata c,
        uint256[17] calldata input, BoundReceipt calldata receipt,
        RecoveryCertificate calldata r
    ) external nonReentrant returns (bool settled) {
        return _verifyRecoveryAndSettle(a,b,c,input,receipt,r);
    }

    function _verifyRecoveryAndSettle(
        uint256[2] calldata a, uint256[2][2] calldata b, uint256[2] calldata c,
        uint256[17] calldata input, BoundReceipt calldata receipt,
        RecoveryCertificate calldata r
    ) internal returns (bool settled) {
        (bytes32 domain,,address recipient,bytes32 authorization) =
            _validateRecoveryItem(a,b,c,input,receipt,r);
        RightState storage right = rights[r.rightId];
        require(rightStagedBatch[r.rightId] == bytes32(0), "RIGHT_STAGED");
        require(nullifierStagedBatch[bytes32(input[7])] == bytes32(0),
            "NULLIFIER_STAGED");
        right.owner = recipient;
        right.nonce = r.nonce;
        right.spent = true;
        settled = settlement.settleVerified(domain,bytes32(input[7]),receipt.receiptId,
            recipient,input[4],authorization);
        emit SemanticSettlement(domain,bytes32(input[7]),receipt.receiptId,recipient,
            input[4],AuthorizationMode.TransferableRight,authorization,settled);
        emit RecoveryReconciled(domain,r.rightId,r.transferId,r.recoveryView,r.accept,settled);
    }

    function _validateRecoveryItem(
        uint256[2] calldata a, uint256[2][2] calldata b, uint256[2] calldata c,
        uint256[17] calldata input, BoundReceipt calldata receipt,
        RecoveryCertificate calldata r
    ) internal view returns (bytes32 domain, bytes32 statementHash,
        address recipient, bytes32 authorization)
    {
        (domain, statementHash) = _validateProofAndContext(a,b,c,input);
        require(recoveryEnabled[domain], "RECOVERY_DISABLED");
        require(input[8] == 1, "RECOVERY_REQUIRES_FALLBACK");
        RightState storage right = rights[r.rightId];
        require(right.domain == domain && !right.spent, "INVALID_OR_SPENT_RIGHT");
        require(r.epoch == authorizationDomains[domain].epoch && right.epoch == r.epoch,
            "STALE_EPOCH");
        require(right.owner == r.source && right.value == input[4] && r.nonce == right.nonce + 1,
            "RECOVERY_RIGHT_MISMATCH");
        require(r.transferId != bytes32(0) && r.destination != address(0)
            && r.source != r.destination, "BAD_RECOVERY_TRANSFER");
        require(_recover(recoveryTransferHash(domain,r),r.sourceSignature) == r.source,
            "WRONG_RECOVERY_SOURCE");
        _verifyThreshold(domain,recoveryCommitHash(domain,r),r.commitSignatures);
        recipient = r.accept ? r.destination : r.source;
        require(receipt.site == recipient, "WRONG_RECOVERY_RECIPIENT");
        require(receipt.receiptSkeletonHash == receiptSkeleton(input,receipt.releaseIntentId,
            recipient,r.epoch), "RECEIPT_SKELETON_MISMATCH");
        authorization = recoveryAuthorizationHash(input,receipt,r);
        _verifySiteReceipt(domain,statementHash,receipt,authorization);
    }

    event AuthorizationDomainRegistered(
        bytes32 indexed domain,
        uint64 epoch,
        uint16 threshold,
        uint16 witnessCount,
        uint256 committeeHash,
        uint256 honestSiteRiskCap
    );
    event RightIssued(
        bytes32 indexed domain,
        bytes32 indexed rightId,
        address indexed rightOwner,
        uint256 value,
        uint64 epoch
    );
    event RightTransitionApplied(
        bytes32 indexed domain,
        bytes32 indexed rightId,
        address indexed to,
        uint64 nonce
    );
    event SemanticSettlement(
        bytes32 indexed domain,
        bytes32 indexed nullifier,
        bytes32 indexed receiptId,
        address site,
        uint256 value,
        AuthorizationMode mode,
        bytes32 authorizationHash,
        bool settled
    );

    modifier onlyOwner() {
        require(msg.sender == owner, "ONLY_OWNER");
        _;
    }

    modifier nonReentrant() {
        require(!entered, "REENTRANT_CALL");
        entered = true;
        _;
        entered = false;
    }

    constructor(address verifierAddress) {
        require(verifierAddress != address(0), "ZERO_VERIFIER");
        owner = msg.sender;
        verifier = IAidMeshSemanticVerifier(verifierAddress);
    }

    function setSettlement(address settlementAddress) external onlyOwner {
        require(address(settlement) == address(0), "SETTLEMENT_ALREADY_SET");
        require(settlementAddress != address(0), "ZERO_SETTLEMENT");
        settlement = IAidMeshSemanticSettlement(settlementAddress);
    }

    function registerAuthorizationDomain(
        bytes32 domain,
        uint64 epoch,
        uint16 threshold,
        uint256 committeeHash,
        uint256 honestSiteRiskCap,
        address[] calldata witnesses
    ) external onlyOwner {
        require(domain != bytes32(0), "ZERO_DOMAIN");
        require(!authorizationDomains[domain].active, "DOMAIN_EXISTS");
        require(epoch > 0 && honestSiteRiskCap > 0, "BAD_DOMAIN_PARAMETERS");
        require(
            threshold > 0 && threshold <= witnesses.length && witnesses.length <= type(uint16).max,
            "BAD_THRESHOLD"
        );
        require(
            committeeHash ==
                semanticCommitteeHash(epoch, threshold, honestSiteRiskCap, witnesses),
            "COMMITTEE_HASH_MISMATCH"
        );
        authorizationDomains[domain] = AuthorizationDomain(
            epoch,
            threshold,
            uint16(witnesses.length),
            committeeHash,
            honestSiteRiskCap,
            0,
            true
        );
        for (uint256 i = 0; i < witnesses.length; i++) {
            address witness = witnesses[i];
            require(witness != address(0), "ZERO_WITNESS");
            require(!authorizedWitnesses[domain][witness], "DUPLICATE_WITNESS");
            authorizedWitnesses[domain][witness] = true;
        }
        emit AuthorizationDomainRegistered(
            domain,
            epoch,
            threshold,
            uint16(witnesses.length),
            committeeHash,
            honestSiteRiskCap
        );
    }

    function semanticCommitteeHash(
        uint64 epoch,
        uint16 threshold,
        uint256 honestSiteRiskCap,
        address[] calldata witnesses
    ) public pure returns (uint256) {
        return
            uint256(
                keccak256(
                    abi.encode(
                        SEMANTIC_AUTHORIZATION_DOMAIN,
                        epoch,
                        threshold,
                        honestSiteRiskCap,
                        witnesses
                    )
                )
            ) % SNARK_SCALAR_FIELD;
    }

    function issueRight(
        bytes32 domain,
        bytes32 rightId,
        address rightOwner,
        uint256 value
    ) external onlyOwner {
        AuthorizationDomain storage configured = authorizationDomains[domain];
        require(configured.active, "UNKNOWN_DOMAIN");
        require(rightId != bytes32(0) && rights[rightId].domain == bytes32(0), "BAD_RIGHT_ID");
        require(rightOwner != address(0) && value > 0, "BAD_RIGHT");
        require(
            configured.issuedRightValue + value <= configured.honestSiteRiskCap,
            "RISK_CAP_EXCEEDED"
        );
        configured.issuedRightValue += value;
        rights[rightId] = RightState(
            domain,
            rightOwner,
            value,
            configured.epoch,
            0,
            false
        );
        emit RightIssued(domain, rightId, rightOwner, value, configured.epoch);
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

    function proofStatementHash(
        uint256[17] calldata input
    ) public pure returns (bytes32) {
        return keccak256(abi.encode(PROTOCOL_DOMAIN, input));
    }

    function receiptSkeleton(
        uint256[17] calldata input,
        bytes32 releaseIntentId,
        address site,
        uint64 epoch
    ) public pure returns (bytes32) {
        return
            keccak256(
                abi.encode(
                    SEMANTIC_AUTHORIZATION_DOMAIN,
                    keccak256("AIDMESH_RECEIPT_SKELETON_V1"),
                    proofDomain(input),
                    proofStatementHash(input),
                    bytes32(input[7]),
                    releaseIntentId,
                    site,
                    input[4],
                    epoch
                )
            );
    }

    function authorizationIntent(
        uint256[17] calldata input,
        bytes32 releaseIntentId,
        bytes32 receiptSkeletonHash,
        address site,
        uint64 epoch
    ) public pure returns (bytes32) {
        return
            keccak256(
                abi.encode(
                    SEMANTIC_AUTHORIZATION_DOMAIN,
                    AuthorizationMode.Quorum,
                    proofDomain(input),
                    proofStatementHash(input),
                    bytes32(input[7]),
                    releaseIntentId,
                    receiptSkeletonHash,
                    site,
                    input[4],
                    epoch
                )
            );
    }

    function rightTransitionStatement(
        bytes32 domain,
        bytes32 rightId,
        address from,
        address to,
        uint64 epoch,
        uint64 nonce
    ) public pure returns (bytes32) {
        return
            keccak256(
                abi.encode(
                    RIGHT_TRANSITION_DOMAIN,
                    domain,
                    rightId,
                    from,
                    to,
                    epoch,
                    nonce
                )
            );
    }

    function rightAuthorizationHash(
        uint256[17] calldata input,
        bytes32 releaseIntentId,
        bytes32 receiptSkeletonHash,
        address site,
        bytes32 rightId,
        uint64 epoch,
        RightTransition[] calldata transitions
    ) public pure returns (bytes32 result) {
        result = keccak256(
            abi.encode(
                SEMANTIC_AUTHORIZATION_DOMAIN,
                AuthorizationMode.TransferableRight,
                proofDomain(input),
                proofStatementHash(input),
                bytes32(input[7]),
                releaseIntentId,
                receiptSkeletonHash,
                site,
                input[4],
                rightId,
                epoch
            )
        );
        for (uint256 i = 0; i < transitions.length; i++) {
            RightTransition calldata transition = transitions[i];
            result = keccak256(
                abi.encode(
                    result,
                    transition.from,
                    transition.to,
                    transition.epoch,
                    transition.nonce
                )
            );
        }
    }

    function receiptPayload(
        bytes32 domain,
        bytes32 statementHash,
        bytes32 receiptId,
        address site,
        bytes32 releaseIntentId,
        bytes32 receiptSkeletonHash,
        bytes32 authorizationHash
    ) public pure returns (bytes32) {
        return
            keccak256(
                abi.encode(
                    SEMANTIC_AUTHORIZATION_DOMAIN,
                    domain,
                    statementHash,
                    receiptId,
                    site,
                    releaseIntentId,
                    receiptSkeletonHash,
                    authorizationHash
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

    function _verifyThreshold(
        bytes32 domain,
        bytes32 statement,
        bytes[] calldata signatures
    ) internal view {
        AuthorizationDomain storage configured = authorizationDomains[domain];
        require(configured.active, "UNKNOWN_AUTHORIZATION_DOMAIN");
        require(signatures.length >= configured.threshold, "INSUFFICIENT_QUORUM");
        address[] memory recovered = new address[](signatures.length);
        for (uint256 i = 0; i < signatures.length; i++) {
            address signer = _recover(statement, signatures[i]);
            require(authorizedWitnesses[domain][signer], "FOREIGN_WITNESS");
            for (uint256 j = 0; j < i; j++) {
                require(recovered[j] != signer, "DUPLICATE_WITNESS_SIGNATURE");
            }
            recovered[i] = signer;
        }
    }

    function _validateProofAndContext(
        uint256[2] calldata a,
        uint256[2][2] calldata b,
        uint256[2] calldata c,
        uint256[17] calldata input
    ) internal view returns (bytes32 domain, bytes32 statementHash) {
        require(address(settlement) != address(0), "SETTLEMENT_NOT_SET");
        require(input[11] == block.chainid, "WRONG_CHAIN");
        require(input[12] == uint256(uint160(address(this))), "WRONG_GATEWAY");
        require(input[13] == uint256(uint160(address(settlement))), "WRONG_SETTLEMENT");
        require(bytes32(input[7]) != bytes32(0) && input[4] > 0, "BAD_PUBLIC_INPUT");
        require(verifier.verifyProof(a, b, c, input), "INVALID_PROOF");
        domain = proofDomain(input);
        require(
            settlement.contextMatches(domain, input[14], input[15], input[16]),
            "UNREGISTERED_CONTEXT"
        );
        require(authorizationDomains[domain].active, "UNKNOWN_AUTHORIZATION_DOMAIN");
        require(
            authorizationDomains[domain].committeeHash == input[16],
            "PROOF_COMMITTEE_MISMATCH"
        );
        statementHash = proofStatementHash(input);
    }

    function _verifySiteReceipt(
        bytes32 domain,
        bytes32 statementHash,
        BoundReceipt calldata receipt,
        bytes32 expectedAuthorizationHash
    ) internal pure {
        require(
            receipt.receiptId != bytes32(0) &&
                receipt.site != address(0) &&
                receipt.releaseIntentId != bytes32(0),
            "BAD_RECEIPT"
        );
        require(
            receipt.authorizationHash == expectedAuthorizationHash,
            "AUTHORIZATION_HASH_MISMATCH"
        );
        bytes32 payload = receiptPayload(
            domain,
            statementHash,
            receipt.receiptId,
            receipt.site,
            receipt.releaseIntentId,
            receipt.receiptSkeletonHash,
            expectedAuthorizationHash
        );
        require(_recover(payload, receipt.siteSignature) == receipt.site, "WRONG_SITE_SIGNATURE");
    }

    function verifyQuorumAndSettle(
        uint256[2] calldata a,
        uint256[2][2] calldata b,
        uint256[2] calldata c,
        uint256[17] calldata input,
        BoundReceipt calldata receipt,
        uint64 epoch,
        bytes[] calldata witnessSignatures
    ) external nonReentrant returns (bool settled) {
        (bytes32 domain, bytes32 statementHash) = _validateProofAndContext(a, b, c, input);
        require(nullifierStagedBatch[bytes32(input[7])] == bytes32(0),
            "NULLIFIER_STAGED");
        require(epoch == authorizationDomains[domain].epoch, "STALE_EPOCH");
        require(receipt.releaseIntentId != bytes32(0), "BAD_RELEASE_INTENT");
        bytes32 expectedSkeleton = receiptSkeleton(
            input,
            receipt.releaseIntentId,
            receipt.site,
            epoch
        );
        require(
            receipt.receiptSkeletonHash == expectedSkeleton,
            "RECEIPT_SKELETON_MISMATCH"
        );
        bytes32 authorizationHash = authorizationIntent(
            input,
            receipt.releaseIntentId,
            expectedSkeleton,
            receipt.site,
            epoch
        );
        _verifyThreshold(domain, authorizationHash, witnessSignatures);
        _verifySiteReceipt(domain, statementHash, receipt, authorizationHash);
        settled = settlement.settleVerified(
            domain,
            bytes32(input[7]),
            receipt.receiptId,
            receipt.site,
            input[4],
            authorizationHash
        );
        emit SemanticSettlement(
            domain,
            bytes32(input[7]),
            receipt.receiptId,
            receipt.site,
            input[4],
            AuthorizationMode.Quorum,
            authorizationHash,
            settled
        );
    }

    function verifyRightAndSettle(
        uint256[2] calldata a,
        uint256[2][2] calldata b,
        uint256[2] calldata c,
        uint256[17] calldata input,
        BoundReceipt calldata receipt,
        bytes32 rightId,
        uint64 epoch,
        RightTransition[] calldata transitions
    ) external nonReentrant returns (bool settled) {
        (bytes32 domain, bytes32 statementHash) = _validateProofAndContext(a, b, c, input);
        AuthorizationDomain storage configured = authorizationDomains[domain];
        require(epoch == configured.epoch, "STALE_EPOCH");
        RightState storage right = rights[rightId];
        require(right.domain == domain && !right.spent, "INVALID_OR_SPENT_RIGHT");
        require(rightStagedBatch[rightId] == bytes32(0) &&
            nullifierStagedBatch[bytes32(input[7])] == bytes32(0), "RIGHT_STAGED");
        require(right.epoch == epoch && right.value == input[4], "RIGHT_STATEMENT_MISMATCH");
        for (uint256 i = 0; i < transitions.length; i++) {
            RightTransition calldata transition = transitions[i];
            require(
                transition.from == right.owner &&
                    transition.to != address(0) &&
                    transition.epoch == epoch &&
                    transition.nonce == right.nonce + 1,
                "INVALID_RIGHT_TRANSITION"
            );
            bytes32 transitionStatement = rightTransitionStatement(
                domain,
                rightId,
                transition.from,
                transition.to,
                transition.epoch,
                transition.nonce
            );
            _verifyThreshold(domain, transitionStatement, transition.witnessSignatures);
            right.owner = transition.to;
            right.nonce = transition.nonce;
            emit RightTransitionApplied(domain, rightId, transition.to, transition.nonce);
        }
        require(right.owner == receipt.site, "RIGHT_NOT_OWNED_BY_SITE");
        require(receipt.releaseIntentId != bytes32(0), "BAD_RELEASE_INTENT");
        bytes32 expectedSkeleton = receiptSkeleton(
            input,
            receipt.releaseIntentId,
            receipt.site,
            epoch
        );
        require(
            receipt.receiptSkeletonHash == expectedSkeleton,
            "RECEIPT_SKELETON_MISMATCH"
        );
        bytes32 authorizationHash = rightAuthorizationHash(
            input,
            receipt.releaseIntentId,
            receipt.receiptSkeletonHash,
            receipt.site,
            rightId,
            epoch,
            transitions
        );
        _verifySiteReceipt(domain, statementHash, receipt, authorizationHash);
        right.spent = true;
        settled = settlement.settleVerified(
            domain,
            bytes32(input[7]),
            receipt.receiptId,
            receipt.site,
            input[4],
            authorizationHash
        );
        emit SemanticSettlement(
            domain,
            bytes32(input[7]),
            receipt.receiptId,
            receipt.site,
            input[4],
            AuthorizationMode.TransferableRight,
            authorizationHash,
            settled
        );
    }
}

