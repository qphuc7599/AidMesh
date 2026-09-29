import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { basename, resolve } from "node:path";
import { buildPoseidon } from "circomlibjs";

const [specArg, outputArg] = process.argv.slice(2);
if (!specArg || !outputArg) {
  throw new Error("usage: node scripts/generate-e2e-inputs.mjs SPEC.json OUTPUT_DIR");
}

const specPath = resolve(specArg);
const output = resolve(outputArg);
const spec = JSON.parse(readFileSync(specPath, "utf8"));
const tagRegistry = JSON.parse(
  readFileSync(resolve("zk", "domain_tags.json"), "utf8"),
);
const tags = Object.fromEntries(
  Object.entries(tagRegistry.tags).map(([name, entry]) => [name, entry.value]),
);
const depth = 20;
const poseidon = await buildPoseidon();
const field = poseidon.F;
const hash = (values) => field.toObject(poseidon(values.map(BigInt))).toString();
const scalar = (label) => {
  const value = BigInt(`0x${createHash("sha256").update(label).digest("hex")}`);
  return (value % (BigInt(field.p.toString()) - 1n) + 1n).toString();
};

const buildTree = (leaves, nodeDomainTag) => {
  const defaults = ["0"];
  for (let level = 0; level < depth; level += 1) {
    defaults.push(hash([nodeDomainTag, defaults[level], defaults[level]]));
  }
  const levels = [new Map(leaves.map((leaf, index) => [index, leaf]))];
  for (let level = 0; level < depth; level += 1) {
    const parents = new Set([...levels[level].keys()].map((index) => Math.floor(index / 2)));
    const next = new Map();
    for (const parent of parents) {
      const left = levels[level].get(parent * 2) ?? defaults[level];
      const right = levels[level].get(parent * 2 + 1) ?? defaults[level];
      next.set(parent, hash([nodeDomainTag, left, right]));
    }
    levels.push(next);
  }
  return {
    root: levels[depth].get(0) ?? defaults[depth],
    proof(index) {
      const elements = [];
      const indices = [];
      let cursor = index;
      for (let level = 0; level < depth; level += 1) {
        elements.push(levels[level].get(cursor ^ 1) ?? defaults[level]);
        indices.push(cursor % 2);
        cursor = Math.floor(cursor / 2);
      }
      return { elements, indices };
    },
  };
};

const campaignId = String(spec.campaignId);
const epoch = String(spec.epoch);
const riskCell = String(spec.riskCell);
const category = String(spec.category);
const value = String(spec.value);
const chainId = String(spec.chainId);
const gatewayAddress = String(spec.gatewayAddress);
const settlementAddress = String(spec.settlementAddress);
const headerHash = String(spec.headerHash);
const policyHash = String(spec.policyHash);
const committeeHash = String(spec.committeeHash);
const deploymentContext = hash([
  tags.deployment_context,
  chainId,
  gatewayAddress,
  settlementAddress,
  headerHash,
  policyHash,
  committeeHash,
]);
const quota = String(spec.vouchers.length + 1);
const vouchers = spec.vouchers.map((voucher, index) => {
  const key = String(voucher.key);
  const secret = scalar(`${spec.registryId}|${spec.context}|${spec.seed}|${key}|secret`);
  const secretBlinding = scalar(
    `${spec.registryId}|${spec.context}|${spec.seed}|${key}|blinding`,
  );
  const groupCode = scalar(`${spec.registryId}|${key}|group`);
  const voucherIndex = String(index + 1);
  const secretCommitment = hash([tags.secret_commitment, secret, secretBlinding]);
  const credentialLeaf = hash([
    tags.credential_leaf,
    secretCommitment,
    campaignId,
    epoch,
    riskCell,
    groupCode,
    category,
    quota,
    deploymentContext,
  ]);
  const entitlementLeaf = hash([
    tags.entitlement_leaf,
    campaignId,
    epoch,
    riskCell,
    groupCode,
    category,
    voucherIndex,
    value,
    deploymentContext,
  ]);
  const nullifier = hash([
    tags.nullifier,
    secret,
    campaignId,
    epoch,
    riskCell,
    category,
    voucherIndex,
    value,
    deploymentContext,
  ]);
  return {
    key,
    secret,
    secretBlinding,
    groupCode,
    voucherIndex,
    credentialLeaf,
    entitlementLeaf,
    nullifier,
  };
});

const credentialTree = buildTree(
  vouchers.map((voucher) => voucher.credentialLeaf),
  tags.credential_merkle_node,
);
const entitlementTree = buildTree(
  vouchers.map((voucher) => voucher.entitlementLeaf),
  tags.entitlement_merkle_node,
);
const vectors = [];
mkdirSync(output, { recursive: true });
for (const [index, voucher] of vouchers.entries()) {
  const credentialPath = credentialTree.proof(index);
  const entitlementPath = entitlementTree.proof(index);
  const common = {
    campaignId,
    epoch,
    riskCell,
    category,
    value,
    chainId,
    gatewayAddress,
    settlementAddress,
    headerHash,
    policyHash,
    committeeHash,
    secret: voucher.secret,
    secretBlinding: voucher.secretBlinding,
    groupCode: voucher.groupCode,
    quota,
    voucherIndex: voucher.voucherIndex,
    credentialRoot: credentialTree.root,
    entitlementRoot: entitlementTree.root,
    nullifier: voucher.nullifier,
    credentialPathElements: credentialPath.elements,
    credentialPathIndices: credentialPath.indices,
    entitlementPathElements: entitlementPath.elements,
    entitlementPathIndices: entitlementPath.indices,
  };
  for (const mode of spec.modes) {
    const fallbackMode = mode === "fallback" ? "1" : "0";
    const fallbackWindow = mode === "fallback" ? String(spec.fallbackWindow) : "0";
    const fallbackTag =
      mode === "fallback"
        ? hash([
            tags.fallback_tag,
            voucher.secret,
            campaignId,
            epoch,
            riskCell,
            category,
            fallbackWindow,
            deploymentContext,
          ])
        : "0";
    const payload = { ...common, fallbackMode, fallbackWindow, fallbackTag };
    const filename = `${String(index).padStart(4, "0")}-${mode}.json`;
    writeFileSync(resolve(output, filename), `${JSON.stringify(payload, null, 2)}\n`);
    vectors.push({
      key: voucher.key,
      mode,
      input: filename,
      nullifier: voucher.nullifier,
      fallbackTag,
      credentialRoot: credentialTree.root,
      entitlementRoot: entitlementTree.root,
    });
  }
}

writeFileSync(
  resolve(output, "input_manifest.json"),
  `${JSON.stringify(
    {
      schemaVersion: 2,
      profile: tagRegistry.profile,
      domainTags: tags,
      generator: basename(import.meta.filename),
      spec: specPath,
      depth,
      campaignId,
      credentialRoot: credentialTree.root,
      entitlementRoot: entitlementTree.root,
      vouchers: vouchers.length,
      vectors,
    },
    null,
    2,
  )}\n`,
);
