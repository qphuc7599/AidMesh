# Tamarin model scope

AidMeshSemanticAuthorization.spthy is an unbounded-session symbolic model of
the semantic authorization core. It models K=4, q=3, f=1 by enumerating the
three possible pairs of honest witnesses that every valid quorum must contain.
Linear WReady state captures durable persist-before-sign; linear right and
settlement state captures unique ownership, crash/recovery without rollback,
and first-valid-wins settlement.

The model intentionally abstracts ECDSA/Groth16 soundness and the numeric risk
cap. Those obligations are covered by the generated verifier/contract tests
and the quantitative/TLA+ models, respectively. A successful Tamarin run is
therefore evidence for unbounded concurrency, replay-domain separation, quorum
non-conflict, right non-forking, and single settlement under the stated
K=4/q=3/f=1 and no-durable-rollback assumptions; it is not a proof of the EVM
bytecode or cryptographic primitives.
