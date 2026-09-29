--------------------------- MODULE AidMeshIntegrated ---------------------------
EXTENDS FiniteSets, Integers, TLC

(*
Composed bounded model for one end-to-end redemption:
 domain-bound proof verification -> durable-witness quorum OR conserved-right debit
 -> physical release -> epoch-bound receipt -> proof/domain-gated gateway
 -> at-most-once settlement.

The model is intentionally small enough for exhaustive TLC exploration. The
explicit SimulationRelation at the bottom maps concrete protocol variables to
the abstract three-stage machine Authorized -> Released -> Settled.
*)

CONSTANTS Witnesses, Byzantine, Q, Epochs, Epoch0, Epoch1, Sites, Digests,
          RightTokens, Nullifiers, UnsafeBypass, UnsafeConservation,
          UnsafeSettlement

NoDecision == "NO_DECISION"
NoAuth == "NO_AUTH"
NoSite == "NO_SITE"
NoEpoch == "NO_EPOCH"
NoNullifier == "NO_NULLIFIER"
NoSpend == <<NoEpoch, NoNullifier>>
SpendTargets == (Epochs \X Nullifiers) \cup {NoSpend}
Honest == Witnesses \ Byzantine
AuthModes == {NoAuth, "quorum", "fallback", "both"}

VARIABLES activeEpoch, durable, shares, certified, proofOK,
          rightState, rightOwner, rightSpend, authorization, releases,
          receiptEpoch, gatewayAccepted, credits

vars == <<activeEpoch, durable, shares, certified, proofOK,
          rightState, rightOwner, rightSpend, authorization, releases,
          receiptEpoch, gatewayAccepted, credits>>

TypeOK ==
    /\ activeEpoch \in Epochs
    /\ durable \in [Witnesses -> [Epochs -> [Nullifiers ->
            Digests \cup {NoDecision}]]]
    /\ shares \subseteq (Witnesses \X Epochs \X Nullifiers \X Digests)
    /\ certified \subseteq (Epochs \X Nullifiers \X Digests)
    /\ proofOK \in [Epochs -> [Nullifiers -> BOOLEAN]]
    /\ rightState \in [RightTokens -> {"remaining", "spent"}]
    /\ rightOwner \in [RightTokens -> Sites \cup {NoSite}]
    /\ rightSpend \in [RightTokens -> SpendTargets]
    /\ authorization \in [Epochs -> [Nullifiers -> AuthModes]]
    /\ releases \in [Epochs -> [Nullifiers -> Nat]]
    /\ receiptEpoch \in [Epochs -> [Nullifiers -> Epochs \cup {NoEpoch}]]
    /\ gatewayAccepted \in [Epochs -> [Nullifiers -> BOOLEAN]]
    /\ credits \in [Epochs -> [Nullifiers -> Nat]]

Init ==
    /\ activeEpoch = Epoch0
    /\ durable = [w \in Witnesses |->
            [e \in Epochs |-> [n \in Nullifiers |-> NoDecision]]]
    /\ shares = {}
    /\ certified = {}
    /\ proofOK = [e \in Epochs |-> [n \in Nullifiers |-> FALSE]]
    /\ rightState = [r \in RightTokens |-> "remaining"]
    /\ rightOwner = [r \in RightTokens |-> CHOOSE s \in Sites : TRUE]
    /\ rightSpend = [r \in RightTokens |-> NoSpend]
    /\ authorization = [e \in Epochs |-> [n \in Nullifiers |-> NoAuth]]
    /\ releases = [e \in Epochs |-> [n \in Nullifiers |-> 0]]
    /\ receiptEpoch = [e \in Epochs |-> [n \in Nullifiers |-> NoEpoch]]
    /\ gatewayAccepted = [e \in Epochs |-> [n \in Nullifiers |-> FALSE]]
    /\ credits = [e \in Epochs |-> [n \in Nullifiers |-> 0]]

Persist(w, n, d) ==
    /\ w \in Honest
    /\ durable[w][activeEpoch][n] \in {NoDecision, d}
    /\ durable' = [durable EXCEPT ![w][activeEpoch][n] = d]
    /\ UNCHANGED <<activeEpoch, shares, certified, proofOK, rightState,
                    rightOwner, rightSpend, authorization, releases, receiptEpoch,
                    gatewayAccepted, credits>>

