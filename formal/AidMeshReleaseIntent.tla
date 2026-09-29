-------------------------- MODULE AidMeshReleaseIntent --------------------------
EXTENDS Integers, TLC

CONSTANT PersistBeforeActuator

Statuses == {"INIT", "RELEASE_INTENT", "AMBIGUOUS", "CLOSED"}
Paths == {"quorum", "fallback"}

VARIABLES durableStatus, volatileAuthorized, authorizationPath, actuatorInvocations

vars == <<durableStatus, volatileAuthorized, authorizationPath, actuatorInvocations>>

TypeOK ==
    /\ durableStatus \in Statuses
    /\ volatileAuthorized \in BOOLEAN
    /\ authorizationPath \in Paths \cup {"none"}
    /\ actuatorInvocations \in 0..2

Init ==
    /\ durableStatus = "INIT"
    /\ volatileAuthorized = FALSE
    /\ authorizationPath = "none"
    /\ actuatorInvocations = 0

\* Quorum and fallback authorization derive different intent identifiers, but
\* both address the same path-independent durable slot (D, epoch, nullifier, site).
Authorize(path) ==
    /\ path \in Paths
    /\ durableStatus = "INIT"
    /\ ~volatileAuthorized
    /\ volatileAuthorized' = TRUE
    /\ authorizationPath' = path
    /\ UNCHANGED <<durableStatus, actuatorInvocations>>

\* Switching evidence before the durable commit changes the operation stored
\* in the same slot; it never creates another slot.
SwitchAuthorizationPath(path) ==
    /\ path \in Paths
    /\ durableStatus = "INIT"
    /\ volatileAuthorized
    /\ path # authorizationPath
    /\ authorizationPath' = path
    /\ UNCHANGED <<durableStatus, volatileAuthorized, actuatorInvocations>>

PersistReleaseIntent ==
    /\ PersistBeforeActuator
    /\ durableStatus = "INIT"
    /\ volatileAuthorized
    /\ durableStatus' = "RELEASE_INTENT"
    /\ UNCHANGED <<volatileAuthorized, authorizationPath, actuatorInvocations>>

KnownActuatorResult ==
    /\ actuatorInvocations < 2
    /\ IF PersistBeforeActuator
          THEN durableStatus = "RELEASE_INTENT"
          ELSE /\ durableStatus = "INIT"
               /\ volatileAuthorized
    /\ actuatorInvocations' = actuatorInvocations + 1
    /\ durableStatus' = "CLOSED"
    /\ volatileAuthorized' = FALSE
    /\ UNCHANGED authorizationPath

\* The device may have actuated before the process receives an acknowledgement.
\* A safe implementation recovers a durable intent and locks it as ambiguous.
\* The negative control invokes the actuator first and loses all memory.
AmbiguousActuatorAndCrash ==
    /\ actuatorInvocations < 2
    /\ IF PersistBeforeActuator
          THEN durableStatus = "RELEASE_INTENT"
          ELSE /\ durableStatus = "INIT"
               /\ volatileAuthorized
    /\ actuatorInvocations' = actuatorInvocations + 1
    /\ durableStatus' = IF PersistBeforeActuator THEN "AMBIGUOUS" ELSE "INIT"
    /\ volatileAuthorized' = FALSE
    /\ authorizationPath' = IF PersistBeforeActuator THEN authorizationPath ELSE "none"

CrashBeforeActuator ==
    /\ volatileAuthorized
    /\ durableStatus \in {"INIT", "RELEASE_INTENT"}
    /\ volatileAuthorized' = FALSE
    /\ durableStatus' =
          IF durableStatus = "RELEASE_INTENT" THEN "AMBIGUOUS" ELSE "INIT"
    /\ authorizationPath' =
          IF durableStatus = "RELEASE_INTENT" THEN authorizationPath ELSE "none"
    /\ UNCHANGED actuatorInvocations

Next ==
    \/ \E path \in Paths : Authorize(path)
    \/ \E path \in Paths : SwitchAuthorizationPath(path)
    \/ PersistReleaseIntent
    \/ KnownActuatorResult
    \/ AmbiguousActuatorAndCrash
    \/ CrashBeforeActuator

AtMostOneAutomaticActuator == actuatorInvocations <= 1

TerminalIsFailClosed ==
    durableStatus \in {"AMBIGUOUS", "CLOSED"} => ~volatileAuthorized

PathSwitchDoesNotCreateSlot ==
    durableStatus \in {"RELEASE_INTENT", "AMBIGUOUS", "CLOSED"}
        => authorizationPath \in Paths

Spec == Init /\ [][Next]_vars
=============================================================================
