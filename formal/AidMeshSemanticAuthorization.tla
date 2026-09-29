--------------------- MODULE AidMeshSemanticAuthorization ---------------------
EXTENDS FiniteSets, Naturals, TLC

(*
Protocol-to-contract refinement for the semantic gateway.  A relayer is fully
adversarial: it may submit any site-signed receipt, signer set, mode and right
nonce.  The safe gateway accepts only the exact signed statement when either a
threshold of distinct registered witnesses signed it or an unspent issued right
is owned by the receipt site.  Unsafe mutations separately skip the threshold,
reuse a spent right, or rebind the authorization after the site signature.
*)

CONSTANTS Receipts, Sites, Witnesses, Threshold, RiskCap,
          UnsafeSkipThreshold, UnsafeRightReuse, UnsafeRebind

NoSite == "NO_SITE"
NoReceipt == "NO_RECEIPT"
NoAuth == <<"NO_AUTH">>
Modes == {"quorum", "right"}

VARIABLES proofOK, configured, mode, receiptSite, signers, rightNonceClaim,
          siteSigned, signedAuth, gatewayAccepted, acceptedAuth,
          semanticEvidenceOK, rightOwner, rightNonce, rightSpent,
          rightAcceptCount, winner, winningAuth

vars == <<proofOK, configured, mode, receiptSite, signers, rightNonceClaim,
          siteSigned, signedAuth, gatewayAccepted, acceptedAuth,
          semanticEvidenceOK, rightOwner, rightNonce, rightSpent,
          rightAcceptCount, winner, winningAuth>>

Authorization(r) ==
    <<mode[r], receiptSite[r], signers[r], rightNonceClaim[r]>>

QuorumValid(r) ==
    /\ mode[r] = "quorum"
    /\ signers[r] \subseteq Witnesses
    /\ Cardinality(signers[r]) >= Threshold

RightValid(r) ==
    /\ mode[r] = "right"
    /\ receiptSite[r] = rightOwner
    /\ rightNonceClaim[r] = rightNonce
    /\ ~rightSpent

Init ==
    /\ proofOK = [r \in Receipts |-> FALSE]
    /\ configured = [r \in Receipts |-> FALSE]
    /\ mode = [r \in Receipts |-> "quorum"]
    /\ receiptSite = [r \in Receipts |-> NoSite]
    /\ signers = [r \in Receipts |-> {}]
    /\ rightNonceClaim = [r \in Receipts |-> 0]
    /\ siteSigned = [r \in Receipts |-> FALSE]
    /\ signedAuth = [r \in Receipts |-> NoAuth]
    /\ gatewayAccepted = [r \in Receipts |-> FALSE]
    /\ acceptedAuth = [r \in Receipts |-> NoAuth]
    /\ semanticEvidenceOK = [r \in Receipts |-> FALSE]
    /\ rightOwner \in Sites
    /\ rightNonce = 0
    /\ rightSpent = FALSE
    /\ rightAcceptCount = 0
    /\ winner = NoReceipt
    /\ winningAuth = NoAuth

VerifyProof(r) ==
    /\ ~proofOK[r]
    /\ proofOK' = [proofOK EXCEPT ![r] = TRUE]
    /\ UNCHANGED <<configured, mode, receiptSite, signers, rightNonceClaim,
                    siteSigned, signedAuth, gatewayAccepted, acceptedAuth,
                    semanticEvidenceOK, rightOwner, rightNonce, rightSpent,
                    rightAcceptCount, winner, winningAuth>>

ConfigureReceipt(r, m, s, ws, nonce) ==
    /\ ~configured[r]
    /\ m \in Modes
    /\ s \in Sites
    /\ ws \subseteq Witnesses
    /\ nonce \in 0..2
    /\ configured' = [configured EXCEPT ![r] = TRUE]
    /\ mode' = [mode EXCEPT ![r] = m]
    /\ receiptSite' = [receiptSite EXCEPT ![r] = s]
    /\ signers' = [signers EXCEPT ![r] = ws]
    /\ rightNonceClaim' = [rightNonceClaim EXCEPT ![r] = nonce]
    /\ UNCHANGED <<proofOK, siteSigned, signedAuth, gatewayAccepted,
                    acceptedAuth, semanticEvidenceOK, rightOwner, rightNonce,
                    rightSpent, rightAcceptCount, winner, winningAuth>>