SignHonest(w, n, d) ==
    /\ w \in Honest
    /\ durable[w][activeEpoch][n] = d
    /\ shares' = shares \cup {<<w, activeEpoch, n, d>>}
    /\ UNCHANGED <<activeEpoch, durable, certified, proofOK, rightState,
                    rightOwner, rightSpend, authorization, releases, receiptEpoch,
                    gatewayAccepted, credits>>

SignByzantine(w, n, d) ==
    /\ w \in Byzantine
    /\ shares' = shares \cup {<<w, activeEpoch, n, d>>}
    /\ UNCHANGED <<activeEpoch, durable, certified, proofOK, rightState,
                    rightOwner, rightSpend, authorization, releases, receiptEpoch,
                    gatewayAccepted, credits>>

Certify(n, d) ==
    /\ Cardinality({w \in Witnesses :
            <<w, activeEpoch, n, d>> \in shares}) >= Q
    /\ certified' = certified \cup {<<activeEpoch, n, d>>}
    /\ UNCHANGED <<activeEpoch, durable, shares, proofOK, rightState,
                    rightOwner, rightSpend, authorization, releases, receiptEpoch,
                    gatewayAccepted, credits>>

VerifyDomainBoundProof(n) ==
    /\ ~proofOK[activeEpoch][n]
    /\ proofOK' = [proofOK EXCEPT ![activeEpoch][n] = TRUE]
    /\ UNCHANGED <<activeEpoch, durable, shares, certified, rightState,
                    rightOwner, rightSpend, authorization, releases, receiptEpoch,
                    gatewayAccepted, credits>>

AuthorizeQuorum(n, d) ==
    /\ proofOK[activeEpoch][n]
    /\ <<activeEpoch, n, d>> \in certified
    /\ authorization[activeEpoch][n] \in {NoAuth, "fallback"}
    /\ authorization' =
            [authorization EXCEPT ![activeEpoch][n] =
                IF @ = "fallback" THEN "both" ELSE "quorum"]
    /\ UNCHANGED <<activeEpoch, durable, shares, certified, proofOK,
                    rightState, rightOwner, rightSpend, releases, receiptEpoch,
                    gatewayAccepted, credits>>

AuthorizeFallback(r, n, site) ==
    /\ proofOK[activeEpoch][n]
    /\ rightState[r] = "remaining"
    /\ rightOwner[r] = site
    /\ rightState' = [rightState EXCEPT ![r] = "spent"]
    /\ rightOwner' = [rightOwner EXCEPT ![r] = NoSite]
    /\ rightSpend' = [rightSpend EXCEPT ![r] = <<activeEpoch, n>>]
    /\ authorization' =
            [authorization EXCEPT ![activeEpoch][n] =
                IF @ \in {"quorum", "both"} THEN "both" ELSE "fallback"]
    /\ UNCHANGED <<activeEpoch, durable, shares, certified, proofOK,
                    releases, receiptEpoch, gatewayAccepted, credits>>

UnsafeFallback(n) ==
    /\ UnsafeConservation
    /\ authorization' =
            [authorization EXCEPT ![activeEpoch][n] =
                IF @ \in {"quorum", "both"} THEN "both" ELSE "fallback"]
    /\ UNCHANGED <<activeEpoch, durable, shares, certified, proofOK,
                    rightState, rightOwner, rightSpend, releases, receiptEpoch,
                    gatewayAccepted, credits>>

FallbackDebitCount(e, n) ==
    Cardinality({r \in RightTokens : rightSpend[r] = <<e, n>>})

QuorumCapacity(e, n) ==
    IF authorization[e][n] \in {"quorum", "both"} THEN 1 ELSE 0

ReleaseCapacity(e, n) ==
    FallbackDebitCount(e, n) + QuorumCapacity(e, n)

Release(n) ==
    /\ authorization[activeEpoch][n] # NoAuth
    /\ \/ UnsafeConservation
       \/ releases[activeEpoch][n] < ReleaseCapacity(activeEpoch, n)
    /\ releases' = [releases EXCEPT ![activeEpoch][n] = @ + 1]
    /\ receiptEpoch' =
            [receiptEpoch EXCEPT ![activeEpoch][n] = activeEpoch]
    /\ UNCHANGED <<activeEpoch, durable, shares, certified, proofOK,
                    rightState, rightOwner, rightSpend, authorization,
                    gatewayAccepted, credits>>

GatewayAccept(n) ==
    /\ proofOK[activeEpoch][n]
    /\ releases[activeEpoch][n] >= 1
    /\ receiptEpoch[activeEpoch][n] = activeEpoch
    /\ ~gatewayAccepted[activeEpoch][n]
    /\ gatewayAccepted' =
            [gatewayAccepted EXCEPT ![activeEpoch][n] = TRUE]
    /\ UNCHANGED <<activeEpoch, durable, shares, certified, proofOK,
                    rightState, rightOwner, rightSpend, authorization, releases,
                    receiptEpoch, credits>>

