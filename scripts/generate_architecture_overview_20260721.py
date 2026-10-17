"""Generate the corrected AidMesh overview architecture figure (SVG + PNG)."""

from __future__ import annotations

from pathlib import Path

from svglib.svglib import svg2rlg
from reportlab.graphics import renderPDF, renderPM


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "docs" / "figures" / "aidmesh_architecture_overview_20260721"
PAPER_FIG = ROOT / "source" / "overleaf_q1_architecture_cursor" / "figures" / "aidmesh_architecture.png"
PAPER_PDF = ROOT / "source" / "overleaf_q1_architecture_cursor" / "figures" / "aidmesh_architecture.pdf"


def build_svg() -> str:
    return """<svg xmlns="http://www.w3.org/2000/svg" width="2000" height="1180" viewBox="0 0 2000 1180">
<defs>
  <filter id="shadow" x="-15%" y="-15%" width="140%" height="150%">
    <feDropShadow dx="0" dy="3" stdDeviation="6" flood-color="#17364D" flood-opacity=".12"/>
  </filter>
  <marker id="arr-navy" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto">
    <path d="M0 0L10 5L0 10Z" fill="#173F5F"/>
  </marker>
  <marker id="arr-teal" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto">
    <path d="M0 0L10 5L0 10Z" fill="#27856F"/>
  </marker>
  <marker id="arr-amber" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto">
    <path d="M0 0L10 5L0 10Z" fill="#D37A19"/>
  </marker>
  <marker id="arr-purple" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto">
    <path d="M0 0L10 5L0 10Z" fill="#6E4A9E"/>
  </marker>
  <marker id="arr-green" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto">
    <path d="M0 0L10 5L0 10Z" fill="#2A7A52"/>
  </marker>
  <style>
    text { font-family: "Segoe UI", Inter, Arial, sans-serif; fill: #17364D; }
    .title { font-size: 30px; font-weight: 700; letter-spacing: -.3px; }
    .subtitle { font-size: 14px; fill: #5B7386; }
    .zone { font-size: 17px; font-weight: 700; letter-spacing: .25px; }
    .cardtitle { font-size: 15px; font-weight: 700; }
    .body { font-size: 12.5px; fill: #3F5D72; }
    .small { font-size: 11px; fill: #60788A; }
    .step { font-size: 11px; font-weight: 800; fill: white; }
    .math { font-family: Cambria, "Times New Roman", serif; font-size: 13px; font-weight: 600; }
    .pill { font-size: 10px; font-weight: 700; letter-spacing: .35px; }
  </style>
</defs>

<rect width="2000" height="1180" fill="#F8FAFB"/>

<!-- header -->
<text x="48" y="44" class="title">AidMesh system architecture</text>
<text x="48" y="68" class="subtitle">Private, risk-bounded multi-site redemption under network partitions · quorum safety · conserved fallback rights · delay-tolerant settlement</text>
<rect x="1540" y="22" width="410" height="34" rx="17" fill="#E6F3F1"/>
<text x="1745" y="44" class="pill" text-anchor="middle" fill="#1D7564">OFFLINE AUTHORIZATION → EVENTUAL SETTLEMENT</text>

<!-- zone backgrounds -->
<rect x="34" y="92" width="700" height="860" rx="24" fill="#F2F7FA" stroke="#66889F" stroke-width="1.5" stroke-dasharray="9 6"/>
<rect x="754" y="92" width="430" height="860" rx="24" fill="#F8F4FA" stroke="#8A6BAA" stroke-width="1.5" stroke-dasharray="9 6"/>
<rect x="1204" y="92" width="500" height="860" rx="24" fill="#F3F8F3" stroke="#5F8B6D" stroke-width="1.5" stroke-dasharray="9 6"/>
<rect x="1724" y="92" width="242" height="860" rx="24" fill="#FFFBF2" stroke="#A98B54" stroke-width="1.5" stroke-dasharray="7 5"/>

<text x="384" y="124" class="zone" text-anchor="middle">Field / disconnected environment</text>
<text x="969" y="124" class="zone" text-anchor="middle">DTN / temporal-contact network</text>
<text x="1454" y="124" class="zone" text-anchor="middle">Connected backbone &amp; settlement</text>
<text x="1845" y="124" class="zone" text-anchor="middle">Assurance</text>
<text x="1845" y="144" class="small" text-anchor="middle">claims &amp; boundaries</text>

<!-- enrollment -->
<rect x="58" y="148" width="652" height="58" rx="14" fill="#E8F1F6" stroke="#9FB6C4"/>
<text x="78" y="172" class="cardtitle">0 · Enrollment &amp; allocation (off protocol)</text>
<text x="78" y="192" class="body">commit credential root R_C and entitlement root R_X → beneficiary derives voucher V with P1 proof π</text>

<!-- beneficiary -->
<rect x="58" y="224" width="250" height="132" rx="16" fill="white" stroke="#A7BECC" filter="url(#shadow)"/>
<text x="78" y="250" class="cardtitle">1 · Beneficiary client</text>
<text x="78" y="272" class="body">presents voucher V = (D, z, k, v, n, mode, τ, t, π)</text>
<text x="78" y="292" class="body">secret s never leaves device</text>
<text x="78" y="312" class="body">no online spent-set lookup</text>
<rect x="78" y="324" width="210" height="22" rx="6" fill="#EEF5F8"/>
<text x="88" y="339" class="small">credential · entitlement · nullifier n</text>

<path d="M308 280 H348" fill="none" stroke="#173F5F" stroke-width="2.5" marker-end="url(#arr-navy)"/>
<circle cx="328" cy="280" r="14" fill="#173F5F" stroke="white" stroke-width="2.5"/>
<text x="328" y="285" class="step" text-anchor="middle">1</text>

<!-- site with P1 verify -->
<rect x="360" y="224" width="350" height="132" rx="16" fill="white" stroke="#27856F" stroke-width="2" filter="url(#shadow)"/>
<text x="380" y="250" class="cardtitle">2 · Distribution site S_i</text>
<text x="380" y="272" class="body">verify header, cell, epoch, category, value</text>
<text x="380" y="292" class="body" fill="#1D7564" font-weight="700">verify P1 membership: Verify(π, x) = 1  ← BEFORE release</text>
<text x="380" y="312" class="body">check local nullifier / fallback-tag freshness</text>
<rect x="380" y="324" width="310" height="22" rx="6" fill="#E6F3F1"/>
<text x="390" y="339" class="small">Groth16 binds roots, nullifier, fallback mode — not global cap</text>

<path d="M535 356 V392" fill="none" stroke="#173F5F" stroke-width="2.5" marker-end="url(#arr-navy)"/>
<circle cx="535" cy="374" r="14" fill="#173F5F" stroke="white" stroke-width="2.5"/>
<text x="535" y="379" class="step" text-anchor="middle">2</text>

<!-- split -->
<circle cx="535" cy="418" r="22" fill="white" stroke="#173F5F" stroke-width="1.5"/>
<text x="535" y="423" class="small" text-anchor="middle" font-weight="700">OR</text>

<!-- quorum path -->
<rect x="58" y="452" width="320" height="250" rx="18" fill="#F4FBF8" stroke="#52A08A" stroke-width="1.4"/>
<rect x="58" y="452" width="320" height="42" rx="18" fill="#DFF2EB"/>
<text x="78" y="480" class="cardtitle" fill="#27856F">2a · Safe quorum path</text>
<text x="218" y="480" class="math" fill="#27856F">2q &gt; K + f</text>
<rect x="78" y="508" width="280" height="78" rx="12" fill="white" stroke="#A8D2C4"/>
<text x="92" y="530" class="body">Witness committee W = {w₁…w_K}</text>
<text x="92" y="550" class="body">persist-before-sign on (campaign, epoch, n)</text>
<text x="92" y="570" class="body">idempotent replay; conflict → reject</text>
<rect x="78" y="598" width="280" height="44" rx="10" fill="#DFF2EB"/>
<text x="92" y="626" class="cardtitle">Quorum certificate C</text>
<text x="250" y="626" class="small">q distinct shares</text>
<circle cx="68" cy="520" r="14" fill="#27856F" stroke="white" stroke-width="2.5"/>
<text x="68" y="525" class="step" text-anchor="middle">a</text>

<!-- fallback path -->
<rect x="392" y="452" width="318" height="250" rx="18" fill="#FFF9EE" stroke="#DC9A4B" stroke-width="1.4"/>
<rect x="392" y="452" width="318" height="42" rx="18" fill="#FAE9C9"/>
<text x="412" y="480" class="cardtitle" fill="#C87116">2b · Bounded fallback path</text>
<rect x="412" y="508" width="278" height="78" rx="12" fill="white" stroke="#E6C38E"/>
<text x="426" y="530" class="body">Risk-right ledger R_m per site</text>
<text x="426" y="550" class="body">atomic debit BEFORE inventory release</text>
<text x="426" y="570" class="body" fill="#A06D18" font-weight="700">exposure / cap ≤ 1 at site</text>
<rect x="412" y="598" width="130" height="44" rx="10" fill="#FAE9C9"/>
<text x="426" y="618" class="body">static cap at S_i</text>
<text x="426" y="636" class="math">R_i ← R_i − v</text>
<rect x="552" y="598" width="138" height="44" rx="10" fill="#FFF3D6" stroke="#E6C38E"/>
<text x="566" y="618" class="body">transferable placement</text>
<text x="566" y="636" class="math">T: m_s → m_d</text>
<circle cx="402" cy="520" r="14" fill="#D37A19" stroke="white" stroke-width="2.5"/>
<text x="402" y="525" class="step" text-anchor="middle">b</text>

<!-- merge and release -->
<path d="M218 702 C218 738 360 738 420 770" fill="none" stroke="#27856F" stroke-width="2.5" marker-end="url(#arr-teal)"/>
<path d="M551 702 C551 738 470 738 420 770" fill="none" stroke="#D37A19" stroke-width="2.5" stroke-dasharray="8 5" marker-end="url(#arr-amber)"/>
<rect x="300" y="778" width="280" height="88" rx="16" fill="white" stroke="#9FB6C4" filter="url(#shadow)"/>
<text x="320" y="804" class="cardtitle">3 · Physical release + signed receipt ρ</text>
<text x="320" y="826" class="body">reserve inventory · record nullifier n</text>
<text x="320" y="846" class="body">enqueue encrypted receipt bundle</text>
<circle cx="290" cy="822" r="14" fill="#173F5F" stroke="white" stroke-width="2.5"/>
<text x="290" y="827" class="step" text-anchor="middle">3</text>

<!-- receipt bundle to DTN -->
<path d="M580 822 H754" fill="none" stroke="#6E4A9E" stroke-width="2.5" stroke-dasharray="9 6" marker-end="url(#arr-purple)"/>
<circle cx="667" cy="822" r="14" fill="#6E4A9E" stroke="white" stroke-width="2.5"/>
<text x="667" y="827" class="step" text-anchor="middle">4</text>

<!-- field footer -->
<rect x="58" y="886" width="652" height="48" rx="12" fill="#E7F0F5" stroke="#ABC0CD"/>
<text x="78" y="908" class="math">Safe domain:</text>
<text x="168" y="908" class="body">quorum ⇒ Theorem conflict exclusion</text>
<text x="78" y="926" class="math">Fallback:</text>
<text x="148" y="926" class="body">I = Σ R + T + S + E + V conserved · no cap violation</text>

<!-- DTN column -->
<rect x="778" y="160" width="382" height="120" rx="16" fill="white" stroke="#B8A4CB" filter="url(#shadow)"/>
<text x="798" y="186" class="cardtitle">Encrypted receipt bundle B</text>
<text x="798" y="208" class="body">signed receipt ρ · path evidence · domain D</text>
<text x="798" y="228" class="body">fixed-shape payload · AES-GCM audit detail separate</text>
<text x="798" y="248" class="body">privacy knobs applied later at gateway batching</text>

<rect x="778" y="300" width="382" height="300" rx="18" fill="#FBF9FC" stroke="#B8A4CB"/>
<text x="969" y="330" class="cardtitle" text-anchor="middle">Temporal-contact graph G(t) = (V, E(t))</text>
<text x="969" y="352" class="small" text-anchor="middle">store · carry · forward under intermittent contacts</text>
<circle cx="840" cy="430" r="34" fill="#F0E8F6" stroke="#A68BBC"/><text x="840" y="435" class="small" text-anchor="middle">relay</text>
<circle cx="969" cy="400" r="34" fill="#F0E8F6" stroke="#A68BBC"/><text x="969" y="405" class="small" text-anchor="middle">ferry</text>
<circle cx="1098" cy="430" r="34" fill="#F0E8F6" stroke="#A68BBC"/><text x="1098" y="435" class="small" text-anchor="middle">tower</text>
<path d="M874 430 H935" stroke="#8A65AA" stroke-width="2" stroke-dasharray="6 5" marker-end="url(#arr-purple)"/>
<path d="M1003 400 V396 L1098 430" stroke="#8A65AA" stroke-width="2" stroke-dasharray="6 5" marker-end="url(#arr-purple)"/>
<text x="969" y="500" class="body" text-anchor="middle">slot-causal forwarding · finite buffer · TTL</text>
<text x="969" y="520" class="small" text-anchor="middle">Epidemic / risk-priority routing (application policy)</text>
<circle cx="754" cy="450" r="14" fill="#6E4A9E" stroke="white" stroke-width="2.5"/>
<text x="754" y="455" class="step" text-anchor="middle">5</text>

<rect x="778" y="620" width="382" height="110" rx="16" fill="white" stroke="#B8A4CB" filter="url(#shadow)"/>
<text x="798" y="646" class="cardtitle">Eventual delivery to gateway g</text>
<text x="798" y="668" class="body">∃ path_t(B, g) when temporal contacts exist</text>
<text x="798" y="688" class="body">LH10 negative control: physical release but zero gateway reach</text>
<text x="798" y="708" class="small">delay measured; synchrony not assumed</text>
<circle cx="754" cy="675" r="14" fill="#6E4A9E" stroke="white" stroke-width="2.5"/>
<text x="754" y="680" class="step" text-anchor="middle">6</text>

<!-- E2E attrition -->
<rect x="778" y="748" width="382" height="186" rx="16" fill="#F0E8F6" stroke="#B8A4CB"/>
<text x="798" y="774" class="cardtitle">E2E attrition pipeline (registered endpoints)</text>
<rect x="798" y="790" width="70" height="44" rx="8" fill="white" stroke="#8A65AA"/><text x="833" y="808" class="small" text-anchor="middle">request</text><text x="833" y="824" class="small" text-anchor="middle">delivered</text>
<text x="878" y="818" class="body">→</text>
<rect x="892" y="790" width="70" height="44" rx="8" fill="white" stroke="#8A65AA"/><text x="927" y="808" class="small" text-anchor="middle">physical</text><text x="927" y="824" class="small" text-anchor="middle">release</text>
<text x="972" y="818" class="body">→</text>
<rect x="986" y="790" width="70" height="44" rx="8" fill="white" stroke="#8A65AA"/><text x="1021" y="808" class="small" text-anchor="middle">gateway</text><text x="1021" y="824" class="small" text-anchor="middle">receipt</text>
<text x="1066" y="818" class="body">→</text>
<rect x="1080" y="790" width="70" height="44" rx="8" fill="#E5F2E8" stroke="#2A7A52"/><text x="1115" y="808" class="small" text-anchor="middle">verified</text><text x="1115" y="824" class="small" text-anchor="middle">settlement</text>
<text x="798" y="862" class="body">Historical domain-v1 evidence is archived and excluded from canonical totals</text>
<text x="798" y="882" class="body">Canonical domain-v2-tagged E2E: report only from verified campaign manifest</text>
<text x="798" y="908" class="small">completion ≠ settlement when gateway is temporally unreachable</text>

<path d="M1160 822 H1204" fill="none" stroke="#2A7A52" stroke-width="2.5" marker-end="url(#arr-green)"/>
<circle cx="1182" cy="822" r="14" fill="#2A7A52" stroke="white" stroke-width="2.5"/>
<text x="1182" y="827" class="step" text-anchor="middle">7</text>

<!-- backbone -->
<rect x="1228" y="160" width="456" height="108" rx="16" fill="white" stroke="#9ABDA5" filter="url(#shadow)"/>
<text x="1248" y="186" class="cardtitle">8 · Settlement gateway</text>
<text x="1248" y="208" class="body">authenticate transport · deduplicate · rate-limit</text>
<text x="1248" y="228" class="body">8-hour fixed-slot batching · shuffle · Merkle batch root</text>
<text x="1248" y="248" class="body">ledger-view privacy; gateway/site observers retain more metadata</text>

<path d="M1456 268 V302" fill="none" stroke="#2A7A52" stroke-width="2.5" marker-end="url(#arr-green)"/>
<circle cx="1456" cy="285" r="14" fill="#2A7A52" stroke="white" stroke-width="2.5"/>
<text x="1456" y="290" class="step" text-anchor="middle">9</text>

<rect x="1228" y="312" width="456" height="118" rx="16" fill="white" stroke="#6FA281" filter="url(#shadow)"/>
<text x="1248" y="338" class="cardtitle">10 · Groth16 re-verification (settlement gate)</text>
<text x="1248" y="360" class="body">re-verify π against public statement x (roots, nullifier, mode)</text>
<text x="1248" y="380" class="body">validate site signature · path evidence · release time</text>
<text x="1248" y="400" class="small">P1 already verified locally at site; gateway/EVM checks bind settlement</text>

<path d="M1456 430 V464" fill="none" stroke="#2A7A52" stroke-width="2.5" marker-end="url(#arr-green)"/>
<circle cx="1456" cy="447" r="14" fill="#2A7A52" stroke="white" stroke-width="2.5"/>
<text x="1456" y="452" class="step" text-anchor="middle">10</text>

<rect x="1228" y="474" width="456" height="148" rx="16" fill="white" stroke="#5F8B6D" filter="url(#shadow)"/>
<text x="1248" y="500" class="cardtitle">11 · Local EVM settlement (prototype)</text>
<text x="1248" y="522" class="body">first-valid-wins per (campaign, epoch, n)</text>
<text x="1248" y="542" class="body">reject duplicate nullifier · debit campaign escrow · credit site</text>
<text x="1248" y="562" class="body">conflicting receipt ⇒ zero additional credit</text>
<text x="1248" y="582" class="body">NOT production public-chain deployment in current evidence</text>
<rect x="1248" y="592" width="416" height="22" rx="6" fill="#E5F2E8"/>
<text x="1258" y="607" class="small">cap enforced at site fallback · escrow/nullifier enforced here</text>

<path d="M1456 622 V656" fill="none" stroke="#2A7A52" stroke-width="2.5" marker-end="url(#arr-green)"/>
<circle cx="1456" cy="639" r="14" fill="#2A7A52" stroke="white" stroke-width="2.5"/>
<text x="1456" y="644" class="step" text-anchor="middle">11</text>

<rect x="1228" y="666" width="456" height="108" rx="16" fill="#E5F2E8" stroke="#5F8B6D" filter="url(#shadow)"/>
<text x="1248" y="692" class="cardtitle">12 · Observer / reproducible evidence</text>
<text x="1248" y="714" class="body">immutable event logs · checksum manifests · registered analysis</text>
<text x="1248" y="734" class="body">ledger-view batch commitments · encrypted audit payloads separate</text>
<text x="1248" y="754" class="small">three vantages measured: ledger · gateway · site</text>

<!-- assurance sidebar -->
<rect x="1744" y="160" width="202" height="150" rx="14" fill="white" stroke="#D7C398"/>
<text x="1760" y="186" class="cardtitle">Trust inputs</text>
<text x="1760" y="210" class="small">• signed roots R_C, R_X</text>
<text x="1760" y="230" class="small">• committee in header H</text>
<text x="1760" y="250" class="small">• campaign escrow</text>
<text x="1760" y="270" class="small">• Groth16 SRS (dev)</text>
<text x="1760" y="290" class="small">• authorized site keys</text>

<rect x="1744" y="326" width="202" height="150" rx="14" fill="white" stroke="#D7C398"/>
<text x="1760" y="352" class="cardtitle">What is proved</text>
<text x="1760" y="376" class="small">✓ quorum conflict bound</text>
<text x="1760" y="396" class="small">✓ rights conservation</text>
<text x="1760" y="416" class="small">✓ bounded site exposure</text>
<text x="1760" y="436" class="small">✓ at-most-once credit</text>
<text x="1760" y="456" class="small">✗ field aid delivery</text>

<rect x="1744" y="492" width="202" height="150" rx="14" fill="white" stroke="#D7C398"/>
<text x="1760" y="518" class="cardtitle">Explicit non-claims</text>
<text x="1760" y="542" class="small">• public-chain finality</text>
<text x="1760" y="562" class="small">• site/network anonymity</text>
<text x="1760" y="582" class="small">• universal routing win</text>
<text x="1760" y="602" class="small">• humanitarian impact</text>
<text x="1760" y="622" class="small">• mobile-ready P1 latency</text>

<rect x="1744" y="658" width="202" height="116" rx="14" fill="#FFF7E8" stroke="#D7C398"/>
<text x="1760" y="684" class="cardtitle">Key formulas</text>
<text x="1760" y="708" class="math">2q &gt; K + f</text>
<text x="1760" y="728" class="math">n = H(s, …, v)</text>
<text x="1760" y="748" class="math">I = R+T+S+E+V</text>

<!-- legend -->
<rect x="34" y="972" width="1932" height="78" rx="16" fill="#173F5F"/>
<text x="58" y="1002" fill="white" class="cardtitle">Legend</text>
<line x1="150" y1="998" x2="210" y2="998" stroke="#27856F" stroke-width="4" marker-end="url(#arr-teal)"/>
<text x="220" y="1002" fill="#DCEAF2" class="body">safe quorum path</text>
<line x1="380" y1="998" x2="440" y2="998" stroke="#D37A19" stroke-width="4" stroke-dasharray="8 5" marker-end="url(#arr-amber)"/>
<text x="450" y="1002" fill="#DCEAF2" class="body">bounded fallback path</text>
<line x1="640" y1="998" x2="700" y2="998" stroke="#6E4A9E" stroke-width="3" stroke-dasharray="9 6" marker-end="url(#arr-purple)"/>
<text x="710" y="1002" fill="#DCEAF2" class="body">DTN transport</text>
<line x1="860" y1="998" x2="920" y2="998" stroke="#2A7A52" stroke-width="3" marker-end="url(#arr-green)"/>
<text x="930" y="1002" fill="#DCEAF2" class="body">settlement backbone</text>
<text x="58" y="1030" fill="#B8D4E8" class="small">P1 proof π is verified at the site before release and re-checked at settlement · exposure cap is enforced locally on fallback debit, not inside the SNARK circuit</text>
</svg>"""


def write_assets() -> dict[str, Path]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    svg_path = OUT_DIR / "aidmesh_architecture_overview.svg"
    png_path = OUT_DIR / "aidmesh_architecture_overview.png"
    pdf_path = OUT_DIR / "aidmesh_architecture_overview.pdf"

    svg_path.write_text(build_svg(), encoding="utf-8")
    drawing = svg2rlg(str(svg_path))
    if drawing is None:
        raise RuntimeError(f"Failed to parse SVG: {svg_path}")
    renderPM.drawToFile(drawing, str(png_path), fmt="PNG", dpi=180)
    renderPDF.drawToFile(drawing, str(pdf_path))

    import shutil

    shutil.copy2(png_path, PAPER_FIG)
    shutil.copy2(pdf_path, PAPER_PDF)
    shutil.copy2(svg_path, PAPER_FIG.with_suffix(".svg"))

    return {"svg": svg_path, "png": png_path, "pdf": pdf_path, "paper_png": PAPER_FIG, "paper_pdf": PAPER_PDF}


def main() -> None:
    paths = write_assets()
    for label, path in paths.items():
        print(f"{label}: {path}")


if __name__ == "__main__":
    main()
