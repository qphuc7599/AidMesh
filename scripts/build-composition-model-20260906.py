from pathlib import Path

p = Path("formal/AidMeshV2AuthorizationAndTerminal.spthy")
s = p.read_text().replace("theory AidMeshV2AuthorizationAndTerminal", "theory AidMeshV3Composition")
s = s.replace("Fr(~value), Fr(~releaseid), Fr(~skeleton) ]", "Fr(~value), Fr(~releaseid), Fr(~skeleton), Fr(~right), Fr(~nonce) ]")
s = s.replace("[ WReady('h1', ~domain", "[ RightOwned(~domain, ~right, ~site, ~nonce),\n    WReady('h1', ~domain", 1)
s = s.replace("releaseid, skeleton, 'fallback'>) ]\n  --[ FallbackAuthorization", "releaseid, skeleton, 'fallback'>),\n    RightOwned(domain, right, site, nonce) ]\n  --[ RightSpent(domain, right, site, nonce, releaseid),\n      FallbackDebit(domain, epoch, nullifier, site, releaseid, right),\n      FallbackAuthorization")
s = s.replace("!Authorized(domain, epoch, nullifier, intent) ]\n  --[ ReleaseIntentPersisted", "!Authorized(domain, epoch, nullifier,\n      <'AuthorizationIntentV1', domain, epoch, nullifier, proofhash,\n       site, value, releaseid, skeleton>) ]\n  --[ ReleaseIntentPersisted")
# Bind the structured intent in a let clause instead of introducing free variables.
s = s.replace("rule PersistReleaseIntent:\n", "rule PersistReleaseIntent:\n  let intent = <'AuthorizationIntentV1', domain, epoch, nullifier, proofhash,\n                site, value, releaseid, skeleton>\n  in\n")
s = s.replace("!Authorized(domain, epoch, nullifier, intent), In(evidence) ]", "ReleaseClosed(domain, epoch, nullifier, site, intent, evidence) ]")
s = s.replace("[ Fr(~domain), Fr(~transfer), Fr(~right), Fr(~source), Fr(~destination) ]\n  --[ TransferCreated(~domain, ~transfer, ~right, ~source, ~destination) ]->\n  [ TReady('h1', ~domain, ~transfer),\n    TReady('h2', ~domain, ~transfer),\n    TReady('h3', ~domain, ~transfer),\n    InTransit(~domain, ~transfer, ~right, ~source, ~destination),\n    Out(<~domain, ~transfer, ~right, ~source, ~destination>) ]",
"[ RightOwned(domain, right, source, nonce), Fr(~transfer), Fr(~destination) ]\n  --[ TransferCreated(domain, ~transfer, right, source, ~destination) ]->\n  [ TReady('h1', domain, ~transfer),\n    TReady('h2', domain, ~transfer),\n    TReady('h3', domain, ~transfer),\n    InTransit(domain, ~transfer, right, source, ~destination),\n    Out(<domain, ~transfer, right, source, ~destination>) ]")
s = s.rsplit("end", 1)[0] + '''
lemma executable_fallback_settlement:
  exists-trace
  "Ex domain epoch nullifier site releaseid right receipt #i #j.
     FallbackDebit(domain, epoch, nullifier, site, releaseid, right) @ i
     & Settled(domain, epoch, nullifier, receipt) @ j & i < j
     & not (Ex d t r a b #k. TransferCreated(d, t, r, a, b) @ k)
     & not (Ex d e n intent #k. AuthorizationCertificate(d, e, n, intent) @ k)"

lemma fallback_authorization_has_debit:
  "All domain epoch nullifier proofhash site value releaseid skeleton #i.
     FallbackAuthorization(domain, epoch, nullifier,
       <'AuthorizationIntentV1', domain, epoch, nullifier, proofhash,
        site, value, releaseid, skeleton>) @ i
     ==> (Ex right. FallbackDebit(domain, epoch, nullifier, site, releaseid, right) @ i)"

lemma settlement_has_closed_handoff:
  "All domain epoch nullifier intent evidence #i.
     Settled(domain, epoch, nullifier,
       <'ReceiptV2', domain, epoch, nullifier, intent, evidence>) @ i
     ==> (Ex site #j.
       HandoffClosed(domain, epoch, nullifier, site, intent, evidence) @ j & j < i)"

end
'''
Path("formal/AidMeshV3Composition.spthy").write_text(s)
