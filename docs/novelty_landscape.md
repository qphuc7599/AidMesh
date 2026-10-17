# Novelty landscape and positioning freeze

This file prevents the paper from claiming novelty already established by adjacent
work.  It should be refreshed immediately before submission.

| Work | What it already establishes | Open limitation relevant to AidMesh |
|---|---|---|
| Wang et al., IEEE S&P 2023 | Private humanitarian distribution, entitlement tags, audit, smart-card and phone prototypes | Flexible multi-site redemption requires synchronized seen-tags; disconnected operation falls back to fixed-station assignment |
| Janus, IEEE S&P 2024 | Privacy-preserving biometric enrollment deduplication | Enrollment is orthogonal to partitioned redemption |
| Luvison et al., IEEE S&P 2025 | Private humanitarian wallet and multi-vendor spending | Does not make AidMesh's disconnected multi-site risk frontier redundant; must be a direct baseline/assumption comparison |
| xDup, IEEE S&P 2026 | Cross-organization privacy-preserving beneficiary deduplication | Solves cross-org enrollment matching, not offline redemption freshness |
| Coconut | Threshold selective-disclosure credentials | Does not by itself prove the AidMesh PRF + indexed Merkle + quota relation |
| Byzantine quorums | Intersection safety | The inequality is not standalone novelty |
| Bounded Counter CRDT | Conservation of distributed decrement rights | Static site caps are not standalone novelty; transferable rights and private redemption composition are the research contribution |
| RAPID / DTN literature | Utility/deadline-aware forwarding | A linear priority is not standalone novelty; attribution requires equal-resource baselines |

## Frozen primary claim

> AidMesh enables flexible private redemption across distribution sites that cannot
> synchronize, using an explicit safe quorum path and conserved risk rights to expose
> and measure the availability-duplicate-risk-privacy frontier without assuming trusted
> beneficiary hardware.

## Claims prohibited without new evidence

- First anonymous or blockchain humanitarian voucher.
- Global offline double-spend prevention.
- Anonymous operation without metadata qualification.
- General humanitarian fairness from the static packing allocator.
- Lower energy or environmental impact inferred from bytes, gas, or CPU time.

## Primary sources

- Wang et al. (2023): <https://doi.org/10.1109/SP46215.2023.10179306>
- Janus (2024): <https://cispa.de/en/research/publications/78580-janus-safe-biometric-deduplication-for-humanitarian-aid-distribution>
- Luvison et al. (2025): <https://doi.org/10.1109/SP61157.2025.00069>
- xDup (2026): <https://arxiv.org/abs/2604.08019>
- Bounded Counter CRDT: <https://arxiv.org/abs/1503.09052>
- Bundle Protocol v7 / BPSec: <https://www.rfc-editor.org/rfc/rfc9171.html> and <https://www.rfc-editor.org/rfc/rfc9172.html>

