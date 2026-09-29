---------------------- MODULE AidMeshReclaimableTransfer ----------------------
EXTENDS FiniteSets, Integers, TLC

CONSTANTS Witnesses, Q, Sites, Rights, Transfers, UnsafeTerminalOverwrite

NoVote == "NO_VOTE"
NoRight == "NO_RIGHT"
NoSite == "NO_SITE"
Decisions == {"accept", "reclaim"}
TransferStatuses == {"none", "in_transit", "accepted", "reclaimed"}
RightStatuses == {"remaining", "in_transit"}

VARIABLES transferStatus, transferRight, transferSource, transferDestination,
          rightStatus, rightOwner, votes, acceptedEver, reclaimedEver

vars == <<transferStatus, transferRight, transferSource, transferDestination,
          rightStatus, rightOwner, votes, acceptedEver, reclaimedEver>>

TypeOK ==
    /\ Q \in 1..Cardinality(Witnesses)
    /\ transferStatus \in [Transfers -> TransferStatuses]
    /\ transferRight \in [Transfers -> Rights \cup {NoRight}]
    /\ transferSource \in [Transfers -> Sites \cup {NoSite}]
    /\ transferDestination \in [Transfers -> Sites \cup {NoSite}]
    /\ rightStatus \in [Rights -> RightStatuses]
    /\ rightOwner \in [Rights -> Sites \cup {NoSite}]
    /\ votes \in [Witnesses -> [Transfers -> Decisions \cup {NoVote}]]
    /\ acceptedEver \subseteq Transfers
    /\ reclaimedEver \subseteq Transfers

Init ==
    /\ transferStatus = [t \in Transfers |-> "none"]
    /\ transferRight = [t \in Transfers |-> NoRight]
    /\ transferSource = [t \in Transfers |-> NoSite]
    /\ transferDestination = [t \in Transfers |-> NoSite]
    /\ rightStatus = [r \in Rights |-> "remaining"]
    /\ rightOwner = [r \in Rights |-> CHOOSE s \in Sites : TRUE]
    /\ votes = [w \in Witnesses |-> [t \in Transfers |-> NoVote]]
    /\ acceptedEver = {}
    /\ reclaimedEver = {}

Begin(t, r, source, destination) ==
    /\ transferStatus[t] = "none"
    /\ rightStatus[r] = "remaining"
    /\ rightOwner[r] = source
    /\ source # destination
    /\ transferStatus' = [transferStatus EXCEPT ![t] = "in_transit"]
    /\ transferRight' = [transferRight EXCEPT ![t] = r]
    /\ transferSource' = [transferSource EXCEPT ![t] = source]
    /\ transferDestination' = [transferDestination EXCEPT ![t] = destination]
    /\ rightStatus' = [rightStatus EXCEPT ![r] = "in_transit"]
    /\ rightOwner' = [rightOwner EXCEPT ![r] = NoSite]
    /\ UNCHANGED <<votes, acceptedEver, reclaimedEver>>

Vote(w, t, decision) ==
    /\ transferStatus[t] = "in_transit"
    /\ decision \in Decisions
    /\ votes[w][t] \in {NoVote, decision}
    /\ votes' = [votes EXCEPT ![w][t] = decision]
    /\ UNCHANGED <<transferStatus, transferRight, transferSource,
                    transferDestination, rightStatus, rightOwner,
                    acceptedEver, reclaimedEver>>

AcceptEnabled(t) ==
    Cardinality({w \in Witnesses : votes[w][t] = "accept"}) >= Q

ReclaimEnabled(t) ==
    Cardinality({w \in Witnesses : votes[w][t] = "reclaim"}) >= Q

FinalizeAccept(t) ==
    LET r == transferRight[t] IN
    /\ AcceptEnabled(t)
    /\ \/ transferStatus[t] = "in_transit"
       \/ /\ UnsafeTerminalOverwrite
          /\ transferStatus[t] = "reclaimed"
    /\ transferStatus' = [transferStatus EXCEPT ![t] = "accepted"]
    /\ rightStatus' = [rightStatus EXCEPT ![r] = "remaining"]
    /\ rightOwner' = [rightOwner EXCEPT ![r] = transferDestination[t]]
    /\ acceptedEver' = acceptedEver \cup {t}
    /\ UNCHANGED <<transferRight, transferSource, transferDestination,
                    votes, reclaimedEver>>

FinalizeReclaim(t) ==
    LET r == transferRight[t] IN
    /\ ReclaimEnabled(t)
    /\ \/ transferStatus[t] = "in_transit"
       \/ /\ UnsafeTerminalOverwrite
          /\ transferStatus[t] = "accepted"
    /\ transferStatus' = [transferStatus EXCEPT ![t] = "reclaimed"]
    /\ rightStatus' = [rightStatus EXCEPT ![r] = "remaining"]
    /\ rightOwner' = [rightOwner EXCEPT ![r] = transferSource[t]]
    /\ reclaimedEver' = reclaimedEver \cup {t}
    /\ UNCHANGED <<transferRight, transferSource, transferDestination,
                    votes, acceptedEver>>

Next ==
    \/ \E t \in Transfers, r \in Rights, s \in Sites, d \in Sites :
            Begin(t, r, s, d)
    \/ \E w \in Witnesses, t \in Transfers, decision \in Decisions :
            Vote(w, t, decision)
    \/ \E t \in Transfers : FinalizeAccept(t)
    \/ \E t \in Transfers : FinalizeReclaim(t)

OwnershipConsistent ==
    \A r \in Rights :
        (rightStatus[r] = "remaining") <=> (rightOwner[r] \in Sites)

OpenTransferUnique ==
    \A t1, t2 \in Transfers :
        /\ t1 # t2
        /\ transferStatus[t1] = "in_transit"
        /\ transferStatus[t2] = "in_transit"
        => transferRight[t1] # transferRight[t2]

TerminalOwnerConsistent ==
    \A t \in Transfers :
        /\ transferStatus[t] = "accepted"
           => rightOwner[transferRight[t]] = transferDestination[t]
        /\ transferStatus[t] = "reclaimed"
           => rightOwner[transferRight[t]] = transferSource[t]

WitnessSingleTerminal ==
    \A w \in Witnesses, t \in Transfers :
        Cardinality({d \in Decisions : votes[w][t] = d}) <= 1

TerminalExclusive == acceptedEver \cap reclaimedEver = {}

Spec == Init /\ [][Next]_vars
=============================================================================
