"""Build deterministic manuscript and evidence archives for external upload."""

from __future__ import annotations

import hashlib
import json
import platform
import shutil
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output/reproducibility"
ARTIFACT = OUT / "AidMesh_Q1_PostReview_Artifact_20260910.zip"
OVERLEAF = OUT / "AidMesh_Elsevier_Overleaf_20260910.zip"
PDF = ROOT / "output/pdf/AidMesh_Elsevier_PostReview_20260910.pdf"
FIXED_TIME = (2026, 9, 10, 0, 0, 0)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_output(command: list[str]) -> str:
    try:
        return subprocess.run(
            command,
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unavailable"


def files_under(relative: str):
    path = ROOT / relative
    if path.is_file():
        yield path
        return
    for item in sorted(path.rglob("*")):
        if not item.is_file():
            continue
        parts = set(item.relative_to(ROOT).parts)
        if parts & {"__pycache__", ".git", ".pytest_cache", ".ruff_cache", "node_modules"}:
            continue
        if item.suffix in {".pyc", ".pyo", ".sqlite-wal", ".sqlite-shm"}:
            continue
        yield item


def write_zip(path: Path, entries: list[tuple[Path, str]], extra: dict[str, bytes] | None = None):
    if path.exists():
        path.unlink()
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for source, name in sorted(entries, key=lambda item: item[1]):
            info = zipfile.ZipInfo(name, FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, source.read_bytes())
        for name, content in sorted((extra or {}).items()):
            info = zipfile.ZipInfo(name, FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content)


OUT.mkdir(parents=True, exist_ok=True)
PDF.parent.mkdir(parents=True, exist_ok=True)
shutil.copy2(ROOT / "source/paper.pdf", PDF)

geth = ROOT / "external/geth_v1.17.5/bin/geth-alltools-windows-amd64-1.17.5-9621c6ad/geth.exe"
tamarin_archive = ROOT / "external/tamarin/tamarin-prover-1.12.0-linux64-ubuntu.tar.gz"
environment = {
    "platform": platform.platform(),
    "python": platform.python_version(),
    "node": run_output(["node", "--version"]),
    "npm": run_output(["npm", "--version"]),
    "cpu": "12th Gen Intel(R) Core(TM) i5-12450HX; 8 cores; 12 logical processors",
    "ramGiB": 15.71,
    "geth": "1.17.5-stable; commit 9621c6ad10934a01b5514886fb6fbd87640b6c05",
    "gethExeSha256": sha256(geth),
    "tamarin": "1.12.0",
    "tamarinArchiveSha256": sha256(tamarin_archive),
    "maude": "3.5.1",
    "tlc": "2.19",
}
runtime_path = OUT / "runtime_environment.json"
runtime_path.write_text(json.dumps(environment, indent=2), encoding="utf-8")

run_root = ROOT / "results/geth_eip7825_atomic100_attackmatrix_final4_20260910"
run_manifest_path = run_root / "manifest.json"
run_rows = []
for item in sorted(run_root.rglob("*")):
    if (
        item.is_file()
        and item != run_manifest_path
        and item.suffix not in {".sqlite-wal", ".sqlite-shm"}
    ):
        run_rows.append(
            {
                "path": item.relative_to(run_root).as_posix(),
                "bytes": item.stat().st_size,
                "sha256": sha256(item),
            }
        )
run_manifest_path.write_text(
    json.dumps(
        {
            "profile": "final staged-atomic 100-voucher evidence tree",
            "files": len(run_rows),
            "entries": run_rows,
        },
        indent=2,
    ),
    encoding="utf-8",
)

roots = [
    "aidmesh",
    "contracts",
    "configs",
    "formal",
    "manifests",
    "schemas",
    "scripts",
    "tests",
    "tools/solc",
    "zk/circuits",
    "zk/build_v3",
    "zk/domain_tags.json",
    "external/bplib_compat",
    "external/coconut_original",
    "source/paper.tex",
    "source/references.bib",
    "source/paper.pdf",
    "source/figs/postreview_core_results.pdf",
    "source/CANONICAL_MANUSCRIPT_README.md",
    "overleaf/paper.tex",
    "overleaf/refs.bib",
    "overleaf/figs/postreview_core_results.pdf",
    "docs/NEW_REVIEW_REVISION_PLAN_20260909_VI.md",
    "docs/POSTREVIEW_FINAL_EVIDENCE_20260910_VI.md",
    "docs/NEW_REVIEW_COMPLETION_AUDIT_20260910_VI.md",
    "docs/NEW_REVIEW_RESPONSE_LETTER_20260910.md",
    "docs/POSTREVIEW_CLAIM_EVIDENCE_MATRIX_20260910.csv",
    "docs/ORIGINAL_REGISTRY_HOLDOUT_REPORT_20260910_VI.md",
    "docs/research_questions.md",
    "docs/threat_model.md",
    "docs/audit",
    "reproducibility/POSTREVIEW_ARTIFACT_README.md",
    "reproducibility/archive_metadata.json",
    "reproducibility/UPLOAD_CHECKLIST.md",
    "results/value_admission_atomic100_attackmatrix_final4_20260910",
    "results/geth_eip7825_atomic100_attackmatrix_final4_20260910",
    "results/strong_allocation_20260910_v6",
    "results/strong_allocation_equal_share_20260910",
    "results/coupled_delivered_fairness_20260909",
    "results/matched_protocol_baselines_20260910",
    "results/matched_receipt_controller_20260910",
    "results/no_ledger_reconciliation_ablation_20260910_v1",
    "results/staged_privacy_20260909_v2",
    "results/postreview_formal_20260910",
    "results/postreview_original_registry_20260910",
    "results/protocol_refinement_postreview_20260910",
    "results/recovery_view_refinement_20260910",
    "results/coconut_online_final_20260906",
    "results/q1_revision_20260906/formal_controls",
    "README.md",
    "LICENSE",
    "CITATION.cff",
    "package.json",
    "package-lock.json",
    "pyproject.toml",
    "requirements.lock",
    "requirements-linux.lock",
]

unique = {}
for root in roots:
    for item in files_under(root):
        unique[item.relative_to(ROOT).as_posix()] = item

prefix = "AidMesh_Q1_PostReview_20260910"
entries = [(item, f"{prefix}/{name}") for name, item in unique.items()]
manifest = "".join(f"{sha256(item)}  {name}\n" for name, item in sorted(unique.items()))
write_zip(
    ARTIFACT,
    entries,
    {
        f"{prefix}/MANIFEST.sha256": manifest.encode("ascii"),
        f"{prefix}/runtime_environment.json": runtime_path.read_bytes(),
        f"{prefix}/ARTIFACT_README.md": (
            ROOT / "reproducibility/POSTREVIEW_ARTIFACT_README.md"
        ).read_bytes(),
    },
)

overleaf_entries = [
    (ROOT / "overleaf/paper.tex", "paper.tex"),
    (ROOT / "overleaf/refs.bib", "refs.bib"),
    (
        ROOT / "overleaf/figs/postreview_core_results.pdf",
        "figs/postreview_core_results.pdf",
    ),
]
write_zip(OVERLEAF, overleaf_entries)

checksums = OUT / "SHA256SUMS.txt"
checksums.write_text(
    "".join(f"{sha256(path)}  {path.name}\n" for path in (PDF, ARTIFACT, OVERLEAF, runtime_path)),
    encoding="ascii",
)
print(
    json.dumps(
        {
            "paper": str(PDF.relative_to(ROOT)),
            "artifact": str(ARTIFACT.relative_to(ROOT)),
            "artifactBytes": ARTIFACT.stat().st_size,
            "artifactFiles": len(unique),
            "overleaf": str(OVERLEAF.relative_to(ROOT)),
            "overleafBytes": OVERLEAF.stat().st_size,
            "checksums": str(checksums.relative_to(ROOT)),
        },
        indent=2,
    )
)
