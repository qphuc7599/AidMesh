from pathlib import Path
import re
import shutil

root = Path(__file__).resolve().parents[1]
backup = root / 'output/architecture_revision_20260909'
backup.mkdir(exist_ok=False)
for name in ['paper.tex', 'paper.pdf']:
    shutil.copy2(root/'source'/name, backup/name)
shutil.copy2(root/'overleaf/paper.tex', backup/'overleaf-before.tex')

figure = r'''
\begin{figure}[p]
\centering
\begin{tikzpicture}[
  x=1cm,y=1cm, font=\sffamily\scriptsize,
  box/.style={draw=black!65,rounded corners=2pt,align=center,
    text width=3.55cm,minimum height=1.35cm,inner sep=5pt,fill=blue!4},
  wide/.style={box,text width=11.85cm,minimum height=1.15cm},
  flow/.style={-{Stealth[length=2mm]},thick,draw=black!75},
  note/.style={font=\sffamily\tiny,fill=white,inner sep=2pt,align=center},
  band/.style={font=\sffamily\bfseries\small,anchor=west}]
\node[band] at (-2,0) {A. Campaign preparation and private eligibility};
\node[box] (policy) at (0,-1.05) {\textbf{Consortium policy}\\Demand, stock, priority weights\\Epoch, cells, rosters, risk caps};
\node[box] (fair) at (4.2,-1.05) {\textbf{Fair entitlement allocation}\\Progressive filling and rounding\\Optional fair-floor completion};
\node[box] (issuer) at (8.4,-1.05) {\textbf{Threshold credential issuance}\\Coconut blind quota credentials\\Holder secret remains private};
\draw[flow] (policy)--(fair);
\draw[flow] (fair)--(issuer);
\node[wide] (roots) at (4.2,-3.05) {\textbf{Admission and campaign anchors}\\
  Validate quota allocation; seal credential and entitlement roots; endorse policy and deployment context.\\
  Root endorsement is a separate trust boundary; P1 checks membership, not the Coconut signature.};
\draw[flow] (fair.south)--(fair.south |- roots.north);
\draw[flow] (issuer.south)--(issuer.south |- roots.north);
\node[band] at (-2,-4.2) {B. Internet-independent authorization and durable release};
\node[wide] (voucher) at (4.2,-5.12) {\textbf{Beneficiary presentation and site verification}\\
  Derive indexed nullifier, commitment and Groth16 P1 proof; verify roots, epoch, cell, value and context.\\
  Quorum path omits the fallback tag; fallback presentations are linkable within their disclosure window.};
\draw[flow] (roots)--(voucher);
\node[box,text width=5.55cm] (quorum) at (1.0,-7.18) {\textbf{Quorum authorization}\\
  Persist one intent per domain/epoch/nullifier\\Collect $q$ distinct witness signatures\\Conflicting certificates excluded if $2q>K+f$};
\node[box,text width=5.55cm] (budget) at (7.4,-7.18) {\textbf{Budgeted fallback authorization}\\
  Require a locally owned, unspent right\\Atomic debit and path-independent release claim\\Unavailable or exhausted budget: no automatic release};
\draw[flow] (voucher.south)-|(quorum.north);
\draw[flow] (voucher.south)-|(budget.north);
\node[wide] (release) at (4.2,-9.28) {\textbf{Shared durable release gate}\\
  Unique site/nullifier slot; persist intent before invoking the actuator; only the winning caller may invoke.\\
  Acknowledged handoff $\rightarrow$ signed CLOSED receipt; lost acknowledgment $\rightarrow$ charged AMBIGUOUS state, no automatic retry.};
\draw[flow] (quorum.south)--(quorum.south |- release.north);
\draw[flow] (budget.south)--(budget.south |- release.north);
\node[band] at (-2,-10.45) {C. Recovery of transferred rights and receipt export};
\node[box] (escrow) at (0,-11.55) {\textbf{Source debit and escrow}\\Durable transfer/outbox\\Neither endpoint may spend\\while the right is in transit};
\node[box] (recovery) at (4.2,-11.55) {\textbf{Terminal recovery committee}\\Prepare/commit; view change\\Highest-prepared selection\\ACCEPT or RECLAIM certificate};
\node[box] (owner) at (8.4,-11.55) {\textbf{Certified terminal owner}\\ACCEPT credits destination\\RECLAIM credits source\\Unique inbox prevents re-credit};
\draw[flow] (escrow)--(recovery);
\draw[flow] (recovery)--(owner);
\draw[flow,dashed] (owner.east)--++(.45,0)|-node[note,pos=.75]{Recovered right re-enters the same fallback gate}(budget.east);
\node[wide] (export) at (4.2,-13.72) {\textbf{Participant dual attestation and durable receipt outbox}\\
  Matching persisted Ed25519 commit and exact CLOSED receipt authorize secp256k1 statements.\\
  Bind chain, contracts, domain, right, transfer, view, decision and receipt; export creates no additional right.};
\draw[flow] (release.west)--++(-.35,0)|-(export.west);
\draw[flow] (recovery.south)--(export.north);
\node[band] at (-2,-14.9) {D. Delayed transport and connected reconciliation};
\node[box] (dtn) at (0,-16) {\textbf{BPv7 store--carry--forward}\\Sites, relays and gateway\\Signed payload preserved\\Replay conveys no authority};
\node[box] (gateway) at (4.2,-16) {\textbf{Semantic Solidity gateway}\\Verify P1 and pinned context\\Validate owner, nonce, quorum\\and final site receipt};
\node[box] (ledger) at (8.4,-16) {\textbf{Atomic settlement ledger}\\Consume right and record winner\\First valid nullifier receives credit\\Conflict/replay: no second credit};
\draw[flow] (export.south)--++(0,-.3)-|(dtn.north);
\draw[flow] (dtn)--(gateway);
\draw[flow] (gateway)--(ledger);
\end{tikzpicture}
\caption{AidMesh architecture and trust boundaries. Campaign preparation connects
fair entitlements to private credential admission. Both authorization paths converge
on the same durable release gate. Terminal recovery restores exactly one owner
of an escrowed right before fallback redemption; it does not refund ambiguous
handoffs. Participant dual attestation binds recovery and CLOSED receipts to the
EVM context. The integrated recovery bridge is one-shot, with synthetic actuation;
view-change implementation refinement and composed privacy remain separate proof
obligations. Transport delays evidence but cannot authorize release or credit.}
\label{fig:overview}
\end{figure}
'''

