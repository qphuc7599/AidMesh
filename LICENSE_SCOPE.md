# Licence and fixture scope

AidMesh source retains the existing **Apache-2.0** licence; see `LICENSE` and
`pyproject.toml`. Original generated simulation evidence, synthetic fixtures and
artifact documentation retain the same licence declared in the original research
artifact metadata. This publication does not relicense third-party materials.

Dependencies remain under their upstream licences and are installed from pinned
manifests. Generated Circom/snarkjs witness helpers retain their upstream headers.
Public powers-of-tau material is a referenced cryptographic input; its provenance
remains in the setup records. Development keys and simulated setup transcripts
are research artifacts, not a production multi-party ceremony.

External contact datasets are not included. Their access conditions are described
in `DATA_SOURCES.md` and `docs/legal/`. Synthetic simulator events must not be
mistaken for real beneficiary records or a release of the external contact data.

Synthetic enrollment values, deterministic signing fixtures and development-chain
keys may appear in the artifact. They have no confidential production role.
Account passwords, service tokens and live wallet credentials are excluded.
Never deploy published fixtures with real identities or funds.

The historical source-only Zenodo deposit has its own metadata. This document
does not silently modify that earlier record.
