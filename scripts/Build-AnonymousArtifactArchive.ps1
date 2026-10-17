param(
    [string]$Output = "release\anonymous_artifact_20260726",
    [switch]$SkipLargeEvidence
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$sevenZip = "C:\Program Files\7-Zip\7z.exe"
if (-not (Test-Path -LiteralPath $sevenZip)) {
    throw "Khong tim thay 7-Zip tai $sevenZip."
}

$release = Join-Path $root $Output
New-Item -ItemType Directory -Force -Path $release | Out-Null

function Get-RelativeFiles {
    param([string[]]$Paths)
    $files = foreach ($path in $Paths) {
        $absolute = Join-Path $root $path
        if (Test-Path -LiteralPath $absolute -PathType Leaf) {
            Get-Item -LiteralPath $absolute
        }
        elseif (Test-Path -LiteralPath $absolute -PathType Container) {
            Get-ChildItem -LiteralPath $absolute -Recurse -File -Force
        }
    }
    $files |
        Where-Object {
            $_.FullName -notmatch '\\(__pycache__|\.pytest_cache|\.ruff_cache|node_modules|\.venv|states)\\' -and
            # Console logs can embed absolute workstation paths/user names.  The
            # archive retains structured JSON/CSV summaries and immutable event
            # evidence, but omits these non-canonical, identifying transcripts.
            $_.Name -notmatch '\.(pyc|pyo|sqlite-wal|sqlite-shm|building|log)$'
        } |
        ForEach-Object {
            $_.FullName.Substring($root.Length).TrimStart("\").Replace("\", "/")
        } |
        Sort-Object -Unique
}

function New-ArchiveFromList {
    param(
        [string]$Name,
        [string[]]$Files
    )
    $listPath = Join-Path $release "$Name.files.txt"
    $archivePath = Join-Path $release "$Name.zip"
    $Files | Set-Content -LiteralPath $listPath -Encoding utf8
    if (Test-Path -LiteralPath $archivePath) {
        Remove-Item -LiteralPath $archivePath -Force
    }
    Push-Location $root
    try {
        & $sevenZip a -tzip -mx=5 -mmt=on -mtc=off -mtm=off -mta=off `
            $archivePath "@$listPath" | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "7-Zip failed for $Name with exit code $LASTEXITCODE."
        }
    }
    finally {
        Pop-Location
    }
    return $archivePath
}

$sourceFiles = Get-RelativeFiles @(
    "aidmesh",
    "contracts",
    "configs",
    "formal",
    "manifests",
    "processed",
    "reproducibility",
    "schemas",
    "scripts",
    "tests",
    "zk\circuits",
    "zk\build",
    "zk\inputs",
    "zk\domain_tags.json",
    "zk\ceremony\validate_transcript.py",
    # Small immutable fixtures required by the 106-test cross-host regression.
    "source\supplementary.tex",
    "results\confirmatory\frontier\frontier_hypervolume_contrasts.csv",
    "results\upgrade_20260718\heldout_temporal_frontier\heldout_temporal_contrasts.csv",
    "results\confirmatory\independent_trace_family_20260718\family_contrasts.csv",
    "results\supporting_2026Q3\second_host_linux\second_host_reproduction_summary.json",
    "docs\PREREG_EXTENSION_P1_V2_20260724.md",
    "docs\PREREG_AMENDMENT_PATH_ONLY_20260724.md",
    "docs\PREREG_AMENDMENT_ECDSA_HIGHS_20260724.md",
    "docs\P1_PROFILE_CANONICALIZATION_VI.md",
    "docs\FORMAL_COMPOSITION_AND_REFINEMENT_VI.md",
    "docs\PRACTICAL_EFFECT_POLICY_RATIONALE_20260724_VI.md",
    "docs\HUONG_DAN_SECOND_HOST_VA_REVIEW_DOC_LAP_20260723.md",
    "docs\audit",
    "docs\legal",
    "docs\ethics",
    "docs\submission\DATA_AVAILABILITY.md",
    "docs\submission\ANONYMOUS_ARCHIVE_RELEASE_VI.md",
    "CITATION.cff",
    "LICENSE",
    "package.json",
    "package-lock.json",
    "pyproject.toml",
    "requirements.lock",
    "requirements-linux.lock",
    "README.md"
)

$manuscriptFiles = @(
    "source/paper.tex",
    "source/supplementary.tex",
    "source/figures/figures_architecture.pdf",
    "source/figures/formal_refinement_upgrade.pdf",
    "source/figures/rq1_completion_effects.pdf",
    "source/figures/rq2_frontier.pdf",
    "source/figures/heldout_temporal_frontier.pdf",
    "source/figures/independent_trace_family_campaign.pdf",
    "source/figures/privacy_pareto.pdf"
)

# Double-blind preflight: fail closed if any packaged PDF carries a real author
# in either the PDF Info dictionary or XMP metadata.
foreach ($relativePdf in ($manuscriptFiles | Where-Object { $_ -match '\.pdf$' })) {
    & python (Join-Path $root "scripts\sanitize-pdf-metadata.py") `
        (Join-Path $root $relativePdf) --check-no-author
    if ($LASTEXITCODE -ne 0) {
        throw "Identifying PDF metadata detected in $relativePdf."
    }
}

$archives = [System.Collections.Generic.List[string]]::new()
$archives.Add((New-ArchiveFromList "aidmesh-anonymous-source" $sourceFiles))
$archives.Add((New-ArchiveFromList "aidmesh-anonymous-manuscript" $manuscriptFiles))

if (-not $SkipLargeEvidence) {
    $evidenceFiles = Get-RelativeFiles @(
        "raw\confirmatory",
        "results\audit",
        "results\confirmatory",
        "results\extension_20260724",
        "results\external_dtn",
        "results\fixback",
        "results\supporting_2026Q3",
        "results\upgrade_20260718"
    )
    $archives.Add((New-ArchiveFromList "aidmesh-anonymous-evidence" $evidenceFiles))
}

$archiveChecksums = Join-Path $release "ARCHIVE_CHECKSUMS.sha256"
$archives |
    Sort-Object |
    ForEach-Object {
        "$((Get-FileHash -LiteralPath ([string]$_) -Algorithm SHA256).Hash.ToLower())  $(Split-Path -Leaf ([string]$_))"
    } | Set-Content -LiteralPath $archiveChecksums -Encoding ascii

$fileLists = Get-ChildItem -LiteralPath $release -Filter "*.files.txt" -File
$contentManifest = Join-Path $release "CONTENT_LIST_CHECKSUMS.sha256"
$fileLists |
    Sort-Object Name |
    ForEach-Object {
        "$((Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLower())  $($_.Name)"
    } | Set-Content -LiteralPath $contentManifest -Encoding ascii

Write-Output $archiveChecksums
Write-Output $contentManifest