Settle(n) ==
    /\ UnsafeSettlement \/ gatewayAccepted[activeEpoch][n]
    /\ UnsafeSettlement \/ credits[activeEpoch][n] = 0
    /\ credits' = [credits EXCEPT ![activeEpoch][n] = @ + 1]
    /\ UNCHANGED <<activeEpoch, durable, shares, certified, proofOK,
                    rightState, rightOwner, rightSpend, authorization, releases,
                    receiptEpoch, gatewayAccepted>>

BypassSettle(n) ==
    /\ UnsafeBypass
    /\ credits' = [credits EXCEPT ![activeEpoch][n] = @ + 1]
    /\ UNCHANGED <<activeEpoch, durable, shares, certified, proofOK,
                    rightState, rightOwner, rightSpend, authorization, releases,
                    receiptEpoch, gatewayAccepted>>

EpochHandoff ==
    /\ activeEpoch = Epoch0
    /\ \A n \in Nullifiers :
            \/ /\ releases[Epoch0][n] = 0
               /\ credits[Epoch0][n] = 0
            \/ /\ releases[Epoch0][n] > 0
               /\ credits[Epoch0][n] = 1
    /\ activeEpoch' = Epoch1
    /\ UNCHANGED <<durable, shares, certified, proofOK, rightState,
                    rightOwner, rightSpend, authorization, releases, receiptEpoch,
                    gatewayAccepted, credits>>

Next ==
    \/ \E w \in Witnesses, n \in Nullifiers, d \in Digests :
            Persist(w, n, d)
    \/ \E w \in Witnesses, n \in Nullifiers, d \in Digests :
            SignHonest(w, n, d)
    \/ \E w \in Witnesses, n \in Nullifiers, d \in Digests :
            SignByzantine(w, n, d)
    \/ \E n \in Nullifiers, d \in Digests : Certify(n, d)
    \/ \E n \in Nullifiers : VerifyDomainBoundProof(n)
    \/ \E n \in Nullifiers, d \in Digests : AuthorizeQuorum(n, d)
    \/ \E r \in RightTokens, n \in Nullifiers, s \in Sites :
            AuthorizeFallback(r, n, s)
    \/ \E n \in Nullifiers : UnsafeFallback(n)
    \/ \E n \in Nullifiers : Release(n)
    \/ \E n \in Nullifiers : GatewayAccept(n)
    \/ \E n \in Nullifiers : Settle(n)
    \/ \E n \in Nullifiers : BypassSettle(n)
    \/ EpochHandoff

NoConflictingCertificates ==
    \A e \in Epochs, n \in Nullifiers :
        Cardinality({d \in Digests : <<e, n, d>> \in certified}) <= 1

QuorumAtMostOnceRelease ==
    \A e \in Epochs, n \in Nullifiers :
        releases[e][n] <= FallbackDebitCount(e, n) + 1

AtMostOnceSettlement ==
    \A e \in Epochs, n \in Nullifiers : credits[e][n] <= 1

ReleaseRequiresAuthorizationAndProof ==
    \A e \in Epochs, n \in Nullifiers :
        releases[e][n] > 0 =>
            /\ authorization[e][n] # NoAuth
            /\ proofOK[e][n]

SettlementRequiresRelease ==
    \A e \in Epochs, n \in Nullifiers :
        credits[e][n] > 0 =>
            /\ releases[e][n] >= 1
            /\ gatewayAccepted[e][n]
            /\ proofOK[e][n]
            /\ receiptEpoch[e][n] = e

FallbackConservation ==
    \A e \in Epochs, n \in Nullifiers :
        releases[e][n] <= ReleaseCapacity(e, n)

AbstractAuthorized(e, n) == authorization[e][n] # NoAuth
AbstractReleased(e, n) == releases[e][n] >= 1
AbstractSettled(e, n) == credits[e][n] = 1

SimulationRelation ==
    \A e \in Epochs, n \in Nullifiers :
        /\ AbstractReleased(e, n) => AbstractAuthorized(e, n)
        /\ AbstractSettled(e, n) => AbstractReleased(e, n)
        /\ AbstractSettled(e, n) => gatewayAccepted[e][n]

Spec == Init /\ [][Next]_vars
=============================================================================
