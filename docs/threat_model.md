# Threat model and safety domains

## Safety domain

Every voucher, witness request, receipt, and risk right is domain-separated by:

```text
(protocol_id, chain_id, contract_id, campaign_id, epoch, header_version,
 committee_id, entitlement_root, policy_digest)
```

Safety-critical changes create a new epoch.  A header update inside an epoch may only
change non-safety metadata.  Committee handoff is either (a) prohibited until the next
epoch while old nullifier state remains authoritative, or (b) accepted by a joint old
and new quorum.  Merely placing a new committee in a header is invalid.

## Trust profiles

| Profile | Assumptions | Claims allowed |
|---|---|---|
| P0 reference disclosure | Issuer and verifier see entitlement fields; real signatures and Merkle checks | Correctness/performance plumbing only; **no anonymity or ZK claim** |
| P1 primary private | Exact credential/SNARK relation, sound verifier parity, issuer threshold below corruption bound | Qualified cryptographic unlinkability plus all P0 claims |
| P2 verifier adapter | Off-chain verifier/attester is honest and available | Adapter-specific integrity and gas/latency only; does not inherit P1 trust minimization |

## Adversary capabilities

- Coordinates beneficiary snapshot rollback and duplicate presentations.
- Delays, duplicates, reorders, selectively drops, floods, or eclipses DTN bundles.
- Causes bursty partitions, gateway censorship, RPC failures, clock jumps, crash/restart,
  disk rollback, stale headers, and batch reordering.
- Corrupts up to the registered witness bound in a static profile; adaptive/mobile
  corruption is a separate stress profile.
- Observes ledger data and, by vantage, site/cell/category/value/time/size/path metadata.
- May corrupt sites.  A corrupt site can release physical inventory outside the
  protocol; no cryptographic cap covers such organizational loss.
- May collude across issuer, site, witness, gateway, or ledger roles only in explicitly
  labeled stress scenarios.

## Protected properties

1. No two valid quorum certificates for one `(safety_domain, nullifier)` when
   `2q > K + f`, signer identities are distinct, and honest witnesses persist before
   signing.
2. Total outstanding plus spent risk rights is conserved; honest-site duplicate value
   is bounded by the initial right supply for the reconciliation interval.
3. Settlement credits at most one receipt per domain/nullifier.
4. Issued anonymous entitlement value never exceeds the committed allocation for each
   cell/group/category/denomination.
5. P1 presentations provide only the qualified privacy proved by the exact relation;
   metadata leakage is measured separately.

## Explicit exclusions

Field safety, personhood, enrollment correctness, coercion resistance, malware below
the client process, physical observation, ethical correctness of policy weights, and
humanitarian impact are not established by this artifact.

