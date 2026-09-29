---------------------------- MODULE AidMeshProtocol ----------------------------
EXTENDS FiniteSets, Integers, TLC

CONSTANTS Witnesses, Byzantine, Q, Epochs, Epoch0, Epoch1, Sites,
          RightTokens, Nullifiers, TransferIds, RollbackEnabled,
          UnsafeSettlement, UnsafeConservation, CheckMode

Digests == {"digest-A", "digest-B"}
NoDecision == "NO_DECISION"
NoSite == "NO_SITE"
NoToken == "NO_TOKEN"
Statuses == {"remaining", "in_transit", "spent", "expired", "revoked"}
TransferStatuses == {"none", "in_transit", "accepted", "expired", "revoked"}
Honest == Witnesses \ Byzantine

VARIABLES activeEpoch, durable, up, shares, certified,
          rightState, rightOwner, transfers, fallbackReleases, credits

vars == <<activeEpoch, durable, up, shares, certified,
          rightState, rightOwner, transfers, fallbackReleases, credits>>

DecisionValues == Digests \cup {NoDecision}
TransferEntry == [status : TransferStatuses,
                  token : RightTokens \cup {NoToken},
                  source : Sites \cup {NoSite},
                  destination : Sites \cup {NoSite}]

TypeOK ==
    /\ CheckMode \in {"quorum", "rights", "settlement"}
    /\ activeEpoch \in Epochs
    /\ durable \in [Witnesses -> [Epochs -> [Nullifiers -> DecisionValues]]]
    /\ up \in [Witnesses -> BOOLEAN]
    /\ shares \subseteq (Witnesses \X Epochs \X Nullifiers \X Digests)
    /\ certified \subseteq (Epochs \X Nullifiers \X Digests)
    /\ rightState \in [RightTokens -> Statuses]
    /\ rightOwner \in [RightTokens -> Sites \cup {NoSite}]
    /\ transfers \in [TransferIds -> TransferEntry]
    /\ fallbackReleases \subseteq (RightTokens \X Nullifiers)
    /\ credits \in [Epochs -> [Nullifiers -> Nat]]

Init ==
    /\ activeEpoch = Epoch0
    /\ durable = [w \in Witnesses |->
            [e \in Epochs |-> [n \in Nullifiers |-> NoDecision]]]
    /\ up = [w \in Witnesses |-> TRUE]
    /\ shares = {}
    /\ certified = {}
    /\ rightState = [r \in RightTokens |-> "remaining"]
    /\ rightOwner = [r \in RightTokens |-> CHOOSE s \in Sites : TRUE]
    /\ transfers = [t \in TransferIds |->
            [status |-> "none", token |-> NoToken,
             source |-> NoSite, destination |-> NoSite]]
    /\ fallbackReleases = {}
    /\ credits = [e \in Epochs |-> [n \in Nullifiers |-> 0]]

Persist(w, e, n, d) ==
    /\ w \in Honest
    /\ up[w]
    /\ e = activeEpoch
    /\ durable[w][e][n] \in {NoDecision, d}
    /\ durable' = [durable EXCEPT ![w][e][n] = d]
    /\ UNCHANGED <<activeEpoch, up, shares, certified, rightState,
                    rightOwner, transfers, fallbackReleases, credits>>

SignHonest(w, e, n, d) ==
    /\ w \in Honest
    /\ up[w]
    /\ e = activeEpoch
    /\ durable[w][e][n] = d
    /\ shares' = shares \cup {<<w, e, n, d>>}
    /\ UNCHANGED <<activeEpoch, durable, up, certified, rightState,
                    rightOwner, transfers, fallbackReleases, credits>>

SignByzantine(w, e, n, d) ==
    /\ w \in Byzantine
    /\ up[w]
    /\ e = activeEpoch
    /\ shares' = shares \cup {<<w, e, n, d>>}
    /\ UNCHANGED <<activeEpoch, durable, up, certified, rightState,
                    rightOwner, transfers, fallbackReleases, credits>>

