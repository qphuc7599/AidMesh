---------------------- MODULE AidMeshAuthorizationBinding ----------------------
EXTENDS FiniteSets, TLC

(*
Compact model of the protocol-to-contract authorization-binding boundary.
A site signs a receipt only after an exact nonzero path-evidence commitment has
been selected.  The gateway must accept that same commitment and the settlement
contract must preserve it with the first-valid winner.

UnsafeRebind is a mutation operator: it lets the gateway substitute a different
commitment after signature verification.  The negative-control configuration is
expected to violate SettlementPreservesSignedAuthorization.
*)

CONSTANTS Receipts, Commitments, UnsafeRebind

NoAuthorization == "NO_AUTHORIZATION"
NoReceipt == "NO_RECEIPT"
AuthorizationValues == Commitments \cup {NoAuthorization}

VARIABLES proofOK, evidenceCommitment, signedCommitment,
          gatewayAccepted, acceptedCommitment, winner, winningCommitment

vars == <<proofOK, evidenceCommitment, signedCommitment,
          gatewayAccepted, acceptedCommitment, winner, winningCommitment>>

TypeOK ==
    /\ proofOK \in [Receipts -> BOOLEAN]
    /\ evidenceCommitment \in [Receipts -> AuthorizationValues]
    /\ signedCommitment \in [Receipts -> AuthorizationValues]
    /\ gatewayAccepted \in [Receipts -> BOOLEAN]
    /\ acceptedCommitment \in [Receipts -> AuthorizationValues]
    /\ winner \in Receipts \cup {NoReceipt}
    /\ winningCommitment \in AuthorizationValues

Init ==
    /\ proofOK = [r \in Receipts |-> FALSE]
    /\ evidenceCommitment = [r \in Receipts |-> NoAuthorization]
    /\ signedCommitment = [r \in Receipts |-> NoAuthorization]
    /\ gatewayAccepted = [r \in Receipts |-> FALSE]
    /\ acceptedCommitment = [r \in Receipts |-> NoAuthorization]
    /\ winner = NoReceipt
    /\ winningCommitment = NoAuthorization

VerifyProof(r) ==
    /\ ~proofOK[r]
    /\ proofOK' = [proofOK EXCEPT ![r] = TRUE]
    /\ UNCHANGED <<evidenceCommitment, signedCommitment, gatewayAccepted,
                    acceptedCommitment, winner, winningCommitment>>

CommitEvidence(r, c) ==
    /\ c \in Commitments
    /\ evidenceCommitment[r] = NoAuthorization
    /\ signedCommitment[r] = NoAuthorization
    /\ evidenceCommitment' = [evidenceCommitment EXCEPT ![r] = c]
    /\ UNCHANGED <<proofOK, signedCommitment, gatewayAccepted,
                    acceptedCommitment, winner, winningCommitment>>

SignReceipt(r) ==
    /\ proofOK[r]
    /\ evidenceCommitment[r] # NoAuthorization
    /\ signedCommitment[r] = NoAuthorization
    /\ signedCommitment' =
            [signedCommitment EXCEPT ![r] = evidenceCommitment[r]]
    /\ UNCHANGED <<proofOK, evidenceCommitment, gatewayAccepted,
                    acceptedCommitment, winner, winningCommitment>>

GatewayAccept(r, c) ==
    /\ proofOK[r]
    /\ signedCommitment[r] # NoAuthorization
    /\ ~gatewayAccepted[r]
    /\ c \in Commitments
    /\ c = signedCommitment[r] \/ UnsafeRebind
    /\ gatewayAccepted' = [gatewayAccepted EXCEPT ![r] = TRUE]
    /\ acceptedCommitment' = [acceptedCommitment EXCEPT ![r] = c]
    /\ UNCHANGED <<proofOK, evidenceCommitment, signedCommitment,
                    winner, winningCommitment>>

Settle(r) ==
    /\ winner = NoReceipt
    /\ gatewayAccepted[r]
    /\ acceptedCommitment[r] # NoAuthorization
    /\ winner' = r
    /\ winningCommitment' = acceptedCommitment[r]
    /\ UNCHANGED <<proofOK, evidenceCommitment, signedCommitment,
                    gatewayAccepted, acceptedCommitment>>

Next ==
    \/ \E r \in Receipts : VerifyProof(r)
    \/ \E r \in Receipts, c \in Commitments : CommitEvidence(r, c)
    \/ \E r \in Receipts : SignReceipt(r)
    \/ \E r \in Receipts, c \in Commitments : GatewayAccept(r, c)
    \/ \E r \in Receipts : Settle(r)

GatewayPreservesSignedAuthorization ==
    \A r \in Receipts :
        gatewayAccepted[r] =>
            /\ acceptedCommitment[r] # NoAuthorization
            /\ acceptedCommitment[r] = signedCommitment[r]

SettlementPreservesSignedAuthorization ==
    winner # NoReceipt =>
        /\ winningCommitment # NoAuthorization
        /\ gatewayAccepted[winner]
        /\ winningCommitment = acceptedCommitment[winner]
        /\ winningCommitment = signedCommitment[winner]

=============================================================================
