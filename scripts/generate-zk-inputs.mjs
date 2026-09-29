import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { buildPoseidon } from "circomlibjs";

const depth = 20;
const poseidon = await buildPoseidon();
const field = poseidon.F;
const hash = (values) => field.toObject(poseidon(values.map(BigInt))).toString();
const tagRegistry = JSON.parse(readFileSync(resolve("zk", "domain_tags.json"), "utf8"));
const tags = Object.fromEntries(
  Object.entries(tagRegistry.tags).map(([name, entry]) => [name, entry.value]),
);
const merkleRoot = (leaf, indices, nodeDomainTag) => {
  let current = leaf;
  for (const index of indices) {
    current =
      index === 0
        ? hash([nodeDomainTag, current, 0])
        : hash([nodeDomainTag, 0, current]);
  }
  return current;
};

const privateFields = {
  secret: "12345678901234567890",
  secretBlinding: "9876543210987654321",
  groupCode: "42",
  quota: "5",
  voucherIndex: "2",
};
const publicFields = {
  campaignId: "1001",
  epoch: "1",
  riskCell: "7",
  category: "3",
  value: "10",
  // Deterministic eth-tester deployment anchors used by the local-EVM
  // conformance fixture: verifier nonce 0, gateway nonce 1, settlement nonce 2.
  chainId: "131277322940537",
  gatewayAddress: "235632950691130629397570742864357571059148313476",
  settlementAddress: "1267616807065886026890900485244985828061334074567",
  headerHash: "5001",
  policyHash: "5002",
  committeeHash: "5003",
};
const deploymentContext = hash([
  tags.deployment_context,
  publicFields.chainId,
  publicFields.gatewayAddress,
  publicFields.settlementAddress,
  publicFields.headerHash,
  publicFields.policyHash,
  publicFields.committeeHash,
]);
const secretCommitment = hash([
  tags.secret_commitment,
  privateFields.secret,
  privateFields.secretBlinding,
]);
const credentialLeaf = hash([
  tags.credential_leaf,
  secretCommitment,
  publicFields.campaignId,
  publicFields.epoch,
  publicFields.riskCell,
  privateFields.groupCode,
  publicFields.category,
  privateFields.quota,
  deploymentContext,
]);
const entitlementLeaf = hash([
  tags.entitlement_leaf,
  publicFields.campaignId,
  publicFields.epoch,
  publicFields.riskCell,
  privateFields.groupCode,
  publicFields.category,
  privateFields.voucherIndex,
  publicFields.value,
  deploymentContext,
]);
const indices = Array(depth).fill(0);
const elements = Array(depth).fill("0");
const common = {
  ...publicFields,
  ...privateFields,
  credentialRoot: merkleRoot(credentialLeaf, indices, tags.credential_merkle_node),
  entitlementRoot: merkleRoot(entitlementLeaf, indices, tags.entitlement_merkle_node),
  nullifier: hash([
    tags.nullifier,
    privateFields.secret,
    publicFields.campaignId,
    publicFields.epoch,
    publicFields.riskCell,
    publicFields.category,
    privateFields.voucherIndex,
    publicFields.value,
    deploymentContext,
  ]),
  credentialPathElements: elements,
  credentialPathIndices: indices,
  entitlementPathElements: elements,
  entitlementPathIndices: indices,
};
const quorum = {
  ...common,
  fallbackMode: "0",
  fallbackWindow: "0",
  fallbackTag: "0",
};
const fallbackWindow = "6";
const fallback = {
  ...common,
  fallbackMode: "1",
  fallbackWindow,
  fallbackTag: hash([
    tags.fallback_tag,
    privateFields.secret,
    publicFields.campaignId,
    publicFields.epoch,
    publicFields.riskCell,
    publicFields.category,
    fallbackWindow,
    deploymentContext,
  ]),
};
const output = resolve("zk", "inputs");
mkdirSync(output, { recursive: true });
writeFileSync(resolve(output, "valid_quorum.json"), JSON.stringify(quorum, null, 2) + "\n");
writeFileSync(resolve(output, "valid_fallback.json"), JSON.stringify(fallback, null, 2) + "\n");
writeFileSync(
  resolve(output, "invalid_changed_value.json"),
  JSON.stringify({ ...quorum, value: "11" }, null, 2) + "\n",
);
