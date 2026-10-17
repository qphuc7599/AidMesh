param(
    [string]$Output = "results\tdsc_full_20260822\final_manifest"
)

$ErrorActionPreference = "Stop"
$workspace = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$workspacePrefix = $workspace.TrimEnd("\") + "\"
$outputRoot = [System.IO.Path]::GetFullPath((Join-Path $workspace $Output))

if (-not $outputRoot.StartsWith($workspacePrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Manifest output must stay inside the workspace: $outputRoot"
}

New-Item -ItemType Directory -Force -Path $outputRoot | Out-Null
$ledgerPath = Join-Path $outputRoot "EVIDENCE_SHA256SUMS.txt"
$manifestPath = Join-Path $outputRoot "manifest.json"

$canonicalInputs = @(
    "results\tdsc_full_20260822\primary_temporal_gate_v4",
    "results\tdsc_full_20260822\factorial_temporal_gate_v3",
    "results\tdsc_full_20260822\final_summary",
    "results\tdsc_full_20260822\semantic_e2e_final",
    "results\tdsc_full_20260822\semantic_adversarial",
    "results\tdsc_full_20260822\distributed_rights_soak",
    "results\tdsc_full_20260822\tamarin",
    "results\tdsc_full_20260822\pytest_final.xml",
    "source\paper.tex",
    "source\paper.pdf",
    "source\supplementary.tex",
    "source\supplementary.pdf",
    "configs\e2e\semantic_6h_final.yaml",
    "contracts\src\AidMeshSemanticAuthorizationGateway.sol",
    "contracts\src\AidMeshSettlement.sol",
    "formal\AidMeshSemanticAuthorization.tla"
)

$files = foreach ($relativeInput in $canonicalInputs) {
    $absoluteInput = [System.IO.Path]::GetFullPath((Join-Path $workspace $relativeInput))
    if (-not $absoluteInput.StartsWith($workspacePrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Canonical input escapes workspace: $absoluteInput"
    }
    if (-not (Test-Path -LiteralPath $absoluteInput)) {
        throw "Missing canonical input: $absoluteInput"
    }
    if (Test-Path -LiteralPath $absoluteInput -PathType Container) {
        Get-ChildItem -LiteralPath $absoluteInput -Recurse -File
    } else {
        Get-Item -LiteralPath $absoluteInput
    }
}

$files = $files |
    Where-Object { -not $_.FullName.StartsWith($outputRoot + "\", [System.StringComparison]::OrdinalIgnoreCase) } |
    Sort-Object FullName -Unique

$entries = foreach ($file in $files) {
    $relative = $file.FullName.Substring($workspace.Length + 1).Replace("\", "/")
    $hash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    [pscustomobject]@{
        path = $relative
        bytes = [int64]$file.Length
        sha256 = $hash
    }
}

$entries |
    ForEach-Object { "$($_.sha256)  $($_.path)" } |
    Set-Content -LiteralPath $ledgerPath -Encoding ascii

$ledgerHash = (Get-FileHash -LiteralPath $ledgerPath -Algorithm SHA256).Hash.ToLowerInvariant()
$manifest = [ordered]@{
    schema_version = 1
    campaign = "AidMesh IEEE TDSC six-hour bounded final evidence"
    generated_utc = (Get-Date).ToUniversalTime().ToString("o")
    canonical_roots = $canonicalInputs
    file_count = @($entries).Count
    total_bytes = [int64](($entries | Measure-Object bytes -Sum).Sum)
    ledger = "results/tdsc_full_20260822/final_manifest/EVIDENCE_SHA256SUMS.txt"
    ledger_sha256 = $ledgerHash
    exclusions = @(
        "Superseded V1/V2 diagnostics and historical campaigns",
        "Physical Android/Raspberry-Pi or multi-machine measurements",
        "Semantic Sepolia deployment, which was not executed"
    )
}
$manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $manifestPath -Encoding utf8

Write-Output $manifestPath
Write-Output $ledgerPath
Write-Output "files=$(@($entries).Count)"
Write-Output "bytes=$($manifest.total_bytes)"
Write-Output "ledger_sha256=$ledgerHash"
