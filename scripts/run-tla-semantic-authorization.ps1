param(
    [string]$Output = "results\bow_20260822\formal\tlc_semantic_authorization",
    [int]$Workers = 4,
    [string]$JarPath = "tools\tla2tools.jar"
)

$ErrorActionPreference = "Stop"
$workspaceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$jar = (Resolve-Path (Join-Path $workspaceRoot $JarPath)).Path
$formalRoot = Join-Path $workspaceRoot "formal"
$outputRoot = Join-Path $workspaceRoot $Output
New-Item -ItemType Directory -Force -Path $outputRoot | Out-Null

$cases = @(
    @{ Name = "safe"; Config = "AidMeshSemanticAuthorizationSafe.cfg"; Safe = $true; Expected = "" },
    @{ Name = "unsafe-threshold"; Config = "AidMeshSemanticAuthorizationUnsafeThreshold.cfg"; Safe = $false; Expected = "SemanticAuthorizationSound" },
    @{ Name = "unsafe-right-reuse"; Config = "AidMeshSemanticAuthorizationUnsafeReuse.cfg"; Safe = $false; Expected = "HonestSiteRiskBound" },
    @{ Name = "unsafe-rebind"; Config = "AidMeshSemanticAuthorizationUnsafeRebind.cfg"; Safe = $false; Expected = "ExactReleaseToSettlementBinding" }
)

$summary = @()
Push-Location $formalRoot
try {
    foreach ($case in $cases) {
        $caseRoot = Join-Path $outputRoot $case.Name
        $stateRoot = Join-Path $caseRoot "states"
        New-Item -ItemType Directory -Force -Path $stateRoot | Out-Null
        $logPath = Join-Path $caseRoot "tlc.log"
        & java -XX:+UseParallelGC -cp $jar tlc2.TLC -deadlock -workers $Workers `
            -metadir $stateRoot -config $case.Config AidMeshSemanticAuthorization.tla *>&1 |
            Tee-Object -FilePath $logPath
        $exitCode = $LASTEXITCODE
        $log = Get-Content -Raw $logPath
        $invariant = ""
        if ($log -match "Invariant ([A-Za-z0-9_]+) is violated") {
            $invariant = $Matches[1]
        }
        $passed = if ($case.Safe) {
            $exitCode -eq 0 -and $log -match "Model checking completed"
        } else {
            $exitCode -ne 0 -and $invariant -eq $case.Expected
        }
        $summary += [ordered]@{
            case = $case.Name
            expected_safe = $case.Safe
            exit_code = $exitCode
            violated_invariant = $invariant
            expected_invariant = $case.Expected
            passed = $passed
            log_sha256 = (Get-FileHash -Algorithm SHA256 $logPath).Hash.ToLowerInvariant()
        }
    }
} finally {
    Pop-Location
}

$summaryPath = Join-Path $outputRoot "tlc_semantic_authorization_summary.json"
$summary | ConvertTo-Json -Depth 5 | Set-Content -Encoding utf8 $summaryPath
if ($summary.passed -contains $false) {
    throw "semantic authorization TLC campaign did not match its safe/unsafe oracle"
}
Write-Output $summaryPath
