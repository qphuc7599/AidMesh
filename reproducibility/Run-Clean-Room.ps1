[CmdletBinding()]
param(
    [string]$OutputDirectory = "results\reproduction\single_host_clean_room_reproduction",
    [string]$WslDistribution = "Ubuntu",
    [switch]$ReuseImage
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = (Resolve-Path (Join-Path $scriptRoot "..")).Path
if (-not [IO.Path]::IsPathRooted($OutputDirectory)) {
    $OutputDirectory = Join-Path $repoRoot $OutputDirectory
}
if (Test-Path -LiteralPath $OutputDirectory) {
    throw "Refusing to reuse reproduction output: $OutputDirectory"
}
New-Item -ItemType Directory -Path $OutputDirectory | Out-Null
$outputRoot = (Resolve-Path $OutputDirectory).Path

function Convert-ToWslPath([string]$Path) {
    if ($Path -notmatch "^([A-Za-z]):\\(.*)$") { throw "Expected drive-letter path: $Path" }
    "/mnt/$($Matches[1].ToLowerInvariant())/$($Matches[2].Replace('\','/'))"
}

$image = "airproof-clean-room:20260713"
$nativeDocker = Get-Command docker -ErrorAction SilentlyContinue
if ($nativeDocker) {
    if (-not $ReuseImage) {
        & $nativeDocker.Source build --no-cache --pull --tag $image `
            --file (Join-Path $scriptRoot "Dockerfile") $repoRoot
        if ($LASTEXITCODE -ne 0) { throw "Clean-room image build failed" }
    }
    & $nativeDocker.Source run --rm --network=none --read-only `
        --entrypoint python `
        --cap-drop=ALL --security-opt=no-new-privileges `
        --tmpfs "/tmp:rw,noexec,nosuid,size=256m" `
        --mount "type=bind,source=$repoRoot,target=/repo,readonly" `
        --mount "type=bind,source=$outputRoot,target=/out" `
        $image /repo/reproducibility/clean_room_runner.py
} else {
    $wslRepo = Convert-ToWslPath $repoRoot
    $wslDockerfile = Convert-ToWslPath (Join-Path $scriptRoot "Dockerfile")
    $wslOutput = Convert-ToWslPath $outputRoot
    if (-not $ReuseImage) {
        & wsl.exe -d $WslDistribution -- docker build --no-cache --pull --tag $image `
            --file $wslDockerfile $wslRepo
        if ($LASTEXITCODE -ne 0) { throw "Clean-room image build failed" }
    }
    & wsl.exe -d $WslDistribution -- docker run --rm --network=none --read-only `
        --entrypoint python `
        --cap-drop=ALL --security-opt=no-new-privileges `
        --tmpfs "/tmp:rw,noexec,nosuid,size=256m" `
        --mount "type=bind,source=$wslRepo,target=/repo,readonly" `
        --mount "type=bind,source=$wslOutput,target=/out" `
        $image /repo/reproducibility/clean_room_runner.py
}
if ($LASTEXITCODE -ne 0) { throw "Clean-room reproduction failed; inspect report.json" }
Write-Host "Clean-room reproduction: PASS"
Write-Host "Report: $(Join-Path $outputRoot 'REPORT.md')"
