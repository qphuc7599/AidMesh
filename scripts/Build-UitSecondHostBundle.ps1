param(
    [string]$Output = "release\uit_second_host_upload_20260726"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$bundle = Join-Path $root $Output
$anonymous = Join-Path $root "release\anonymous_artifact_20260726"
New-Item -ItemType Directory -Force -Path $bundle | Out-Null

& powershell -NoProfile -ExecutionPolicy Bypass `
    -File (Join-Path $root "scripts\Build-AnonymousArtifactArchive.ps1") `
    -Output "release\anonymous_artifact_20260726" `
    -SkipLargeEvidence
if ($LASTEXITCODE -ne 0) {
    throw "Khong tao duoc anonymous source archive."
}

Copy-Item -LiteralPath `
    (Join-Path $anonymous "aidmesh-anonymous-source.zip") `
    -Destination (Join-Path $bundle "aidmesh-source.zip") -Force

& python (Join-Path $root "scripts\build-posix-input-archives.py") `
    --root $root --output $bundle
if ($LASTEXITCODE -ne 0) {
    throw "Khong tao duoc POSIX input archives."
}

$names = @(
    "aidmesh-source.zip",
    "sociopatterns-normalized.zip",
    "privacy-core-events.zip"
)
$checksumPath = Join-Path $bundle "UPLOAD_CHECKSUMS.sha256"
$names | ForEach-Object {
    $path = Join-Path $bundle $_
    "$((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLower())  $_"
} | Set-Content -LiteralPath $checksumPath -Encoding ascii

Write-Output $checksumPath
