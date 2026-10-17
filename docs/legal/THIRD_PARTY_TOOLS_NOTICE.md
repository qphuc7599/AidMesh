# Third-party tools and license boundary

The repository's Apache-2.0 license applies to original AidMesh code and
documentation only. It does not relicense third-party compilers, model checkers,
libraries, public datasets, or generated artifacts derived from them.

## Pinned external binaries

| File | Version | SHA-256 | Upstream | License |
|---|---|---|---|---|
| `tools/solc/solc-windows-v0.8.36.exe` | `0.8.36+commit.8a079791` | `38cf5df656da822cf88b854cf736475a196c679f4f5f3f15ddd8c85ce5dbb36a` | https://github.com/argotorg/solidity/releases/tag/v0.8.36 | GPL-3.0; some bundled third-party code has its own terms |
| `tools/solc/solc-static-linux-v0.8.36` | `0.8.36+commit.8a079791` | `c8d35afdddc3cd2743ee88b8f25e0fecd16e2bdd5f2120f37e52cd9cc45ae0e6` | https://github.com/argotorg/solidity/releases/tag/v0.8.36 | GPL-3.0; downloaded by the Linux bootstrap and excluded from release archives |
| `tools/tla2tools.jar` | TLC 2.19, rev. `5a47802` | `936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88` | https://github.com/tlaplus/tlaplus/releases | MIT |

Upstream license sources:

- Solidity: https://github.com/argotorg/solidity/blob/develop/LICENSE.txt
- TLA+ tools: https://github.com/tlaplus/tlaplus/blob/master/LICENSE

The anonymous source archive does **not** redistribute these binaries. Run
`scripts/Install-PinnedThirdPartyTools.ps1` to fetch their exact official release
assets and reject any SHA-256 mismatch. This keeps the artifact license boundary
clear while preserving bit-level compiler/model-checker identity. Local development
copies under `tools/` are excluded from the release ZIP.

## Lockfile-resolved cryptographic tools

The public source archive includes dependency lockfiles, not `node_modules`.

| Package/tool | Frozen version | Upstream | License |
|---|---:|---|---|
| `snarkjs` | 0.7.6 | https://github.com/iden3/snarkjs | GPL-3.0 |
| `circomlib` | 2.0.5 | https://github.com/iden3/circomlib | GPL-3.0 |
| Circom compiler | 2.2.3 | https://github.com/iden3/circom | GPL-3.0 |

Generated R1CS, WASM, verification key, Solidity verifier, and proof files are
distributed as research artifacts. Their presence does not change the upstream tool
licenses.

## Public contact traces

SocioPatterns and UMass DieselNet data are not relicensed by AidMesh. Their
provenance, acquisition URL, original terms, citation, and local source checksum are
documented separately in:

- `docs/legal/SOCIOPATTERNS_LICENSE_PROVENANCE.md`;
- `docs/legal/UMASS_DIESELNET_LICENSE_PROVENANCE.md`.

The release builder does not automatically redistribute those public trace inputs.
