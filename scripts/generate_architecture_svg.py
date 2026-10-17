"""Generate the AidMesh flat-2D architecture and its reusable SVG icon set."""

from __future__ import annotations

from pathlib import Path


OUT = Path("docs/figures/aidmesh_svg")
NAVY = "#16324F"
TEAL = "#168B8C"
AMBER = "#E5A02D"
CORAL = "#D95852"
PALE = "#EEF3F6"
WHITE = "#FFFFFF"
INK = "#203040"


ICONS: dict[str, str] = {
    "beneficiary": f"""
<circle cx="60" cy="38" r="22" fill="#F1B88F" stroke="{NAVY}" stroke-width="3"/>
<path d="M36 39c0-28 15-35 25-35 17 0 28 14 28 35l-9-10c-7-12-20-16-33-5z" fill="{TEAL}" stroke="{NAVY}" stroke-width="3"/>
<path d="M24 112c3-31 16-48 36-48s34 17 37 48" fill="{TEAL}" stroke="{NAVY}" stroke-width="3"/>
<circle cx="52" cy="38" r="2" fill="{NAVY}"/><circle cx="68" cy="38" r="2" fill="{NAVY}"/>
<path d="M52 49q8 7 16 0" fill="none" stroke="{NAVY}" stroke-width="2.5" stroke-linecap="round"/>
<rect x="70" y="61" width="26" height="42" rx="5" fill="{WHITE}" stroke="{NAVY}" stroke-width="3"/>
<rect x="77" y="78" width="12" height="10" rx="2" fill="{TEAL}"/><path d="M80 78v-4a3 3 0 016 0v4" fill="none" stroke="{TEAL}" stroke-width="3"/>
""",
    "phone_lock": f"""
<rect x="30" y="8" width="60" height="104" rx="10" fill="{WHITE}" stroke="{NAVY}" stroke-width="4"/>
<line x1="48" y1="18" x2="72" y2="18" stroke="{NAVY}" stroke-width="3" stroke-linecap="round"/>
<rect x="45" y="48" width="30" height="28" rx="5" fill="{TEAL}"/>
<path d="M51 48v-8a9 9 0 0118 0v8" fill="none" stroke="{TEAL}" stroke-width="6"/>
<circle cx="60" cy="61" r="3" fill="{WHITE}"/>
""",
    "voucher": f"""
<path d="M12 22h96v76H12z" fill="{WHITE}" stroke="{NAVY}" stroke-width="3"/>
<path d="M12 34h96" stroke="{TEAL}" stroke-width="8"/>
<path d="M22 48h42M22 58h34M22 68h28" stroke="{NAVY}" stroke-width="3" stroke-linecap="round"/>
<g fill="{NAVY}"><path d="M73 48h12v12H73zM91 48h12v12H91zM73 66h12v12H73zM91 66h5v5h-5zM98 73h5v5h-5zM88 75h5v5h-5z"/></g>
""",
    "witness": f"""
<circle cx="60" cy="60" r="48" fill="{PALE}" stroke="{TEAL}" stroke-width="4"/>
<circle cx="60" cy="45" r="17" fill="#F1B88F" stroke="{NAVY}" stroke-width="3"/>
<path d="M33 103c4-24 14-35 27-35s24 11 28 35" fill="{TEAL}" stroke="{NAVY}" stroke-width="3"/>
<path d="M45 43c3-17 26-22 32 0-8-5-22-5-32 0z" fill="{NAVY}"/>
<circle cx="99" cy="91" r="15" fill="{WHITE}" stroke="{TEAL}" stroke-width="3"/><path d="M92 91l5 5 9-12" fill="none" stroke="{TEAL}" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>
""",
    "durable_store": f"""
<ellipse cx="60" cy="25" rx="39" ry="15" fill="{PALE}" stroke="{NAVY}" stroke-width="3"/>
<path d="M21 25v58c0 9 18 16 39 16s39-7 39-16V25" fill="{WHITE}" stroke="{NAVY}" stroke-width="3"/>
<path d="M21 50c0 9 18 16 39 16s39-7 39-16M21 74c0 9 18 16 39 16s39-7 39-16" fill="none" stroke="{NAVY}" stroke-width="2"/>
<path d="M60 45l25 10v20c0 16-12 25-25 31-13-6-25-15-25-31V55z" fill="{TEAL}" stroke="{NAVY}" stroke-width="3"/>
<path d="M48 73l8 8 16-19" fill="none" stroke="{WHITE}" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>
""",
    "risk_right": f"""
<circle cx="60" cy="60" r="44" fill="#FFF4DD" stroke="{AMBER}" stroke-width="5"/>
<circle cx="60" cy="60" r="32" fill="{AMBER}" stroke="{NAVY}" stroke-width="3"/>
<text x="60" y="74" text-anchor="middle" font-family="Arial,sans-serif" font-size="42" font-weight="700" fill="{WHITE}">R</text>
""",
    "distribution_site": f"""
<path d="M14 54L60 18l46 36v54H14z" fill="{WHITE}" stroke="{NAVY}" stroke-width="4" stroke-linejoin="round"/>
<path d="M7 57L60 12l53 45" fill="none" stroke="{NAVY}" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/>
<rect x="43" y="67" width="34" height="41" fill="{PALE}" stroke="{NAVY}" stroke-width="3"/>
<rect x="24" y="63" width="15" height="15" fill="{TEAL}"/><rect x="82" y="63" width="15" height="15" fill="{TEAL}"/>
<path d="M84 18v26" stroke="{NAVY}" stroke-width="3"/><path d="M84 18h25v18H84z" fill="{TEAL}" stroke="{NAVY}" stroke-width="3"/><path d="M91 27l5 5 8-10" fill="none" stroke="{WHITE}" stroke-width="3"/>
""",
    "inventory": f"""
<path d="M18 42l42-22 42 22-42 22z" fill="#FFDCA0" stroke="{NAVY}" stroke-width="3"/>
<path d="M18 42v48l42 22V64zM102 42v48l-42 22V64z" fill="{AMBER}" stroke="{NAVY}" stroke-width="3"/>
<path d="M60 64v48M39 31l42 22" stroke="{NAVY}" stroke-width="3"/>
""",
    "receipt": f"""
<path d="M25 10h58l12 12v88l-9-6-9 6-9-6-9 6-9-6-9 6-8-6-8 6z" fill="{WHITE}" stroke="{NAVY}" stroke-width="3"/>
<path d="M83 10v14h12" fill="none" stroke="{NAVY}" stroke-width="3"/>
<path d="M38 42h43M38 54h34M38 66h40" stroke="{NAVY}" stroke-width="3" stroke-linecap="round"/>
<path d="M42 88q9-16 18 0t18 0" fill="none" stroke="{TEAL}" stroke-width="3"/>
<circle cx="91" cy="91" r="17" fill="{TEAL}"/><path d="M84 91l5 5 9-12" fill="none" stroke="{WHITE}" stroke-width="4"/>
""",
    "dtn_relay": f"""
<rect x="36" y="48" width="48" height="48" rx="5" fill="{PALE}" stroke="{NAVY}" stroke-width="3"/>
<path d="M60 48V30M47 35q13-13 26 0M39 27q21-22 42 0" fill="none" stroke="{TEAL}" stroke-width="4" stroke-linecap="round"/>
<circle cx="60" cy="68" r="6" fill="{TEAL}"/><path d="M60 74v12" stroke="{TEAL}" stroke-width="4"/>
""",
    "zk_gate": f"""
<g fill="{WHITE}" stroke="{NAVY}" stroke-width="3"><circle cx="24" cy="30" r="8"/><circle cx="24" cy="60" r="8"/><circle cx="24" cy="90" r="8"/><circle cx="62" cy="45" r="8"/><circle cx="62" cy="78" r="8"/></g>
<path d="M32 30l22 15M32 60l22-15M32 60l22 18M32 90l22-12M70 45l22 15M70 78l22-18" fill="none" stroke="{TEAL}" stroke-width="3"/>
<circle cx="100" cy="60" r="16" fill="{TEAL}"/><path d="M92 60l6 6 11-15" fill="none" stroke="{WHITE}" stroke-width="4"/>
""",
    "gateway": f"""
<path d="M60 26L37 108M60 26l23 82M44 82h32M40 96h40M48 67h24" fill="none" stroke="{NAVY}" stroke-width="4" stroke-linecap="round"/>
<circle cx="60" cy="26" r="6" fill="{TEAL}"/>
<path d="M47 20q13-13 26 0M39 12q21-22 42 0" fill="none" stroke="{TEAL}" stroke-width="4" stroke-linecap="round"/>
""",
    "ledger": f"""
<g fill="{WHITE}" stroke="{NAVY}" stroke-width="3"><rect x="5" y="43" width="25" height="34" rx="4"/><rect x="47" y="43" width="25" height="34" rx="4"/><rect x="89" y="43" width="25" height="34" rx="4"/></g>
<path d="M30 60h17M72 60h17" stroke="{NAVY}" stroke-width="3"/><circle cx="38" cy="60" r="3" fill="{TEAL}"/><circle cx="80" cy="60" r="3" fill="{TEAL}"/>
<path d="M101 52l7 7-12 12" fill="none" stroke="{TEAL}" stroke-width="4"/>
""",
    "conflict": f"""
<circle cx="60" cy="60" r="45" fill="#FFF0EF" stroke="{CORAL}" stroke-width="5"/>
<path d="M40 40l40 40M80 40L40 80" stroke="{CORAL}" stroke-width="9" stroke-linecap="round"/>
""",
}