SiteSign(r) ==
    /\ configured[r]
    /\ ~siteSigned[r]
    /\ siteSigned' = [siteSigned EXCEPT ![r] = TRUE]
    /\ signedAuth' = [signedAuth EXCEPT ![r] = Authorization(r)]
    /\ UNCHANGED <<proofOK, configured, mode, receiptSite, signers,
                    rightNonceClaim, gatewayAccepted, acceptedAuth,
                    semanticEvidenceOK, rightOwner, rightNonce, rightSpent,
                    rightAcceptCount, winner, winningAuth>>

TransferIssuedRight(to) ==
    /\ ~rightSpent
    /\ rightNonce < 2
    /\ to \in Sites
    /\ rightOwner' = to
    /\ rightNonce' = rightNonce + 1
    /\ UNCHANGED <<proofOK, configured, mode, receiptSite, signers,
                    rightNonceClaim, siteSigned, signedAuth, gatewayAccepted,
                    acceptedAuth, semanticEvidenceOK, rightSpent,
                    rightAcceptCount, winner, winningAuth>>

GatewayAccept(r, substitute) ==
    LET quorumOK == QuorumValid(r) \/ UnsafeSkipThreshold
        rightOK == RightValid(r) \/ UnsafeRightReuse
        evidenceOK == IF mode[r] = "quorum" THEN quorumOK ELSE rightOK
        chosen == IF substitute THEN NoAuth ELSE Authorization(r)
    IN
    /\ proofOK[r]
    /\ configured[r]
    /\ siteSigned[r]
    /\ ~gatewayAccepted[r]
    /\ evidenceOK
    /\ ~substitute \/ UnsafeRebind
    /\ gatewayAccepted' = [gatewayAccepted EXCEPT ![r] = TRUE]
    /\ acceptedAuth' = [acceptedAuth EXCEPT ![r] = chosen]
    /\ semanticEvidenceOK' = [semanticEvidenceOK EXCEPT
            ![r] = (QuorumValid(r) \/ RightValid(r))]
    /\ IF mode[r] = "right"
          THEN /\ rightSpent' = TRUE
               /\ rightAcceptCount' = rightAcceptCount + 1
          ELSE /\ UNCHANGED rightSpent
               /\ UNCHANGED rightAcceptCount
    /\ UNCHANGED <<proofOK, configured, mode, receiptSite, signers,
                    rightNonceClaim, siteSigned, signedAuth, rightOwner,
                    rightNonce, winner, winningAuth>>

Settle(r) ==
    /\ winner = NoReceipt
    /\ gatewayAccepted[r]
    /\ winner' = r
    /\ winningAuth' = acceptedAuth[r]
    /\ UNCHANGED <<proofOK, configured, mode, receiptSite, signers,
                    rightNonceClaim, siteSigned, signedAuth, gatewayAccepted,
                    acceptedAuth, semanticEvidenceOK, rightOwner, rightNonce,
                    rightSpent, rightAcceptCount>>

Next ==
    \/ \E r \in Receipts : VerifyProof(r)
    \/ \E r \in Receipts, m \in Modes, s \in Sites,
          ws \in SUBSET Witnesses, nonce \in 0..2 :
             ConfigureReceipt(r, m, s, ws, nonce)
    \/ \E r \in Receipts : SiteSign(r)
    \/ \E s \in Sites : TransferIssuedRight(s)
    \/ \E r \in Receipts, substitute \in BOOLEAN : GatewayAccept(r, substitute)
    \/ \E r \in Receipts : Settle(r)

TypeOK ==
    /\ proofOK \in [Receipts -> BOOLEAN]
    /\ configured \in [Receipts -> BOOLEAN]
    /\ mode \in [Receipts -> Modes]
    /\ receiptSite \in [Receipts -> Sites \cup {NoSite}]
    /\ signers \in [Receipts -> SUBSET Witnesses]
    /\ rightNonceClaim \in [Receipts -> 0..2]
    /\ siteSigned \in [Receipts -> BOOLEAN]
    /\ gatewayAccepted \in [Receipts -> BOOLEAN]
    /\ semanticEvidenceOK \in [Receipts -> BOOLEAN]
    /\ rightOwner \in Sites
    /\ rightNonce \in 0..2
    /\ rightSpent \in BOOLEAN
    /\ rightAcceptCount \in Nat
    /\ winner \in Receipts \cup {NoReceipt}

SemanticAuthorizationSound ==
    \A r \in Receipts : gatewayAccepted[r] => semanticEvidenceOK[r]

ExactReleaseToSettlementBinding ==
    /\ \A r \in Receipts :
          gatewayAccepted[r] => acceptedAuth[r] = signedAuth[r]
    /\ winner # NoReceipt => winningAuth = signedAuth[winner]

HonestSiteRiskBound == rightAcceptCount <= RiskCap

=============================================================================
