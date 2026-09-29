param(
    [string]$JarPath = $env:TLA2TOOLS_JAR,
    [string]$Output = "results\tdsc_upgrade\formal\recovery_liveness",
    [int]$Workers = 4
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if (-not $JarPath) { $JarPath = Join-Path $root "tools\tla2tools.jar" }
if (-not (Test-Path -LiteralPath $JarPath -PathType Leaf)) {
    throw "Missing tla2tools.jar: $JarPath"
}
$jar = (Resolve-Path -LiteralPath $JarPath).Path
$outputRoot = [System.IO.Path]::GetFullPath((Join-Path $root $Output))
$workspaceRoot = [System.IO.Path]::GetFullPath($root).TrimEnd("\") + "\"
if (-not $outputRoot.StartsWith($workspaceRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Output must stay inside the workspace: $outputRoot"
}
New-Item -ItemType Directory -Force -Path $outputRoot | Out-Null

$cases = @(
    @{ Name = "safe-recovery"; Config = "AidMeshRecoveryLivenessSafe.cfg"; Safe = $true },
    @{ Name = "safe-conflict"; Config = "AidMeshRecoveryLivenessSafeConflict.cfg"; Safe = $true },
    @{ Name = "unsafe-permanent-partition"; Config = "AidMeshRecoveryLivenessUnsafePartition.cfg"; Safe = $false }
)
$summary = @()
Push-Location (Join-Path $root "formal")
try {
    foreach ($case in $cases) {
        $caseRoot = Join-Path $outputRoot $case.Name
        $stateRoot = Join-Path $caseRoot "states"
        New-Item -ItemType Directory -Force -Path $stateRoot | Out-Null
        $logPath = Join-Path $caseRoot "tlc.log"
        $started = Get-Date
        & java -XX:+UseParallelGC -cp $jar tlc2.TLC -deadlock -workers $Workers `
            -metadir $stateRoot -config $case.Config AidMeshRecoveryLiveness.tla *>&1 |
            Tee-Object -FilePath $logPath
        $exitCode = $LASTEXITCODE
        $content = Get-Content -LiteralPath $logPath -Raw
        if ($case.Safe) {
            $passed = ($exitCode -eq 0) -and
                ($content -match "Model checking completed. No error has been found")
        }
        else {
            $passed = ($exitCode -ne 0) -and
                ($content -match "Temporal properties were violated")
        }
        $stateMatches = [regex]::Matches(
            $content,
            "([0-9,]+) states generated, ([0-9,]+) distinct states found"
        )
        $generatedStates = $null
        $distinctStates = $null
        if ($stateMatches.Count -gt 0) {
            $last = $stateMatches[$stateMatches.Count - 1]
            $generatedStates = [int64]($last.Groups[1].Value.Replace(",", ""))
            $distinctStates = [int64]($last.Groups[2].Value.Replace(",", ""))
        }
        $summary += [ordered]@{
            case = $case.Name
            config = $case.Config
            negative_control = -not $case.Safe
            exit_code = $exitCode
            passed = $passed
            generated_states = $generatedStates
            distinct_states = $distinctStates
            elapsed_seconds = [math]::Round(((Get-Date) - $started).TotalSeconds, 3)
        }
        if (-not $passed) { throw "TLC case '$($case.Name)' failed its registered criterion." }
    }
}
finally { Pop-Location }

$summaryPath = Join-Path $outputRoot "tlc_recovery_liveness_summary.json"
$summary | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $summaryPath -Encoding utf8
Write-Output $summaryPath
