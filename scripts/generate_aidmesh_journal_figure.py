"""Generate the publication-style AidMesh architecture figure.

All pictograms are copied verbatim from Phosphor Icons Core's official
duotone SVG release, then embedded as vector symbols in the final SVG.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "figures" / "aidmesh_journal_architecture_20260719"
PKG = OUT / "package" / "package"
SRC = PKG / "assets" / "duotone"
ICONS = OUT / "icons"

NAMES = [
    "user-circle-check", "identification-card", "storefront", "users-three", "certificate",
    "seal-check", "vault", "coins", "hand-coins", "package", "gift",
    "file-lock", "stack", "device-mobile", "motorcycle", "van", "broadcast",
    "cell-tower", "wifi-slash", "cloud-arrow-up", "shield-check",
    "tree-structure", "fingerprint", "cube", "database", "globe-hemisphere-west",
    "magnifying-glass", "scales", "lock-key", "receipt", "file-text",
    "circuitry", "arrows-split", "arrows-merge", "check-circle", "warning",
    "clock-countdown", "key", "network", "flow-arrow", "eye",
]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inner(name: str) -> str:
    text = (ICONS / f"{name}-duotone.svg").read_text(encoding="utf-8")
    match = re.search(r"<svg[^>]*>(.*)</svg>", text, re.S)
    if not match:
        raise ValueError(name)
    return match.group(1)


def ico(name: str, x: float, y: float, size: float, color: str) -> str:
    return f'<use href="#i-{name}" x="{x}" y="{y}" width="{size}" height="{size}" color="{color}" fill="{color}" style="color:{color}"/>'


def step(n: str, x: float, y: float, color: str) -> str:
    return f'<circle cx="{x}" cy="{y}" r="15" fill="{color}" stroke="white" stroke-width="3"/><text x="{x}" y="{y+5}" class="step" text-anchor="middle">{n}</text>'


def build() -> str:
    symbols = "\n".join(f'<symbol id="i-{n}" viewBox="0 0 256 256">{inner(n)}</symbol>' for n in NAMES)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1800" height="1120" viewBox="0 0 1800 1120">
<defs>
  <filter id="soft" x="-20%" y="-20%" width="150%" height="160%"><feDropShadow dx="0" dy="4" stdDeviation="8" flood-color="#17364D" flood-opacity=".10"/></filter>
  <filter id="tiny" x="-20%" y="-20%" width="150%" height="160%"><feDropShadow dx="0" dy="2" stdDeviation="3" flood-color="#17364D" flood-opacity=".10"/></filter>
  <marker id="a-navy" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto"><path d="M0 0L10 5L0 10Z" fill="#173F5F"/></marker>
  <marker id="a-green" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto"><path d="M0 0L10 5L0 10Z" fill="#27856F"/></marker>
  <marker id="a-orange" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto"><path d="M0 0L10 5L0 10Z" fill="#D37A19"/></marker>
  <marker id="a-purple" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto"><path d="M0 0L10 5L0 10Z" fill="#6E4A9E"/></marker>
  <style>
    @page{{size:18.75in 11.6667in;margin:0}}
    text{{font-family:Inter,"Segoe UI",Arial,sans-serif;fill:#17364D}}
    .title{{font-size:29px;font-weight:760;letter-spacing:-.35px}}
    .sub{{font-size:14px;fill:#60788A}}
    .zone{{font-size:17px;font-weight:760;letter-spacing:.3px}}
    .cardtitle{{font-size:15px;font-weight:750}}
    .body{{font-size:12.5px;fill:#405F74}}
    .small{{font-size:11px;fill:#657C8C}}
    .micro{{font-size:10px;fill:#657C8C}}
    .math{{font-family:"Cambria Math",Georgia,serif;font-size:14px;font-weight:650}}
    .tag{{font-size:10px;font-weight:760;letter-spacing:.45px}}
    .step{{font-size:11px;font-weight:800;fill:white}}
    .foot{{font-size:11px;fill:#D8E8F0}}
  </style>
  {symbols}
</defs>
<rect width="1800" height="1120" fill="#F8FAFB"/>

<!-- Header -->
<text x="48" y="42" class="title">AidMesh system architecture</text>
<text x="48" y="67" class="sub">Private, risk-bounded humanitarian settlement across disconnected field sites and temporal-contact networks</text>
<rect x="1418" y="25" width="334" height="35" rx="17.5" fill="#E6F3F1"/>
<text x="1585" y="47" class="tag" text-anchor="middle" fill="#1D7564">OFFLINE SAFETY → EVENTUAL PRIVATE SETTLEMENT</text>

<!-- Three environmental domains -->
<rect x="34" y="92" width="666" height="842" rx="24" fill="#F2F7FA" stroke="#66889F" stroke-width="1.5" stroke-dasharray="9 6"/>
<rect x="720" y="92" width="394" height="842" rx="24" fill="#F8F4FA" stroke="#8A6BAA" stroke-width="1.5" stroke-dasharray="9 6"/>
<rect x="1134" y="92" width="434" height="842" rx="24" fill="#F3F8F3" stroke="#5F8B6D" stroke-width="1.5" stroke-dasharray="9 6"/>
<rect x="1588" y="92" width="178" height="842" rx="24" fill="#FFFBF2" stroke="#A98B54" stroke-width="1.5" stroke-dasharray="7 5"/>

<!-- domain labels -->
<circle cx="64" cy="123" r="17" fill="#173F5F"/><text x="64" y="129" class="step" text-anchor="middle">1</text>
<text x="92" y="128" class="zone">Field / disconnected safety domain</text>
<text x="92" y="148" class="small">local authorization without fresh global state</text>
<circle cx="750" cy="123" r="17" fill="#6E4A9E"/><text x="750" y="129" class="step" text-anchor="middle">2</text>
<text x="778" y="128" class="zone">DTN relay fabric</text><text x="778" y="148" class="small">temporal-contact transport</text>
<circle cx="1164" cy="123" r="17" fill="#27856F"/><text x="1164" y="129" class="step" text-anchor="middle">3</text>
<text x="1192" y="128" class="zone">Connected settlement</text><text x="1192" y="148" class="small">ZK verification + local EVM</text>
<text x="1677" y="127" class="zone" text-anchor="middle">Assurance</text><text x="1677" y="147" class="small" text-anchor="middle">claims &amp; boundaries</text>

<!-- FIELD: beneficiary and site -->
<rect x="62" y="178" width="220" height="154" rx="18" fill="white" stroke="#A7BECC" filter="url(#soft)"/>
<circle cx="112" cy="231" r="35" fill="#E8F1F6"/>{ico('user-circle-check', 84, 203, 56, '#235D83')}
<text x="158" y="211" class="cardtitle">Voucher holder</text><text x="158" y="234" class="body">offline credential</text><text x="158" y="255" class="body">signed request V</text>
<rect x="84" y="286" width="176" height="27" rx="8" fill="#EEF5F8"/>{ico('identification-card', 92, 291, 18, '#235D83')}<text x="118" y="304" class="small">credential · item · epoch · n</text>

<path d="M282 250C330 250 345 250 385 250" fill="none" stroke="#173F5F" stroke-width="2.5" marker-end="url(#a-navy)"/>
{step('1', 333, 250, '#173F5F')}<text x="333" y="230" class="small" text-anchor="middle">present V</text>

<rect x="390" y="178" width="270" height="154" rx="18" fill="white" stroke="#A7BECC" filter="url(#soft)"/>
<circle cx="445" cy="231" r="35" fill="#E8F1F6"/>{ico('storefront', 417, 203, 56, '#235D83')}
<text x="490" y="208" class="cardtitle">Distribution site</text><text x="490" y="231" class="body">validate signature + epoch</text><text x="490" y="252" class="body">check local policy snapshot</text>
<rect x="417" y="286" width="216" height="27" rx="8" fill="#EEF5F8"/>{ico('wifi-slash', 426, 291, 18, '#C6534F')}<text x="452" y="304" class="small">no synchronous global lookup</text>

<!-- split and branch labels -->
<path d="M525 332V373" stroke="#173F5F" stroke-width="2.5" marker-end="url(#a-navy)"/>{step('2', 525, 355, '#173F5F')}
<circle cx="525" cy="397" r="23" fill="white" stroke="#173F5F" stroke-width="1.5" filter="url(#tiny)"/>{ico('arrows-split', 508, 380, 34, '#173F5F')}
<path d="M505 414C460 444 315 436 232 466" fill="none" stroke="#27856F" stroke-width="2.5" marker-end="url(#a-green)"/>
<path d="M545 414C575 435 577 443 579 466" fill="none" stroke="#D37A19" stroke-width="2.5" stroke-dasharray="8 5" marker-end="url(#a-orange)"/>

<!-- safe path -->
<rect x="62" y="468" width="300" height="266" rx="19" fill="#F4FBF8" stroke="#52A08A" stroke-width="1.4"/>
<path d="M62 510V487a19 19 0 0 1 19-19h262a19 19 0 0 1 19 19v23" fill="#DFF2EB"/>
{ico('seal-check', 82, 480, 38, '#27856F')}<text x="131" y="493" class="cardtitle">Safe quorum path</text><text x="131" y="513" class="small">certificate-backed authorization</text>
<rect x="83" y="535" width="258" height="100" rx="13" fill="white" stroke="#A8D2C4"/>
{ico('users-three', 101, 553, 55, '#27856F')}<text x="168" y="557" class="cardtitle">Witness committee</text><text x="168" y="581" class="body">persistent vote state</text><text x="168" y="603" class="body">policy-valid event digest</text><text x="168" y="624" class="small">K members · ≤ f unavailable/faulty</text>
<path d="M212 642V664" stroke="#27856F" stroke-width="2" marker-end="url(#a-green)"/>
<rect x="83" y="670" width="258" height="43" rx="11" fill="#DFF2EB"/>{ico('certificate', 94, 679, 25, '#27856F')}<text x="128" y="688" class="cardtitle">Quorum certificate C</text><text x="128" y="705" class="small">q signatures · threshold met</text>
{step('2a', 73, 544, '#27856F')}{step('3a', 73, 690, '#27856F')}

<!-- fallback path -->
<rect x="383" y="468" width="277" height="266" rx="19" fill="#FFF9EE" stroke="#DC9A4B" stroke-width="1.4"/>
<path d="M383 510V487a19 19 0 0 1 19-19h239a19 19 0 0 1 19 19v23" fill="#FAE9C9"/>
{ico('vault', 402, 480, 38, '#C87116')}<text x="450" y="493" class="cardtitle">Bounded fallback</text><text x="450" y="513" class="small">conserved risk-rights R</text>
<rect x="404" y="535" width="235" height="100" rx="13" fill="white" stroke="#E6C38E"/>
{ico('coins', 421, 553, 55, '#C87116')}<text x="488" y="557" class="cardtitle">Per-domain rights pool</text><text x="488" y="581" class="body">mint · transfer · spend</text><text x="488" y="603" class="body">debit before transfer</text><text x="488" y="624" class="small">durable monotone state</text>
<path d="M521 642V664" stroke="#D37A19" stroke-width="2" stroke-dasharray="7 4" marker-end="url(#a-orange)"/>
<rect x="404" y="670" width="235" height="43" rx="11" fill="#FAE9C9"/>{ico('hand-coins', 415, 679, 25, '#C87116')}<text x="450" y="688" class="cardtitle">Consume R before release</text><text x="450" y="705" class="small">exposure ≤ registered cap</text>
{step('2b', 393, 544, '#D37A19')}{step('3b', 393, 690, '#D37A19')}

<!-- merge, release, receipt -->
<path d="M212 734C212 768 335 766 385 783" fill="none" stroke="#27856F" stroke-width="2.5" marker-end="url(#a-green)"/>
<path d="M521 734C521 768 448 766 408 783" fill="none" stroke="#D37A19" stroke-width="2.5" stroke-dasharray="8 5" marker-end="url(#a-orange)"/>
<circle cx="397" cy="790" r="24" fill="white" stroke="#173F5F" filter="url(#tiny)"/>{ico('arrows-merge', 379, 772, 36, '#173F5F')}
<path d="M421 790H447" stroke="#173F5F" stroke-width="2.5" marker-end="url(#a-navy)"/>
<rect x="451" y="756" width="209" height="93" rx="16" fill="white" stroke="#9FB6C4" filter="url(#soft)"/>
{ico('package', 468, 774, 47, '#235D83')}<text x="526" y="780" class="cardtitle">Release aid</text><text x="526" y="802" class="body">same service outcome</text><text x="526" y="823" class="body">emit signed receipt ρ</text>
{step('4', 440, 790, '#173F5F')}
<rect x="62" y="865" width="598" height="45" rx="12" fill="#E7F0F5" stroke="#ABC0CD"/>{ico('shield-check', 76, 875, 25, '#235D83')}<text x="111" y="883" class="math">Safe domain: 2q &gt; K + f</text><text x="313" y="883" class="body">or conserved fallback:</text><text x="444" y="883" class="math">I = R + T + S + E + V</text><text x="111" y="901" font-size="9.5" fill="#657C8C">violating quorum persistence or conservation yields a counterexample / bounded exposure—not unconditional safety</text>

<!-- DTN: receipt, temporal graph, bundle -->
<path d="M660 802C692 802 700 244 754 244" fill="none" stroke="#6E4A9E" stroke-width="2.5" stroke-dasharray="9 6" marker-end="url(#a-purple)"/>
{step('5', 708, 501, '#6E4A9E')}
<rect x="752" y="178" width="330" height="132" rx="18" fill="white" stroke="#B8A4CB" filter="url(#soft)"/>
<circle cx="806" cy="234" r="37" fill="#F0E8F6"/>{ico('file-lock', 777, 205, 58, '#6E4A9E')}
<text x="855" y="207" class="cardtitle">Privacy-preserving bundle B</text><text x="855" y="232" class="body">signed receipt(s) + evidence</text><text x="855" y="253" class="body">batch root + nullifier(s)</text><text x="855" y="274" class="body">fixed-size padding + encrypted detail</text><text x="855" y="295" class="small">configurable batch window / cover traffic</text>

<rect x="752" y="342" width="330" height="310" rx="19" fill="#FBF9FC" stroke="#B8A4CB"/>
<text x="917" y="372" class="cardtitle" text-anchor="middle">Temporal-contact graph G(t)</text><text x="917" y="392" class="small" text-anchor="middle">store · carry · forward under intermittent contacts</text>
<!-- relay graph -->
<path d="M806 455L913 424L1026 465L945 556L821 552L806 455M913 424L945 556M806 455L1026 465M821 552L913 424" fill="none" stroke="#8A65AA" stroke-width="2" stroke-dasharray="6 5"/>
<circle cx="806" cy="455" r="30" fill="#F0E8F6" stroke="#A68BBC"/>{ico('device-mobile', 787, 436, 38, '#6E4A9E')}
<circle cx="913" cy="424" r="30" fill="#F0E8F6" stroke="#A68BBC"/>{ico('motorcycle', 894, 405, 38, '#6E4A9E')}
<circle cx="1026" cy="465" r="30" fill="#F0E8F6" stroke="#A68BBC"/>{ico('van', 1007, 446, 38, '#6E4A9E')}
<circle cx="945" cy="556" r="30" fill="#F0E8F6" stroke="#A68BBC"/>{ico('cell-tower', 926, 537, 38, '#6E4A9E')}
<circle cx="821" cy="552" r="30" fill="#F0E8F6" stroke="#A68BBC"/>{ico('broadcast', 802, 533, 38, '#6E4A9E')}
<text x="917" y="628" class="small" text-anchor="middle">paired budgets: bytes · copies · TTL · buffer · contacts</text>
{step('6', 737, 485, '#6E4A9E')}

<path d="M917 652V686" stroke="#6E4A9E" stroke-width="2.5" stroke-dasharray="8 5" marker-end="url(#a-purple)"/>
<rect x="752" y="694" width="330" height="122" rx="18" fill="white" stroke="#B8A4CB" filter="url(#soft)"/>
{ico('clock-countdown', 776, 718, 52, '#6E4A9E')}<text x="844" y="718" class="cardtitle">Eventual delivery contract</text><text x="844" y="743" class="body">deliver once a temporal path exists</text><text x="844" y="766" class="body">retain immutable receipt identity</text><text x="844" y="789" class="small">delay is measured; synchrony is not assumed</text>
{step('7', 737, 748, '#6E4A9E')}
<rect x="752" y="849" width="330" height="61" rx="13" fill="#F0E8F6"/>{ico('network', 770, 863, 33, '#6E4A9E')}<text x="816" y="870" class="cardtitle">External-validity traces</text><text x="816" y="892" class="small">independent families + held-out temporal windows</text>

<!-- settlement flow -->
<path d="M1082 755C1110 755 1111 224 1168 224" fill="none" stroke="#6E4A9E" stroke-width="2.5" stroke-dasharray="9 6" marker-end="url(#a-purple)"/>
{step('8', 1121, 492, '#27856F')}
<rect x="1168" y="178" width="366" height="124" rx="18" fill="white" stroke="#9ABDA5" filter="url(#soft)"/>
<circle cx="1221" cy="231" r="36" fill="#E5F2E8"/>{ico('cloud-arrow-up', 1192, 202, 58, '#27856F')}
<text x="1270" y="207" class="cardtitle">Settlement gateway</text><text x="1270" y="231" class="body">authenticate transport + deduplicate</text><text x="1270" y="253" class="body">validate domain, format and rate limits</text><text x="1270" y="276" class="small">submit canonical public inputs x</text>

<path d="M1351 302V336" stroke="#27856F" stroke-width="2.5" marker-end="url(#a-green)"/>{step('9', 1351, 320, '#27856F')}
<rect x="1168" y="342" width="366" height="169" rx="18" fill="white" stroke="#6FA281" filter="url(#soft)"/>
<circle cx="1221" cy="406" r="40" fill="#E5F2E8"/>{ico('shield-check', 1189, 374, 64, '#27856F')}
<text x="1270" y="372" class="cardtitle">Groth16 anonymous membership</text><text x="1270" y="398" class="body">✓ verify proof π and receipt signature</text><text x="1270" y="421" class="body">✓ bind root · nullifier · epoch · mode</text><text x="1270" y="444" class="body">✓ reject malformed / replayed statements</text><text x="1270" y="468" class="small">claim scoped to stated Groth16 SRS assumption</text>
<rect x="1270" y="479" width="218" height="22" rx="11" fill="#E5F2E8"/><text x="1379" y="494" class="tag" text-anchor="middle" fill="#1D7564">SNARKJS ↔ PARSER ↔ SOLIDITY PARITY</text>

<path d="M1351 511V545" stroke="#27856F" stroke-width="2.5" marker-end="url(#a-green)"/>{step('10', 1351, 529, '#27856F')}
<rect x="1168" y="551" width="366" height="164" rx="18" fill="white" stroke="#6FA281" filter="url(#soft)"/>
<circle cx="1221" cy="613" r="40" fill="#E5F2E8"/>{ico('cube', 1189, 581, 64, '#27856F')}
<text x="1270" y="580" class="cardtitle">Local EVM settlement ledger</text><text x="1270" y="606" class="body">• register unique nullifier</text><text x="1270" y="629" class="body">• credit domain/site at most once</text><text x="1270" y="652" class="body">• commit settlement + epoch state</text><text x="1270" y="675" class="body">• emit immutable analysis events</text><text x="1270" y="698" class="small">replay ⇒ zero additional credit</text>

<path d="M1351 715V749" stroke="#27856F" stroke-width="2.5" marker-end="url(#a-green)"/>{step('11', 1351, 733, '#27856F')}
<rect x="1168" y="755" width="366" height="155" rx="18" fill="white" stroke="#9ABDA5" filter="url(#soft)"/>
<circle cx="1221" cy="815" r="38" fill="#E5F2E8"/>{ico('globe-hemisphere-west', 1191, 785, 60, '#27856F')}
<text x="1270" y="785" class="cardtitle">Observer / reproducible evidence</text><text x="1270" y="811" class="body">raw event log → immutable manifest</text><text x="1270" y="834" class="body">paired bootstrap + full distributions</text><text x="1270" y="857" class="body">figures generated from registered results</text><text x="1270" y="881" class="small">ledger-only · gateway · site vantage points</text>

<!-- assurance rail -->
<rect x="1606" y="178" width="142" height="191" rx="16" fill="white" stroke="#D7C398"/>{ico('shield-check', 1645, 194, 64, '#27856F')}<text x="1677" y="270" class="cardtitle" text-anchor="middle">Trust boundary</text><text x="1622" y="296" class="small">• domain-scoped keys</text><text x="1622" y="317" class="small">• persistent witnesses</text><text x="1622" y="338" class="small">• no global synchrony</text><text x="1622" y="359" class="small">• SRS assumption stated</text>
<rect x="1606" y="389" width="142" height="177" rx="16" fill="white" stroke="#D7C398"/>{ico('scales', 1647, 405, 60, '#A06D18')}<text x="1677" y="476" class="cardtitle" text-anchor="middle">Policy layer</text><text x="1622" y="502" class="small">• site admission</text><text x="1622" y="523" class="small">• domain cap</text><text x="1622" y="544" class="small">• epoch handoff</text><text x="1622" y="565" class="small">• campaign escrow</text>
<rect x="1606" y="586" width="142" height="176" rx="16" fill="white" stroke="#D7C398"/>{ico('magnifying-glass', 1647, 602, 60, '#A06D18')}<text x="1677" y="673" class="cardtitle" text-anchor="middle">Measured claims</text><text x="1622" y="699" class="small">• completion</text><text x="1622" y="720" class="small">• exposed value</text><text x="1622" y="741" class="small">• privacy advantage</text>
<rect x="1606" y="782" width="142" height="128" rx="16" fill="#FFF4DE" stroke="#D7C398"/>{ico('warning', 1651, 795, 52, '#C87116')}<text x="1677" y="857" class="cardtitle" text-anchor="middle">Non-claim</text><text x="1622" y="880" class="small">not zero-risk offline</text><text x="1622" y="899" class="small">not a field deployment</text>

<!-- Bottom legend / semantic rail -->
<rect x="34" y="956" width="1732" height="132" rx="20" fill="white" stroke="#CFDAE1"/>
<text x="60" y="984" class="cardtitle">Visual grammar</text>
<path d="M62 1012H130" stroke="#173F5F" stroke-width="2.5" marker-end="url(#a-navy)"/><text x="144" y="1017" class="small">request / state transition</text>
<path d="M62 1046H130" stroke="#27856F" stroke-width="2.5" marker-end="url(#a-green)"/><text x="144" y="1051" class="small">safe certificate / settlement</text>
<path d="M333 1012H401" stroke="#D37A19" stroke-width="2.5" stroke-dasharray="8 5" marker-end="url(#a-orange)"/><text x="415" y="1017" class="small">risk-right fallback</text>
<path d="M333 1046H401" stroke="#6E4A9E" stroke-width="2.5" stroke-dasharray="8 5" marker-end="url(#a-purple)"/><text x="415" y="1051" class="small">DTN transport</text>
<line x1="602" y1="975" x2="602" y2="1068" stroke="#D5E0E6"/>
<text x="628" y="984" class="cardtitle">Core artifacts</text>
{ico('identification-card', 628, 998, 30, '#235D83')}<text x="667" y="1018" class="body">V — credential-bound request</text>
{ico('seal-check', 628, 1034, 30, '#27856F')}<text x="667" y="1054" class="body">C — quorum certificate</text>
{ico('coins', 860, 998, 30, '#C87116')}<text x="899" y="1018" class="body">R — conserved risk-right</text>
{ico('file-lock', 860, 1034, 30, '#6E4A9E')}<text x="899" y="1054" class="body">B — padded encrypted bundle</text>
<line x1="1120" y1="975" x2="1120" y2="1068" stroke="#D5E0E6"/>
<text x="1146" y="984" class="cardtitle">End-to-end invariants</text>
{ico('check-circle', 1146, 1000, 25, '#27856F')}<text x="1180" y="1017" class="body">certificate uniqueness</text>
{ico('check-circle', 1146, 1035, 25, '#27856F')}<text x="1180" y="1052" class="body">risk-right conservation</text>
{ico('check-circle', 1382, 1000, 25, '#27856F')}<text x="1416" y="1017" class="body">persistent monotone state</text>
{ico('check-circle', 1382, 1035, 25, '#27856F')}<text x="1416" y="1052" class="body">at-most-once settlement</text>
</svg>'''


def main() -> None:
    if not SRC.exists():
        raise SystemExit("Extract @phosphor-icons/core@2.1.1 first")
    ICONS.mkdir(parents=True, exist_ok=True)
    for name in NAMES:
        src = SRC / f"{name}-duotone.svg"
        if not src.exists():
            raise SystemExit(f"Missing official icon: {src.name}")
        shutil.copy2(src, ICONS / src.name)
    shutil.copy2(PKG / "LICENSE", OUT / "PHOSPHOR_LICENSE.txt")
    svg = OUT / "aidmesh_architecture_journal.svg"
    svg.write_text(build(), encoding="utf-8")
    manifest = {
        "artifact": svg.name,
        "source": "@phosphor-icons/core",
        "version": "2.1.1",
        "style": "duotone",
        "license": "MIT",
        "repository": "https://github.com/phosphor-icons/core",
        "catalog": "https://phosphoricons.com/?weight=duotone",
        "npm_shasum": "62a4cfbec9772f1a613a647da214fbb96f3ad39d",
        "icons": [{"file": f"icons/{n}-duotone.svg", "sha256": digest(ICONS / f"{n}-duotone.svg")} for n in NAMES],
    }
    manifest["composite_sha256"] = digest(svg)
    (OUT / "SOURCE_MANIFEST.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