for rel in ['source/paper.tex', 'overleaf/paper.tex']:
    p=root/rel
    s=p.read_text(encoding='utf-8')
    s=s.replace(r'\usepackage{graphicx}', r'\usepackage{graphicx}'+'\n'+r'\usepackage{tikz}'+'\n'+r'\usetikzlibrary{arrows.meta,positioning,calc}')
    a=s.index(r'\begin{figure}')
    b=s.index(r'\end{figure}',a)+len(r'\end{figure}')
    s=s[:a]+figure+s[b:]
    a=s.index(r'\section*{CRediT')
    b=s.index(r'\bibliographystyle',a)
    s=s[:a]+s[b:]
    # Remove redundant historical runtime paragraph; retain current measured
    # values, negative results and boundaries of the underlying experiments.
    a=s.index('The following performance\nnumbers belong to the historical campaign')
    b=s.index('Across the Asturies primary/factorial',a)
    s=s[:a]+('These measurements use commodity software and local-EVM environments;\n'
             'they do not measure beneficiary-device or radio-energy costs.\n\n')+s[b:]
    s=s.replace('The integrated recovery implementation passes 262 unit, property and semantic-EVM\nregression tests. A further suite evaluates baseline and bridge behavior in 36 cases.\nThese overlapping suites measure software regression, not independent scientific\nreplication or completeness of the security matrix. Three additional',
    'Unit, property and semantic-EVM tests exercise the integrated recovery boundary.\nThree additional')
    p.write_text(s,encoding='utf-8')
print('Updated canonical and Overleaf manuscripts with inline TikZ architecture.')
