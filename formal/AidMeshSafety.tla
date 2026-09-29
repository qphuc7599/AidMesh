----------------------------- MODULE AidMeshSafety -----------------------------
EXTENDS FiniteSets, Sequences, TLC, Integers

CONSTANTS Witnesses, Byzantine, Q, Sites, RightTokens, Nullifiers

Digests == {"digest-A", "digest-B"}
NoOwner == "SPENT"
Owners == Sites \cup {NoOwner}

VARIABLES signed, certified, owner, settled

TypeOK ==
    /\ signed \in [Witnesses -> SUBSET Digests]
    /\ certified \subseteq Digests
    /\ owner \in [RightTokens -> Owners]
    /\ settled \subseteq Nullifiers

Honest == Witnesses \ Byzantine

Init ==
    /\ signed = [w \in Witnesses |-> {}]
    /\ certified = {}
    /\ owner = [r \in RightTokens |-> CHOOSE s \in Sites : TRUE]
    /\ settled = {}

Sign(w, d) ==
    /\ w \in Witnesses
    /\ d \in Digests
    /\ w \in Byzantine \/ signed[w] = {}
    /\ signed' = [signed EXCEPT ![w] = @ \cup {d}]
    /\ UNCHANGED <<certified, owner, settled>>

Certify(d) ==
    /\ d \in Digests
    /\ ~(Cardinality({w \in Witnesses : d \in signed[w]}) \in 0..(Q - 1))
    /\ certified' = certified \cup {d}
    /\ UNCHANGED <<signed, owner, settled>>

Transfer(r, from, to) ==
    /\ r \in RightTokens
    /\ from \in Sites
    /\ to \in Sites
    /\ from # to
    /\ owner[r] = from
    /\ owner' = [owner EXCEPT ![r] = to]
    /\ UNCHANGED <<signed, certified, settled>>

Debit(r, from) ==
    /\ r \in RightTokens
    /\ from \in Sites
    /\ owner[r] = from
    /\ owner' = [owner EXCEPT ![r] = NoOwner]
    /\ UNCHANGED <<signed, certified, settled>>

Settle(n) ==
    /\ n \in Nullifiers
    /\ n \notin settled
    /\ settled' = settled \cup {n}
    /\ UNCHANGED <<signed, certified, owner>>

Next ==
    \/ \E w \in Witnesses, d \in Digests : Sign(w, d)
    \/ \E d \in Digests : Certify(d)
    \/ \E r \in RightTokens, f \in Sites, t \in Sites : Transfer(r, f, t)
    \/ \E r \in RightTokens, f \in Sites : Debit(r, f)
    \/ \E n \in Nullifiers : Settle(n)

NoConflictingCertificates == ~(Digests \subseteq certified)
RightsConserved == DOMAIN owner = RightTokens /\ \A r \in RightTokens : owner[r] \in Owners
AtMostOnce == settled \in SUBSET Nullifiers

Spec == Init /\ [][Next]_<<signed, certified, owner, settled>>
=============================================================================
