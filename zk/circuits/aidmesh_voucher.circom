pragma circom 2.2.0;

include "circomlib/circuits/poseidon.circom";
include "circomlib/circuits/comparators.circom";

// Fixed-depth, direction-constrained Poseidon Merkle membership. The root
// commits to an ordered tree; each path bit is constrained to be Boolean.
template PoseidonMerkleProof(depth, nodeDomainTag) {
    signal input leaf;
    signal input pathElements[depth];
    signal input pathIndices[depth];
    signal output root;

    signal current[depth + 1];
    signal left[depth];
    signal right[depth];
    component hashes[depth];

    current[0] <== leaf;
    for (var level = 0; level < depth; level++) {
        pathIndices[level] * (pathIndices[level] - 1) === 0;
        left[level] <== current[level] + pathIndices[level] *
            (pathElements[level] - current[level]);
        right[level] <== pathElements[level] + pathIndices[level] *
            (current[level] - pathElements[level]);
        hashes[level] = Poseidon(3);
        hashes[level].inputs[0] <== nodeDomainTag;
        hashes[level].inputs[1] <== left[level];
        hashes[level].inputs[2] <== right[level];
        current[level + 1] <== hashes[level].out;
    }
    root <== current[depth];
}

template AidMeshVoucher(credentialDepth, entitlementDepth) {
    // Explicit, versioned cryptographic object domains. Values are
    // SHA-256(label) reduced modulo the BN254 scalar field and are mirrored in
    // zk/domain_tags.json. Distinct tree-node tags prevent cross-tree proof reuse.
    var TAG_SECRET_COMMITMENT = 7957202165008941587942884175822142575197271636839200244531546926364797300615;
    var TAG_DEPLOYMENT_CONTEXT = 10279457872467220686037605195531073081020785024427662631323139980601738806341;
    var TAG_CREDENTIAL_LEAF = 10662832029939819777163154605710976618610670096724312987459462279219475715574;
    var TAG_ENTITLEMENT_LEAF = 10495568564360266868986809719319371809578015551726909450073431995454971263129;
    var TAG_CREDENTIAL_NODE = 3219120787801822970970063404307210132667460704307990590043886201694231817604;
    var TAG_ENTITLEMENT_NODE = 13002880936769648891828537586384038786036541435624647724130807206407663247646;
    var TAG_NULLIFIER = 14044334092243766055814656962391606337572969158474304408345254470199908398612;
    var TAG_FALLBACK = 18342164167427212437636873626165678946354110832429500096949564471355489769136;
    // Public safety domain and disclosed redemption fields.
    signal input campaignId;
    signal input epoch;
    signal input riskCell;
    signal input category;
    signal input value;
    signal input credentialRoot;
    signal input entitlementRoot;
    signal input nullifier;
    signal input fallbackMode;
    signal input fallbackWindow;
    signal input fallbackTag;
    signal input chainId;
    signal input gatewayAddress;
    signal input settlementAddress;
    signal input headerHash;
    signal input policyHash;
    signal input committeeHash;

    // Private credential, entitlement index, and membership witnesses.
    signal input secret;
    signal input secretBlinding;
    signal input groupCode;
    signal input quota;
    signal input voucherIndex;
    signal input credentialPathElements[credentialDepth];
    signal input credentialPathIndices[credentialDepth];
    signal input entitlementPathElements[entitlementDepth];
    signal input entitlementPathIndices[entitlementDepth];

    component secretCommitment = Poseidon(3);
    secretCommitment.inputs[0] <== TAG_SECRET_COMMITMENT;
    secretCommitment.inputs[1] <== secret;
    secretCommitment.inputs[2] <== secretBlinding;

    // Bind the proof statement to one concrete deployment and the complete
    // campaign-policy context.  The EVM checks the three runtime anchors and
    // the registered header/policy/committee fields before settlement.
    component deploymentContext = Poseidon(7);
    deploymentContext.inputs[0] <== TAG_DEPLOYMENT_CONTEXT;
    deploymentContext.inputs[1] <== chainId;
    deploymentContext.inputs[2] <== gatewayAddress;
    deploymentContext.inputs[3] <== settlementAddress;
    deploymentContext.inputs[4] <== headerHash;
    deploymentContext.inputs[5] <== policyHash;
    deploymentContext.inputs[6] <== committeeHash;

    // One credential-registry leaf authorizes one category quota. The root is
    // threshold-signed in the campaign header outside this circuit.
    component credentialLeafHash = Poseidon(9);
    credentialLeafHash.inputs[0] <== TAG_CREDENTIAL_LEAF;
    credentialLeafHash.inputs[1] <== secretCommitment.out;
    credentialLeafHash.inputs[2] <== campaignId;
    credentialLeafHash.inputs[3] <== epoch;
    credentialLeafHash.inputs[4] <== riskCell;
    credentialLeafHash.inputs[5] <== groupCode;
    credentialLeafHash.inputs[6] <== category;
    credentialLeafHash.inputs[7] <== quota;
    credentialLeafHash.inputs[8] <== deploymentContext.out;

    component credentialMembership = PoseidonMerkleProof(credentialDepth, TAG_CREDENTIAL_NODE);
    credentialMembership.leaf <== credentialLeafHash.out;
    for (var ci = 0; ci < credentialDepth; ci++) {
        credentialMembership.pathElements[ci] <== credentialPathElements[ci];
        credentialMembership.pathIndices[ci] <== credentialPathIndices[ci];
    }
    credentialMembership.root === credentialRoot;

    component positiveIndex = LessThan(64);
    positiveIndex.in[0] <== 0;
    positiveIndex.in[1] <== voucherIndex;
    positiveIndex.out === 1;

    component withinQuota = LessEqThan(64);
    withinQuota.in[0] <== voucherIndex;
    withinQuota.in[1] <== quota;
    withinQuota.out === 1;

    component entitlementLeafHash = Poseidon(9);
    entitlementLeafHash.inputs[0] <== TAG_ENTITLEMENT_LEAF;
    entitlementLeafHash.inputs[1] <== campaignId;
    entitlementLeafHash.inputs[2] <== epoch;
    entitlementLeafHash.inputs[3] <== riskCell;
    entitlementLeafHash.inputs[4] <== groupCode;
    entitlementLeafHash.inputs[5] <== category;
    entitlementLeafHash.inputs[6] <== voucherIndex;
    entitlementLeafHash.inputs[7] <== value;
    entitlementLeafHash.inputs[8] <== deploymentContext.out;

    component entitlementMembership = PoseidonMerkleProof(entitlementDepth, TAG_ENTITLEMENT_NODE);
    entitlementMembership.leaf <== entitlementLeafHash.out;
    for (var ei = 0; ei < entitlementDepth; ei++) {
        entitlementMembership.pathElements[ei] <== entitlementPathElements[ei];
        entitlementMembership.pathIndices[ei] <== entitlementPathIndices[ei];
    }
    entitlementMembership.root === entitlementRoot;

    component nullifierHash = Poseidon(9);
    nullifierHash.inputs[0] <== TAG_NULLIFIER;
    nullifierHash.inputs[1] <== secret;
    nullifierHash.inputs[2] <== campaignId;
    nullifierHash.inputs[3] <== epoch;
    nullifierHash.inputs[4] <== riskCell;
    nullifierHash.inputs[5] <== category;
    nullifierHash.inputs[6] <== voucherIndex;
    nullifierHash.inputs[7] <== value;
    nullifierHash.inputs[8] <== deploymentContext.out;
    nullifierHash.out === nullifier;

    fallbackMode * (fallbackMode - 1) === 0;
    component fallbackHash = Poseidon(8);
    fallbackHash.inputs[0] <== TAG_FALLBACK;
    fallbackHash.inputs[1] <== secret;
    fallbackHash.inputs[2] <== campaignId;
    fallbackHash.inputs[3] <== epoch;
    fallbackHash.inputs[4] <== riskCell;
    fallbackHash.inputs[5] <== category;
    fallbackHash.inputs[6] <== fallbackWindow;
    fallbackHash.inputs[7] <== deploymentContext.out;

    // Quorum path publishes zero window/tag. Fallback path proves the exact tag.
    fallbackMode * (fallbackHash.out - fallbackTag) === 0;
    (1 - fallbackMode) * fallbackWindow === 0;
    (1 - fallbackMode) * fallbackTag === 0;
}

component main {public [campaignId, epoch, riskCell, category, value,
    credentialRoot, entitlementRoot, nullifier, fallbackMode,
    fallbackWindow, fallbackTag, chainId, gatewayAddress, settlementAddress,
    headerHash, policyHash, committeeHash]} = AidMeshVoucher(20, 20);