Certify(e, n, d) ==
    /\ e = activeEpoch
    /\ Cardinality({w \in Witnesses : <<w, e, n, d>> \in shares}) >= Q
    /\ certified' = certified \cup {<<e, n, d>>}
    /\ UNCHANGED <<activeEpoch, durable, up, shares, rightState,
                    rightOwner, transfers, fallbackReleases, credits>>

Crash(w) ==
    /\ up[w]
    /\ up' = [up EXCEPT ![w] = FALSE]
    /\ UNCHANGED <<activeEpoch, durable, shares, certified, rightState,
                    rightOwner, transfers, fallbackReleases, credits>>

Restart(w) ==
    /\ ~up[w]
    /\ up' = [up EXCEPT ![w] = TRUE]
    /\ UNCHANGED <<activeEpoch, durable, shares, certified, rightState,
                    rightOwner, transfers, fallbackReleases, credits>>

Rollback(w, e, n) ==
    /\ RollbackEnabled
    /\ w \in Honest
    /\ durable[w][e][n] # NoDecision
    /\ durable' = [durable EXCEPT ![w][e][n] = NoDecision]
    /\ UNCHANGED <<activeEpoch, up, shares, certified, rightState,
                    rightOwner, transfers, fallbackReleases, credits>>

BeginTransfer(t, r, source, destination) ==
    /\ transfers[t].status = "none"
    /\ rightState[r] = "remaining"
    /\ rightOwner[r] = source
    /\ source # destination
    /\ rightState' = [rightState EXCEPT ![r] = "in_transit"]
    /\ rightOwner' = [rightOwner EXCEPT ![r] = NoSite]
    /\ transfers' = [transfers EXCEPT ![t] =
            [status |-> "in_transit", token |-> r,
             source |-> source, destination |-> destination]]
    /\ UNCHANGED <<activeEpoch, durable, up, shares, certified,
                    fallbackReleases, credits>>

AcceptTransfer(t) ==
    LET r == transfers[t].token IN
    /\ transfers[t].status = "in_transit"
    /\ rightState[r] = "in_transit"
    /\ rightState' = [rightState EXCEPT ![r] = "remaining"]
    /\ rightOwner' = [rightOwner EXCEPT ![r] = transfers[t].destination]
    /\ transfers' = [transfers EXCEPT ![t].status = "accepted"]
    /\ UNCHANGED <<activeEpoch, durable, up, shares, certified,
                    fallbackReleases, credits>>

TerminateTransfer(t, terminal) ==
    LET r == transfers[t].token IN
    /\ terminal \in {"expired", "revoked"}
    /\ transfers[t].status = "in_transit"
    /\ rightState[r] = "in_transit"
    /\ rightState' = [rightState EXCEPT ![r] = terminal]
    /\ transfers' = [transfers EXCEPT ![t].status = terminal]
    /\ UNCHANGED <<activeEpoch, durable, up, shares, certified,
                    rightOwner, fallbackReleases, credits>>

SpendFallback(r, n, site) ==
    /\ rightState[r] = "remaining"
    /\ rightOwner[r] = site
    /\ rightState' = [rightState EXCEPT ![r] = "spent"]
    /\ rightOwner' = [rightOwner EXCEPT ![r] = NoSite]
    /\ fallbackReleases' = fallbackReleases \cup {<<r, n>>}
    /\ UNCHANGED <<activeEpoch, durable, up, shares, certified,
                    transfers, credits>>

UnsafeFallbackRelease(r, n) ==
    /\ UnsafeConservation
    /\ rightState[r] = "remaining"
    /\ fallbackReleases' = fallbackReleases \cup {<<r, n>>}
    /\ UNCHANGED <<activeEpoch, durable, up, shares, certified,
                    rightState, rightOwner, transfers, credits>>

Settle(e, n) ==
    /\ e = activeEpoch
    /\ UnsafeSettlement \/ credits[e][n] = 0
    /\ credits' = [credits EXCEPT ![e][n] = @ + 1]
    /\ UNCHANGED <<activeEpoch, durable, up, shares, certified,
                    rightState, rightOwner, transfers, fallbackReleases>>

