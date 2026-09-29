/** Repeated sequential Groth16 verification for the no-ledger reconciliation ablation. */
import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { performance } from "node:perf_hooks";
import { groth16 } from "snarkjs";

const [source, output, trialText = "30"] = process.argv.slice(2);
if (!source || !output) throw new Error("source and output are required");
const trials = Number(trialText);
if (!Number.isInteger(trials) || trials < 1) throw new Error("trials must be a positive integer");

const proofBytes = readFileSync(`${source}/fresh_proofs.json`);
const verificationKeyBytes = readFileSync("zk/build_v3/verification_key.json");
const rows = JSON.parse(proofBytes);
const verificationKey = JSON.parse(verificationKeyBytes);
if (rows.length !== 100) throw new Error(`expected 100 proofs, found ${rows.length}`);

// A real proof warms module initialization and pairing tables outside the trials.
if (!(await groth16.verify(verificationKey, rows[0].publicSignals, rows[0].proof))) {
  throw new Error("warm-up proof failed");
}

const measurements = [];
for (let trial = 0; trial < trials; trial += 1) {
  const start = performance.now();
  for (let index = 0; index < rows.length; index += 1) {
    const row = rows[index];
    if (!(await groth16.verify(verificationKey, row.publicSignals, row.proof))) {
      throw new Error(`invalid proof ${index} in trial ${trial}`);
    }
  }
  measurements.push({ trial, p1BatchVerificationMs: performance.now() - start });
}

const sha256 = (value) => createHash("sha256").update(value).digest("hex");
const result = {
  profile: "sequential verification of the exact final 100 P1-v3 proofs",
  trials,
  proofsPerTrial: rows.length,
  allValid: true,
  warmup: "one valid proof excluded",
  proofInputSha256: sha256(proofBytes),
  verificationKeySha256: sha256(verificationKeyBytes),
  measurements,
};
writeFileSync(output, JSON.stringify(result, null, 2));
console.log(JSON.stringify({ trials, proofsPerTrial: rows.length, allValid: true }));
process.exit(0);
