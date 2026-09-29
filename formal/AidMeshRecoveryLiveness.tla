------------------------ MODULE AidMeshRecoveryLiveness ------------------------
EXTENDS TLC

(*
Small temporal model for the conditional liveness proposition.  The environment
constants represent the stated assumptions: one authorization path is available,
the bound receipt is eventually deliverable, and processing is weakly fair.
*)

CONSTANTS CanAuthorize, CanDeliver, PriorWinner

VARIABLES phase, durableAuthorization

vars == <<phase, durableAuthorization>>

Phases == {"request", "authorized", "released", "delivered", "settled", "conflict"}

TypeOK ==
    /\ phase \in Phases
    /\ durableAuthorization \in BOOLEAN

Init ==
    /\ phase = "request"
    /\ durableAuthorization = FALSE

Authorize ==
    /\ phase = "request"
    /\ CanAuthorize
    /\ phase' = "authorized"
    /\ durableAuthorization' = TRUE

Release ==
    /\ phase = "authorized"
    /\ durableAuthorization
    /\ phase' = "released"
    /\ UNCHANGED durableAuthorization

Deliver ==
    /\ phase = "released"
    /\ CanDeliver
    /\ phase' = "delivered"
    /\ UNCHANGED durableAuthorization

SettleOrConflict ==
    /\ phase = "delivered"
    /\ phase' = IF PriorWinner THEN "conflict" ELSE "settled"
    /\ UNCHANGED durableAuthorization

Restart ==
    /\ phase \in {"authorized", "released"}
    /\ phase' = phase
    /\ durableAuthorization' = durableAuthorization

Next == Authorize \/ Release \/ Deliver \/ SettleOrConflict \/ Restart

Spec ==
    /\ Init
    /\ [][Next]_vars
    /\ WF_vars(Authorize)
    /\ WF_vars(Release)
    /\ WF_vars(Deliver)
    /\ WF_vars(SettleOrConflict)

ReleaseRetainsDurableAuthorization ==
    phase \in {"released", "delivered", "settled", "conflict"} => durableAuthorization

EventuallyTerminal == <> (phase \in {"settled", "conflict"})

=============================================================================
