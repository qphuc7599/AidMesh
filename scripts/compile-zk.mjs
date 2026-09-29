import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve } from "node:path";
import { spawnSync } from "node:child_process";

const executable = process.env.CIRCOM2_BIN || join(
  homedir(),
  ".cargo",
  "bin",
  process.platform === "win32" ? "circom.exe" : "circom",
);
const output = resolve("zk", "build");
mkdirSync(output, { recursive: true });
const args = [
  resolve("zk", "circuits", "aidmesh_voucher.circom"),
  "--r1cs",
  "--wasm",
  "--sym",
  "--inspect",
  "-l",
  resolve("node_modules"),
  "-o",
  output,
];
const result = spawnSync(executable, args, { stdio: "inherit" });
if (result.error) throw result.error;
process.exit(result.status ?? 1);
