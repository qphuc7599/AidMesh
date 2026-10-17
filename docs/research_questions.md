# AidMesh research questions

Updated: 2026-09-01.

## Central thesis

AidMesh studies private, risk-bounded multi-site redemption under network partitions
without assuming rollback-resistant beneficiary hardware. Its contribution is the
composition and measured frontier of persist-before-sign quorum authorization,
conserved fallback rights, causal transfer, semantic settlement, and observer-specific
metadata privacy.

## Primary questions

| ID | Question | Main evidence and falsification boundary |
|---|---|---|
| RQ1 | What safety and quantitative completion bounds are possible under partitions? | Lower bound, quorum intersection, rights conservation, at-most-once settlement, event-expanded frontier, safe-reclamation theorem, formal negative controls. A conflicting safe certificate, mint, cap failure, or second settlement credit falsifies the affected claim. |
| RQ2 | When does causal transfer improve the availability--risk frontier, and when does it reverse? | Untouched Asturies primary and complete 2^8 factorial, earlier three-family shared-queue evidence, optimized/equal/robust static and periodic comparators. Universal superiority is rejected; transfer helps only when mismatch reduction exceeds stranding and coordination cost. |
| RQ3 | Does the cross-layer realization preserve the declared authorization boundary? | P1-v2-tagged Groth16, semantic Solidity gateway, trace-to-EVM replay, adversarial controls, TLA+/TLC, Tamarin, SQLite/process recovery, BPv7 check. Any accepted substituted authorization, reused right, invalid proof, or duplicate credit falsifies the affected boundary. |

Metadata privacy is a supporting observer-specific characterization rather than a
co-equal primary question. Ledger batching can enlarge anonymity sets at delay and
bandwidth cost; it does not provide site, gateway, or network unlinkability.

Allocation remains supplementary and does not create another primary research
question.

## Claim classes

- A theorem follows only from its stated assumptions.
- A model-checking or trace invariant is bounded executable evidence, not an
  unbounded implementation proof.
- An empirical effect requires its declared sampling unit and uncertainty interval.
- An engineering target may fail and is not an observed result.
- Governance, field safety, and humanitarian impact require external evidence that
  simulation and cryptography cannot supply.
