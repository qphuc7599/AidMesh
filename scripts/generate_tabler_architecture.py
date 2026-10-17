"""Build the AidMesh architecture plate from official Tabler SVG assets.

The script intentionally copies every third-party icon as an individual SVG and
also embeds the same vector paths into a self-contained architecture SVG.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "figures" / "aidmesh_tabler_architecture_20260719"
PKG = OUT / "package" / "package"
SOURCE = PKG / "icons" / "outline"
ICONS = OUT / "icons"

ICON_NAMES = [
    "user-heart", "device-mobile-check", "id-badge-2", "shield-check",
    "database", "users-group", "building-warehouse", "building-store",
    "package", "coin", "coins", "file-certificate", "file-check",
    "antenna-bars-5", "access-point", "cloud-off", "route", "network",
    "lock", "key", "binary-tree", "hierarchy-3", "blocks", "cube",
    "alert-triangle", "circle-x", "receipt", "receipt-2",
    "file-description", "server", "wallet", "transfer", "arrows-exchange",
    "wifi-off", "satellite",
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def icon_inner(name: str) -> str:
    text = (ICONS / f"{name}.svg").read_text(encoding="utf-8")
    match = re.search(r"<svg[^>]*>(.*)</svg>", text, flags=re.S)
    if not match:
        raise ValueError(f"Invalid SVG: {name}")
    return match.group(1).strip()


def symbol_defs() -> str:
    return "\n".join(
        f'<symbol id="i-{name}" viewBox="0 0 24 24">{icon_inner(name)}</symbol>'
        for name in ICON_NAMES
    )


def icon(name: str, x: int, y: int, size: int, color: str = "#12304A", stroke: float = 1.9) -> str:
    return (
        f'<use href="#i-{name}" x="{x}" y="{y}" width="{size}" height="{size}" '
        f'color="{color}" fill="none" stroke="currentColor" stroke-width="{stroke}"/>'
    )


def pill(x: int, y: int, w: int, label: str, fill: str, color: str) -> str:
    return (
        f'<rect x="{x}" y="{y}" width="{w}" height="26" rx="13" fill="{fill}"/>'
        f'<text x="{x + w / 2}" y="{y + 18}" text-anchor="middle" class="pill" fill="{color}">{label}</text>'
    )


def build_svg() -> str:
    defs = symbol_defs()
    # A single self-contained journal plate: four protocol stages and two safety paths.
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="920" viewBox="0 0 1600 920">
<defs>
  <filter id="shadow" x="-20%" y="-20%" width="140%" height="150%"><feDropShadow dx="0" dy="5" stdDeviation="7" flood-color="#17324D" flood-opacity=".12"/></filter>
  <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M0 0L10 5L0 10Z" fill="#315A73"/></marker>
  <marker id="arrow-teal" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto"><path d="M0 0L10 5L0 10Z" fill="#008C87"/></marker>
  <marker id="arrow-amber" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto"><path d="M0 0L10 5L0 10Z" fill="#D88700"/></marker>
  <style>
    text {{font-family: Inter, "Segoe UI", Arial, sans-serif; fill:#15324A}}
    .title {{font-size:27px;font-weight:750;letter-spacing:-.25px}}
    .subtitle {{font-size:14px;fill:#567084}}
    .stage {{font-size:16px;font-weight:750;letter-spacing:.4px}}
    .box-title {{font-size:16px;font-weight:720}}
    .body {{font-size:13px;fill:#36566D}}
    .small {{font-size:11.5px;fill:#567084}}
    .formula {{font-family:"Cambria Math", Georgia, serif;font-size:14px;font-weight:650}}
    .pill {{font-size:11px;font-weight:720;letter-spacing:.15px}}
    .num {{font-size:13px;font-weight:800;fill:white}}
    .footer-label {{font-size:13px;font-weight:700;fill:#FFFFFF}}
    .footer-text {{font-size:13px;fill:#DCEAF2}}
  </style>
  {defs}
</defs>
<rect width="1600" height="920" fill="#F7FAFC"/>
<text x="58" y="48" class="title">AidMesh: private, risk-bounded settlement under network partitions</text>
<text x="58" y="73" class="subtitle">Offline authorization · quorum / conserved risk-rights · delay-tolerant transport · zero-knowledge settlement</text>
{pill(1290, 35, 250, 'SELF-CONTAINED PROTOCOL VIEW', '#E6F4F3', '#007B78')}

<!-- stage rail -->
<path d="M90 116H1510" stroke="#D1DEE7" stroke-width="2"/>
<g><circle cx="130" cy="116" r="17" fill="#123B5D"/><text x="130" y="121" text-anchor="middle" class="num">1</text><text x="160" y="121" class="stage">OFFLINE REQUEST</text></g>
<g><circle cx="485" cy="116" r="17" fill="#008C87"/><text x="485" y="121" text-anchor="middle" class="num">2</text><text x="515" y="121" class="stage">PARTITION-SAFE AUTHORIZATION</text></g>
<g><circle cx="1022" cy="116" r="17" fill="#D88700"/><text x="1022" y="121" text-anchor="middle" class="num">3</text><text x="1052" y="121" class="stage">DTN CONVERGENCE</text></g>
<g><circle cx="1310" cy="116" r="17" fill="#5B4BB7"/><text x="1310" y="121" text-anchor="middle" class="num">4</text><text x="1340" y="121" class="stage">PRIVATE SETTLEMENT</text></g>

<!-- left request panel -->
<rect x="50" y="150" width="300" height="690" rx="18" fill="white" stroke="#BCD0DD" stroke-width="1.5" filter="url(#shadow)"/>
<rect x="50" y="150" width="300" height="58" rx="18" fill="#EAF2F7"/><path d="M50 190V168a18 18 0 0 1 18-18h264a18 18 0 0 1 18 18v22" fill="#EAF2F7"/>
{icon('user-heart', 72, 164, 32, '#123B5D')}
<text x="116" y="184" class="box-title">Beneficiary device</text><text x="116" y="202" class="small">offline-first credential wallet</text>
<rect x="82" y="238" width="236" height="186" rx="24" fill="#F8FBFD" stroke="#7896A9" stroke-width="2"/>
<rect x="151" y="251" width="98" height="6" rx="3" fill="#B8CAD5"/>
{icon('device-mobile-check', 166, 275, 68, '#008C87')}
<text x="200" y="365" text-anchor="middle" class="box-title">Signed request</text>
<text x="200" y="388" text-anchor="middle" class="formula">m = (cred, item, epoch, n)</text>
{pill(117, 405, 166, 'NO ONLINE ID LOOKUP', '#E6F4F3', '#007B78')}

<path d="M200 444V475" stroke="#315A73" stroke-width="2" marker-end="url(#arrow)"/>
<rect x="82" y="488" width="236" height="122" rx="14" fill="#FFF9EC" stroke="#E4B554" stroke-width="1.5"/>
{icon('id-badge-2', 101, 512, 42, '#D88700')}
<text x="154" y="521" class="box-title">Local eligibility</text><text x="154" y="544" class="body">signature · epoch · policy</text>
<text x="154" y="566" class="body">revocation snapshot · nonce</text>
<text x="154" y="589" class="small">metadata stays off the ledger</text>

<rect x="82" y="638" width="236" height="154" rx="14" fill="#F1F6FA" stroke="#BCD0DD"/>
{icon('wifi-off', 101, 660, 40, '#C14949')}
<text x="154" y="670" class="box-title">Partition threat</text>
<text x="104" y="706" class="body">• sites cannot share fresh state</text><text x="104" y="730" class="body">• retries and replay are expected</text><text x="104" y="754" class="body">• safety is conditional, not magic</text>
{pill(105, 768, 190, 'AVAILABILITY ≠ ZERO RISK', '#FBECEC', '#A43A3A')}

<!-- central authorization panel -->
<rect x="380" y="150" width="600" height="690" rx="18" fill="white" stroke="#9BC9C5" stroke-width="1.7" filter="url(#shadow)"/>
<rect x="380" y="150" width="600" height="58" rx="18" fill="#E6F4F3"/><path d="M380 190V168a18 18 0 0 1 18-18h564a18 18 0 0 1 18 18v22" fill="#E6F4F3"/>
{icon('shield-check', 402, 164, 34, '#008C87')}
<text x="448" y="184" class="box-title">Safety domain D(e, item, region)</text><text x="448" y="202" class="small">choose one authorization path; preserve persistent witness / right state</text>

<!-- quorum lane -->
<rect x="404" y="232" width="552" height="248" rx="16" fill="#F3FAF9" stroke="#77BBB5" stroke-width="1.4" stroke-dasharray="7 5"/>
{pill(424, 248, 120, 'FAST PATH', '#CCEAE7', '#007B78')}
<text x="562" y="267" class="box-title">Quorum certificate</text>
<text x="426" y="299" class="formula">2q &gt; K + f</text><text x="535" y="299" class="body">⇒ conflicting certificates intersect in an honest witness</text>
<g>
  <rect x="424" y="324" width="105" height="94" rx="12" fill="white" stroke="#90BEBB"/>{icon('server', 451, 338, 32, '#008C87')}<text x="476" y="392" text-anchor="middle" class="small">w₁ · signed</text>
  <rect x="546" y="324" width="105" height="94" rx="12" fill="white" stroke="#90BEBB"/>{icon('database', 573, 338, 32, '#008C87')}<text x="598" y="392" text-anchor="middle" class="small">w₂ · durable</text>
  <rect x="668" y="324" width="105" height="94" rx="12" fill="white" stroke="#90BEBB"/>{icon('shield-check', 695, 338, 32, '#008C87')}<text x="720" y="392" text-anchor="middle" class="small">w₃ · policy</text>
  <rect x="790" y="324" width="105" height="94" rx="12" fill="white" stroke="#90BEBB"/>{icon('key', 817, 338, 32, '#008C87')}<text x="842" y="392" text-anchor="middle" class="small">w₄ · key</text>
</g>
<path d="M476 426H842" stroke="#008C87" stroke-width="2" marker-end="url(#arrow-teal)"/>
{pill(691, 430, 202, 'QC: q = 3 OF K = 4', '#CCEAE7', '#007B78')}

<!-- fallback lane -->
<rect x="404" y="500" width="552" height="310" rx="16" fill="#FFFAEF" stroke="#E4B554" stroke-width="1.4" stroke-dasharray="7 5"/>
{pill(424, 516, 132, 'FALLBACK PATH', '#FBE8B9', '#9C6200')}
<text x="574" y="535" class="box-title">Conserved transferable risk-rights</text>
<text x="425" y="568" class="formula">I = R + T + S + E + V</text><text x="620" y="568" class="body">conserved across mint · transfer · spend · settle</text>
<g>
  <rect x="424" y="594" width="116" height="112" rx="13" fill="white" stroke="#DDB96A"/>{icon('building-store', 460, 606, 40, '#D88700')}<text x="482" y="666" text-anchor="middle" class="box-title">Site A</text><text x="482" y="690" text-anchor="middle" class="formula">Rₐ = 12</text>
  <rect x="554" y="594" width="116" height="112" rx="13" fill="white" stroke="#DDB96A"/>{icon('building-store', 590, 606, 40, '#D88700')}<text x="612" y="666" text-anchor="middle" class="box-title">Site B</text><text x="612" y="690" text-anchor="middle" class="formula">Rᵦ = 7</text>
  <rect x="684" y="594" width="116" height="112" rx="13" fill="white" stroke="#DDB96A"/>{icon('building-store', 720, 606, 40, '#D88700')}<text x="742" y="666" text-anchor="middle" class="box-title">Site C</text><text x="742" y="690" text-anchor="middle" class="formula">R𝚌 = 4</text>
  <rect x="814" y="594" width="116" height="112" rx="13" fill="white" stroke="#DDB96A"/>{icon('building-store', 850, 606, 40, '#D88700')}<text x="872" y="666" text-anchor="middle" class="box-title">Site D</text><text x="872" y="690" text-anchor="middle" class="formula">R𝚍 = 1</text>
</g>
<path d="M540 651H554" stroke="#D88700" stroke-width="2" marker-end="url(#arrow-amber)"/><path d="M670 651H684" stroke="#D88700" stroke-width="2" marker-end="url(#arrow-amber)"/><path d="M800 651H814" stroke="#D88700" stroke-width="2" marker-end="url(#arrow-amber)"/>
<rect x="424" y="727" width="506" height="58" rx="11" fill="#FFF3D6"/>{icon('transfer', 440, 741, 30, '#D88700')}<text x="482" y="751" class="box-title">Debit-before-transfer</text><text x="482" y="773" class="small">persistent monotone state prevents cloning after crash/restart</text>

<!-- main flow connectors -->
<path d="M350 446H380" stroke="#315A73" stroke-width="3" marker-end="url(#arrow)"/>
<text x="365" y="432" text-anchor="middle" class="small">request</text>
<path d="M980 446H1005" stroke="#315A73" stroke-width="3" marker-end="url(#arrow)"/>

<!-- DTN panel -->
<rect x="1005" y="150" width="250" height="690" rx="18" fill="white" stroke="#E2C277" stroke-width="1.5" filter="url(#shadow)"/>
<rect x="1005" y="150" width="250" height="58" rx="18" fill="#FFF4DC"/><path d="M1005 190V168a18 18 0 0 1 18-18h214a18 18 0 0 1 18 18v22" fill="#FFF4DC"/>
{icon('route', 1026, 164, 34, '#D88700')}<text x="1072" y="184" class="box-title">Store–carry–forward</text><text x="1072" y="202" class="small">delay-tolerant convergence</text>
<rect x="1030" y="238" width="200" height="116" rx="14" fill="#F8FBFD" stroke="#B8CAD5"/>
{icon('receipt-2', 1051, 262, 42, '#123B5D')}<text x="1106" y="273" class="box-title">Padded receipt</text><text x="1106" y="297" class="body">fixed-size bundle</text><text x="1106" y="319" class="body">encrypted audit detail</text><text x="1106" y="341" class="small">root · nullifier · epoch</text>
<path d="M1130 365V400" stroke="#D88700" stroke-width="2.5" marker-end="url(#arrow-amber)"/>
<g>
  <circle cx="1070" cy="438" r="34" fill="#FFF7E8" stroke="#E0B04D"/>{icon('access-point', 1052, 420, 36, '#D88700')}
  <circle cx="1190" cy="438" r="34" fill="#FFF7E8" stroke="#E0B04D"/>{icon('antenna-bars-5', 1172, 420, 36, '#D88700')}
  <circle cx="1070" cy="558" r="34" fill="#FFF7E8" stroke="#E0B04D"/>{icon('satellite', 1052, 540, 36, '#D88700')}
  <circle cx="1190" cy="558" r="34" fill="#FFF7E8" stroke="#E0B04D"/>{icon('cloud-off', 1172, 540, 36, '#C14949')}
  <path d="M1105 438H1155" stroke="#D88700" stroke-width="2" stroke-dasharray="7 5" marker-end="url(#arrow-amber)"/><path d="M1190 473V523" stroke="#D88700" stroke-width="2" stroke-dasharray="7 5" marker-end="url(#arrow-amber)"/><path d="M1155 558H1105" stroke="#D88700" stroke-width="2" stroke-dasharray="7 5" marker-end="url(#arrow-amber)"/>
</g>
<text x="1130" y="618" text-anchor="middle" class="box-title">Intermittent contacts</text><text x="1130" y="641" text-anchor="middle" class="body">Epidemic / Spray-and-Wait</text>
<rect x="1030" y="675" width="200" height="105" rx="13" fill="#F1F6FA" stroke="#BCD0DD"/>{icon('lock', 1049, 694, 37, '#5B4BB7')}<text x="1098" y="704" class="box-title">Privacy knobs</text><text x="1098" y="727" class="body">batch window · padding</text><text x="1098" y="749" class="body">optional cover traffic</text><text x="1098" y="771" class="small">bounded bytes + delay</text>

<!-- settlement panel -->
<rect x="1280" y="150" width="270" height="690" rx="18" fill="white" stroke="#B7AFE3" stroke-width="1.5" filter="url(#shadow)"/>
<rect x="1280" y="150" width="270" height="58" rx="18" fill="#F0EEFA"/><path d="M1280 190V168a18 18 0 0 1 18-18h234a18 18 0 0 1 18 18v22" fill="#F0EEFA"/>
{icon('file-certificate', 1301, 164, 34, '#5B4BB7')}<text x="1347" y="184" class="box-title">Groth16 settlement</text><text x="1347" y="202" class="small">under the stated SRS assumption</text>
<rect x="1304" y="236" width="222" height="126" rx="14" fill="#F8F7FD" stroke="#B7AFE3"/>
{icon('binary-tree', 1323, 257, 44, '#5B4BB7')}<text x="1380" y="268" class="box-title">Public statement</text><text x="1380" y="293" class="body">batchRoot · epoch</text><text x="1380" y="316" class="body">nullifier · policy hash</text><text x="1380" y="339" font-size="10.5" fill="#567084">receipt details remain private</text>
<path d="M1415 374V410" stroke="#5B4BB7" stroke-width="2.5" marker-end="url(#arrow)"/>
<polygon points="1415,418 1495,463 1415,508 1335,463" fill="#F0EEFA" stroke="#7A6BC4" stroke-width="1.7"/>{icon('shield-check', 1395, 440, 40, '#5B4BB7')}<text x="1415" y="525" text-anchor="middle" class="box-title">verifyProof(π, x)</text>
<path d="M1415 540V574" stroke="#5B4BB7" stroke-width="2.5" marker-end="url(#arrow)"/>
<rect x="1304" y="585" width="222" height="116" rx="14" fill="#F3FAF9" stroke="#77BBB5"/>{icon('blocks', 1324, 609, 44, '#008C87')}<text x="1380" y="620" class="box-title">Local EVM ledger</text><text x="1380" y="645" class="body">unique nullifier set</text><text x="1380" y="668" class="body">at-most-once credit</text><text x="1380" y="691" class="small">event log → analysis</text>
<path d="M1415 711V739" stroke="#008C87" stroke-width="2.5" marker-end="url(#arrow-teal)"/>
<rect x="1304" y="748" width="222" height="62" rx="13" fill="#E6F4F3"/>{icon('file-check', 1322, 763, 32, '#008C87')}<text x="1365" y="775" class="box-title">Credit exactly once</text><text x="1365" y="797" class="small">replay ⇒ zero additional credit</text>

<!-- bottom invariant rail -->
<rect x="50" y="860" width="1500" height="42" rx="12" fill="#123B5D"/>
{icon('shield-check', 70, 868, 26, '#FFFFFF')}<text x="108" y="886" class="footer-label">CONDITIONAL SAFETY</text>
<text x="267" y="886" class="footer-text">certificate uniqueness</text><circle cx="423" cy="882" r="3" fill="#57C3BB"/>
<text x="445" y="886" class="footer-text">risk-right conservation</text><circle cx="625" cy="882" r="3" fill="#57C3BB"/>
<text x="647" y="886" class="footer-text">persistent monotone state</text><circle cx="840" cy="882" r="3" fill="#57C3BB"/>
<text x="862" y="886" class="footer-text">at-most-once settlement</text><circle cx="1055" cy="882" r="3" fill="#57C3BB"/>
<text x="1077" y="886" class="footer-text">privacy–network trade-off measured, not assumed</text>
</svg>'''


def main() -> None:
    if not SOURCE.exists():
        raise SystemExit("Extract @tabler/icons@3.45.0 into the package directory first.")
    ICONS.mkdir(parents=True, exist_ok=True)
    for name in ICON_NAMES:
        shutil.copy2(SOURCE / f"{name}.svg", ICONS / f"{name}.svg")

    shutil.copy2(PKG / "LICENSE", OUT / "TABLER_LICENSE.txt")
    svg_path = OUT / "aidmesh_architecture_tabler.svg"
    svg_path.write_text(build_svg(), encoding="utf-8")

    files = [ICONS / f"{name}.svg" for name in ICON_NAMES]
    manifest = {
        "artifact": "AidMesh architecture plate",
        "icon_source": "@tabler/icons",
        "version": "3.45.0",
        "license": "MIT",
        "official_repository": "https://github.com/tabler/tabler-icons",
        "official_catalog": "https://tabler.io/icons",
        "npm_tarball": "https://registry.npmjs.org/@tabler/icons/-/icons-3.45.0.tgz",
        "npm_shasum": "0eae44189827b8426796a74afa8219c2e597b847",
        "icons": [{"file": f"icons/{p.name}", "sha256": sha256(p)} for p in files],
        "composite": {"file": svg_path.name, "sha256": sha256(svg_path)},
    }
    (OUT / "SOURCE_MANIFEST.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
