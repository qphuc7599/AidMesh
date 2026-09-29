param(
    [int]$Workers = 2,
    [string]$OutputRoot = "results/elsevier_remediation_20260901/formal/release_slot_v2"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$jar = Join-Path $root "tools/tla2tools.jar"
if (-not (Test-Path -LiteralPath $jar)) {
    throw "Missing tla2tools.jar: $jar"
}
$output = Join-Path $root $OutputRoot
New-Item -ItemType Directory -Force -Path $output | Out-Null

$cases = @(
    @{ Name = "safe"; Config = "AidMeshReleaseIntentSafe.cfg"; Safe = $true; Expected = "" },
    @{ Name = "unsafe-handoff-first"; Config = "AidMeshReleaseIntentUnsafeHandoffFirst.cfg"; Safe = $false; Expected = "AtMostOneAutomaticActuator" }
)
$caseResults = @()

foreach ($case in $cases) {
    $caseRoot = Join-Path $output $case.Name
    $stateRoot = Join-Path $caseRoot "states"
    New-Item -ItemType Directory -Force -Path $stateRoot | Out-Null
    $logPath = Join-Path $caseRoot "tlc.log"
    Push-Location (Join-Path $root "formal")
    try {
        & java -cp $jar tlc2.TLC -deadlock -workers $Workers -metadir $stateRoot `
            -config $case.Config AidMeshReleaseIntent.tla *>&1 |
            Tee-Object -FilePath $logPath
        $exitCode = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
    $log = Get-Content -LiteralPath $logPath -Raw
    if ($case.Safe) {
        $passed = $exitCode -eq 0 -and $log -match "Model checking completed"
    }
    else {
        $passed = $exitCode -ne 0 -and $log -match $case.Expected
    }
    if (-not $passed) {
        throw "TLC case '$($case.Name)' failed its registered criterion."
    }
    $stateMatch = [regex]::Match(
        $log,
        "(?m)^(\d+) states generated, (\d+) distinct states found, (\d+) states left on queue\."
    )
    $depthMatch = [regex]::Match(
        $log,
        "(?m)^The depth of the complete state graph search is (\d+)\."
    )
    if (-not $stateMatch.Success -or -not $depthMatch.Success) {
        throw "TLC case '$($case.Name)' did not emit the expected state-count summary."
    }
    $caseResults += [ordered]@{
        name = $case.Name
        config = $case.Config
        registered_safe = [bool]$case.Safe
        criterion_passed = [bool]$passed
        expected_invariant_violation = $case.Expected
        states_generated = [int]$stateMatch.Groups[1].Value
        distinct_states = [int]$stateMatch.Groups[2].Value
        states_left = [int]$stateMatch.Groups[3].Value
        depth = [int]$depthMatch.Groups[1].Value
        log = (Resolve-Path -LiteralPath $logPath).Path.Substring($output.Length + 1).Replace("\", "/")
        log_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $logPath).Hash.ToLowerInvariant()
    }
}

$modelPath = Join-Path $root "formal/AidMeshReleaseIntent.tla"
$manifest = [ordered]@{
    schema_version = 2
    tool = "TLC"
    model = "AidMeshReleaseIntent.tla"
    model_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $modelPath).Hash.ToLowerInvariant()
    jar_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $jar).Hash.ToLowerInvariant()
    workers = $Workers
    all_registered_criteria_passed = [bool](($caseResults | Where-Object { -not $_.criterion_passed }).Count -eq 0)
    cases = $caseResults
    claim_scope = "Finite-state release-slot model with quorum/fallback path switching, replay, crash, persist-before-actuator, fail-closed ambiguity, and a registered handoff-first negative control."
}
$manifestPath = Join-Path $output "manifest.json"
$manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $manifestPath -Encoding utf8
$checksumInputs = @(
    $modelPath,
    (Join-Path $root "formal/AidMeshReleaseIntentSafe.cfg"),
    (Join-Path $root "formal/AidMeshReleaseIntentUnsafeHandoffFirst.cfg"),
    (Join-Path $output "safe/tlc.log"),
    (Join-Path $output "unsafe-handoff-first/tlc.log"),
    $manifestPath
)
$checksumLines = foreach ($path in $checksumInputs) {
    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant()
    $resolved = (Resolve-Path -LiteralPath $path).Path
    $relative = $resolved.Substring($root.Length + 1).Replace("\", "/")
    "$hash  $relative"
}
$checksumLines | Set-Content -LiteralPath (Join-Path $output "SHA256SUMS.txt") -Encoding ascii
