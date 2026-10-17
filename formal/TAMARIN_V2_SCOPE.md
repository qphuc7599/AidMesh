# AidMesh protocol-V2 Tamarin scope

`AidMeshV2AuthorizationAndTerminal.spthy` is the symbolic model used by the
Elsevier hybrid revision. It supersedes the earlier symbolic model for V2 claims
without modifying or deleting that historical model.

The model binds witness decisions to the full `AuthorizationIntentV1` tuple:
domain, epoch, nullifier, proof hash, site, value, release-intent identifier, and
receipt-skeleton hash. Linear witness-ready facts represent durable
persist-before-sign state. Linear release-pending state represents the single
physical handoff invocation, including the fail-closed ambiguous outcome.

For terminal right transfer, the model places a right in a linear in-transit
state. The same K=4, q=3, f=1 honest-intersection abstraction certifies either
`accept` or `reclaim`; the in-transit state permits only one terminal application.

Verified lemmas cover four executable paths and eight safety properties:

- authorized release success and ambiguous-lock executability;
- terminal accept and reclaim executability;
- honest authorization-intent non-equivocation;
- authorization-certificate non-conflict;
- physical handoff at most once;
- terminal-certificate non-conflict and terminal-application exclusivity;
- settlement first-valid-wins;
- risk-right single spend; and
- cross-domain receipt separation.

This is an unbounded-session symbolic proof under perfect-cryptography and honest
durable-state assumptions. It does not prove the Python or Solidity bytecode,
Groth16 arithmetic, SQLite/filesystem firmware, device identity, actuator
behavior, or that goods physically reached a beneficiary. Those obligations are
covered only by the separate implementation, test, and bounded semantic artifacts
described in the paper.