def svg_document(body: str, *, view_box: str = "0 0 120 120") -> str:
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view_box}" role="img">
<style>text{{font-family:Arial,Helvetica,sans-serif}}</style>
{body}
</svg>
"""


def use_icon(name: str, x: int, y: int, size: int) -> str:
    return f'<use href="#{name}" x="{x}" y="{y}" width="{size}" height="{size}"/>'


def full_architecture() -> str:
    symbols = "\n".join(
        f'<symbol id="{name}" viewBox="0 0 120 120">{body}</symbol>'
        for name, body in ICONS.items()
    )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1600 930" role="img" aria-labelledby="title desc">
<title id="title">AidMesh risk-bounded settlement architecture</title>
<desc id="desc">Two-dimensional scientific diagram of quorum and fallback redemption paths converging through delayed transport into at-most-once EVM settlement.</desc>
<defs>
{symbols}
<marker id="arrow-teal" markerWidth="10" markerHeight="10" refX="9" refY="3" orient="auto"><path d="M0 0L9 3 0 6z" fill="{TEAL}"/></marker>
<marker id="arrow-amber" markerWidth="10" markerHeight="10" refX="9" refY="3" orient="auto"><path d="M0 0L9 3 0 6z" fill="{AMBER}"/></marker>
<marker id="arrow-navy" markerWidth="10" markerHeight="10" refX="9" refY="3" orient="auto"><path d="M0 0L9 3 0 6z" fill="{NAVY}"/></marker>
<marker id="arrow-red" markerWidth="10" markerHeight="10" refX="9" refY="3" orient="auto"><path d="M0 0L9 3 0 6z" fill="{CORAL}"/></marker>
</defs>
<rect width="1600" height="930" fill="{WHITE}"/>
<style>
.title{{font:700 27px Arial,sans-serif;fill:{WHITE}}}.h2{{font:700 25px Arial,sans-serif;fill:{NAVY}}}.h3{{font:700 20px Arial,sans-serif;fill:{INK}}}.label{{font:500 17px Arial,sans-serif;fill:{INK}}}.small{{font:500 14px Arial,sans-serif;fill:{INK}}}.formula{{font:italic 700 23px Georgia,serif;fill:{INK}}}.frame{{fill:{WHITE};stroke:{NAVY};stroke-width:2.5}}.trust{{fill:none;stroke-width:2.5;stroke-dasharray:9 8}}
</style>

<!-- column frames and headers -->
<rect class="frame" x="14" y="14" width="310" height="820" rx="18"/><rect x="14" y="14" width="310" height="62" rx="18" fill="{NAVY}"/><text x="35" y="55" font-family="Arial" font-size="23" font-weight="700" fill="{WHITE}">1  OFFLINE REDEMPTION</text>
<rect class="frame" x="338" y="14" width="750" height="820" rx="18"/><rect x="338" y="14" width="750" height="62" rx="18" fill="{TEAL}"/><text class="title" x="465" y="55">2  PARTITIONED SAFETY DOMAIN</text>
<rect class="frame" x="1102" y="14" width="484" height="820" rx="18"/><rect x="1102" y="14" width="484" height="62" rx="18" fill="{NAVY}"/><text class="title" x="1145" y="55">3  DELAYED SETTLEMENT</text>

<!-- beneficiary -->
{use_icon('beneficiary', 55, 105, 210)}
{use_icon('voucher', 95, 340, 145)}
<text class="h3" x="169" y="510" text-anchor="middle">Voucher + hidden entitlement</text>
<rect x="58" y="535" width="222" height="104" rx="14" fill="{PALE}" stroke="{NAVY}" stroke-width="2.5"/>{use_icon('phone_lock', 78, 545, 82)}
<text class="h3" x="183" y="575">Private credential</text><text class="small" x="183" y="603">Merkle membership</text>

<!-- quorum -->
<rect class="trust" x="360" y="92" width="560" height="350" rx="16" stroke="{TEAL}"/><text x="380" y="126" font-family="Arial" font-size="25" font-weight="700" fill="{TEAL}">QUORUM PATH</text>
{use_icon('witness', 382, 145, 100)}{use_icon('witness', 555, 145, 100)}{use_icon('witness', 382, 305, 100)}{use_icon('witness', 555, 305, 100)}{use_icon('durable_store', 485, 215, 105)}
<text class="small" x="535" y="335" text-anchor="middle">persist before sign</text>
<path d="M480 185L505 235M555 190L550 230M480 345L510 300M555 345L550 305" fill="none" stroke="{TEAL}" stroke-width="3" stroke-dasharray="6 5" marker-end="url(#arrow-teal)"/>
<rect x="700" y="165" width="185" height="190" rx="12" fill="{PALE}" stroke="{TEAL}" stroke-width="3"/><text class="h3" x="792" y="205" text-anchor="middle">Certificate</text><text class="h2" x="792" y="250" text-anchor="middle">q = 3 of K = 4</text><text class="formula" x="792" y="315" text-anchor="middle">2q &gt; K + f</text>
<path d="M650 225L700 225M650 275L700 275M650 330L700 330" fill="none" stroke="{TEAL}" stroke-width="5" marker-end="url(#arrow-teal)"/>

<!-- fallback -->
<rect class="trust" x="360" y="462" width="560" height="325" rx="16" stroke="{AMBER}"/><text x="380" y="500" font-family="Arial" font-size="25" font-weight="700" fill="{AMBER}">FALLBACK PATH</text>
{use_icon('distribution_site', 382, 520, 90)}{use_icon('distribution_site', 495, 520, 90)}{use_icon('distribution_site', 608, 520, 90)}{use_icon('distribution_site', 721, 520, 90)}
{use_icon('risk_right', 397, 582, 58)}{use_icon('risk_right', 510, 582, 58)}{use_icon('risk_right', 623, 582, 58)}{use_icon('risk_right', 736, 582, 58)}
<text class="small" x="425" y="663" text-anchor="middle">R_A</text><text class="small" x="538" y="663" text-anchor="middle">R_B</text><text class="small" x="651" y="663" text-anchor="middle">R_C</text><text class="small" x="764" y="663" text-anchor="middle">R_D</text>
<path d="M795 607h80" stroke="{AMBER}" stroke-width="5" marker-end="url(#arrow-amber)"/><text class="small" x="835" y="585" text-anchor="middle">debit before transfer</text>
<rect x="380" y="690" width="510" height="68" rx="12" fill="#FFF7E8" stroke="{AMBER}" stroke-width="2.5"/><text class="formula" x="635" y="733" text-anchor="middle">I = R + T + S + E + V</text>

<!-- convergence at site -->
{use_icon('distribution_site', 945, 210, 125)}<text class="h3" x="1007" y="195" text-anchor="middle">Distribution site</text>
{use_icon('inventory', 975, 350, 72)}<text class="small" x="1012" y="435" text-anchor="middle">physical release</text>
{use_icon('receipt', 968, 500, 85)}<text class="small" x="1012" y="600" text-anchor="middle">signed padded receipt</text>
<path d="M885 260h70" stroke="{TEAL}" stroke-width="6" marker-end="url(#arrow-teal)"/><path d="M885 610h70" stroke="{AMBER}" stroke-width="6" marker-end="url(#arrow-amber)"/>

<!-- DTN and settlement -->
<text class="h3" x="1344" y="112" text-anchor="middle">Store-carry-forward (DTN relays)</text>
{use_icon('dtn_relay', 1140, 125, 78)}{use_icon('dtn_relay', 1265, 125, 78)}{use_icon('dtn_relay', 1390, 125, 78)}
<path d="M1215 165h45M1340 165h45" stroke="{NAVY}" stroke-width="4" stroke-dasharray="5 6" marker-end="url(#arrow-navy)"/>
<rect x="1140" y="240" width="410" height="105" rx="13" fill="{PALE}" stroke="{NAVY}" stroke-width="2.5"/>{use_icon('receipt', 1155, 248, 80)}<text class="h3" x="1260" y="285">Encrypted fixed-size</text><text class="h3" x="1260" y="315">receipt bundle</text>
<path d="M1345 205v33M1345 345v35" stroke="{NAVY}" stroke-width="4" marker-end="url(#arrow-navy)"/>
<rect x="1140" y="382" width="410" height="105" rx="13" fill="{WHITE}" stroke="{TEAL}" stroke-width="2.5"/>{use_icon('zk_gate', 1160, 392, 82)}<text class="h3" x="1260" y="445">Groth16 proof gate</text>
<path d="M1345 488v35" stroke="{NAVY}" stroke-width="4" marker-end="url(#arrow-navy)"/>
<rect x="1140" y="525" width="410" height="100" rx="13" fill="{WHITE}" stroke="{TEAL}" stroke-width="2.5"/>{use_icon('gateway', 1160, 530, 82)}<text class="h3" x="1260" y="578">Authorized gateway</text>
<rect x="1140" y="662" width="410" height="115" rx="13" fill="{PALE}" stroke="{NAVY}" stroke-width="2.5"/>{use_icon('ledger', 1165, 660, 105)}<text class="h3" x="1290" y="700">Blockchain / EVM ledger</text><text class="small" x="1290" y="730">unique nullifier  →  at-most-once credit</text>
<path d="M1345 625v35" stroke="{NAVY}" stroke-width="4" marker-end="url(#arrow-navy)"/>
{use_icon('conflict', 1470, 760, 72)}<path d="M1485 735q-70-15-55-85" fill="none" stroke="{CORAL}" stroke-width="4" stroke-dasharray="6 5" marker-end="url(#arrow-red)"/><text x="1435" y="814" font-family="Arial" font-size="16" font-weight="700" fill="{CORAL}" text-anchor="middle">replay → zero credit</text>

<!-- legend -->
<rect x="14" y="850" width="1572" height="66" rx="16" fill="{PALE}" stroke="{NAVY}" stroke-width="2"/>
<path d="M45 882h55" stroke="{TEAL}" stroke-width="6" marker-end="url(#arrow-teal)"/><text class="small" x="112" y="888">safe quorum</text>
<path d="M245 882h55" stroke="{AMBER}" stroke-width="6" marker-end="url(#arrow-amber)"/><text class="small" x="312" y="888">bounded-risk fallback</text>
<path d="M515 882h55" stroke="{NAVY}" stroke-width="4" stroke-dasharray="5 6" marker-end="url(#arrow-navy)"/><text class="small" x="582" y="888">delayed transport</text>
<path d="M775 882h55" stroke="{CORAL}" stroke-width="4" stroke-dasharray="6 5" marker-end="url(#arrow-red)"/><text class="small" x="842" y="888">replay/conflict</text>
{use_icon('risk_right', 1020, 858, 48)}<text class="small" x="1075" y="888">risk-right</text><rect x="1195" y="865" width="40" height="34" fill="none" stroke="{TEAL}" stroke-width="2.5" stroke-dasharray="7 5"/><text class="small" x="1245" y="888">trust boundary</text>
</svg>
"""


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, body in ICONS.items():
        (OUT / f"icon_{name}.svg").write_text(svg_document(body), encoding="utf-8")
    (OUT / "aidmesh_architecture_2d.svg").write_text(full_architecture(), encoding="utf-8")
    print(f"generated {len(ICONS)} icons and architecture at {OUT}")


if __name__ == "__main__":
    main()
