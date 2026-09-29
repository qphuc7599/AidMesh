import { randomBytes } from "node:crypto";
import { writeSync } from "node:fs";
import { zKey } from "snarkjs";

const [inputPath, outputPath, identity] = process.argv.slice(2);
if (!inputPath || !outputPath || !/^sim-node-[1-5]$/.test(identity ?? "")) {
  throw new Error("usage: contribute.mjs INPUT OUTPUT sim-node-[1-5]");
}

// Entropy exists only in this short-lived container process. It is never passed
// on the command line, written to a file, or emitted by the logger.
const entropyBytes = randomBytes(64);
let entropy = entropyBytes.toString("hex");
let exitCode = 0;
try {
  const contributionHash = await zKey.contribute(
    inputPath,
    outputPath,
    identity,
    entropy,
  );
  writeSync(process.stdout.fd, `${JSON.stringify({
    identity,
    contribution_hash: Buffer.from(contributionHash).toString("hex"),
    entropy_source: "node:crypto.randomBytes(64)",
    entropy_logged: false,
    entropy_persisted: false,
  })}\n`);
} catch (error) {
  exitCode = 1;
  writeSync(process.stderr.fd, `contribution failed: ${error.message}\n`);
} finally {
  entropyBytes.fill(0);
  entropy = "";
}
process.exit(exitCode);