EpochHandoff ==
    /\ activeEpoch = Epoch0
    /\ \A t \in TransferIds : transfers[t].status # "in_transit"
    /\ activeEpoch' = Epoch1
    /\ UNCHANGED <<durable, up, shares, certified, rightState,
                    rightOwner, transfers, fallbackReleases, credits>>

QuorumNext ==
    \/ \E w \in Witnesses, e \in Epochs, n \in Nullifiers, d \in Digests :
            Persist(w, e, n, d)
    \/ \E w \in Witnesses, e \in Epochs, n \in Nullifiers, d \in Digests :
            SignHonest(w, e, n, d)
    \/ \E w \in Witnesses, e \in Epochs, n \in Nullifiers, d \in Digests :
            SignByzantine(w, e, n, d)
    \/ \E e \in Epochs, n \in Nullifiers, d \in Digests : Certify(e, n, d)
    \/ \E w \in Witnesses : Crash(w)
    \/ \E w \in Witnesses : Restart(w)
    \/ \E w \in Witnesses, e \in Epochs, n \in Nullifiers : Rollback(w, e, n)
    \/ EpochHandoff

RightsNext ==
    \/ \E t \in TransferIds, r \in RightTokens, s \in Sites, d \in Sites :
            BeginTransfer(t, r, s, d)
    \/ \E t \in TransferIds : AcceptTransfer(t)
    \/ \E t \in TransferIds, terminal \in {"expired", "revoked"} :
            TerminateTransfer(t, terminal)
    \/ \E r \in RightTokens, n \in Nullifiers, s \in Sites :
            SpendFallback(r, n, s)
    \/ \E r \in RightTokens, n \in Nullifiers : UnsafeFallbackRelease(r, n)

SettlementNext ==
    \/ \E e \in Epochs, n \in Nullifiers : Settle(e, n)
    \/ EpochHandoff

Next ==
    \/ /\ CheckMode = "quorum"
       /\ QuorumNext
    \/ /\ CheckMode = "rights"
       /\ RightsNext
    \/ /\ CheckMode = "settlement"
       /\ SettlementNext

NoConflictingCertificates ==
    \A e \in Epochs, n \in Nullifiers :
        Cardinality({d \in Digests : <<e, n, d>> \in certified}) <= 1

RiskRightsConserved ==
    Cardinality({r \in RightTokens : rightState[r] = "remaining"}) +
    Cardinality({r \in RightTokens : rightState[r] = "in_transit"}) +
    Cardinality({r \in RightTokens : rightState[r] = "spent"}) +
    Cardinality({r \in RightTokens : rightState[r] = "expired"}) +
    Cardinality({r \in RightTokens : rightState[r] = "revoked"})
        = Cardinality(RightTokens)

RightOwnershipConsistent ==
    \A r \in RightTokens :
        (rightState[r] = "remaining") <=> (rightOwner[r] \in Sites)

TransferTokenUnique ==
    \A t1, t2 \in TransferIds :
        /\ t1 # t2
        /\ transfers[t1].status = "in_transit"
        /\ transfers[t2].status = "in_transit"
        => transfers[t1].token # transfers[t2].token

RiskReleaseAccounting ==
    Cardinality(fallbackReleases) =
        Cardinality({r \in RightTokens : rightState[r] = "spent"})

AtMostOnceSettlement ==
    \A e \in Epochs, n \in Nullifiers : credits[e][n] <= 1

PersistBeforeSign ==
    \A w \in Honest, e \in Epochs, n \in Nullifiers, d \in Digests :
        <<w, e, n, d>> \in shares => durable[w][e][n] = d

EpochIsolation ==
    \A w \in Honest, e \in Epochs, n \in Nullifiers :
        Cardinality({d \in Digests : <<w, e, n, d>> \in shares}) <= 1

Spec == Init /\ [][Next]_vars
=============================================================================
