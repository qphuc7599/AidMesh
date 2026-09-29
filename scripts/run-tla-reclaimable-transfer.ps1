param(
    [string]$JarPath = $env:TLA2TOOLS_JAR,
    [string]$Output = "results\catr_confirmatory_20260822\formal\tlc",
    [int]$Workers = 8
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if (-not $JarPath) {
    $JarPath = Join-Path $root "tools\tla2tools.jar"
}
$JarPath = (Resolve-Path -LiteralPath $JarPath).Path
$outputRoot = Join-Path $root $Output
New-Item -ItemType Directory -Force -Path $outputRoot | Out-Null

$cases = @(
    @{ Name = "safe"; Config = "AidMeshReclaimableTransferSafe.cfg"; Safe = $true; Expected = "" },
    @{ Name = "unsafe-overwrite"; Config = "AidMeshReclaimableTransferUnsafeOverwrite.cfg"; Safe = $false; Expected = "TerminalExclusive" }
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
        & java -cp $JarPath tlc2.TLC -deadlock -workers $Workers -metadir $stateRoot -config $case.Config AidMeshReclaimableTransfer.tla *>&1 |
            Tee-Object -FilePath $logPath
        $exitCode = $LASTEXITCODE
        $log = Get-Content -LiteralPath $logPath -Raw
        if ($case.Safe) {
            $passed = ($exitCode -eq 0) -and ($log -match "Model checking completed. No error has been found")
        }
        else {
            $passed = ($exitCode -ne 0) -and ($log -match $case.Expected) -and ($log -match "Error: Invariant")
        }
        $summary += [ordered]@{
            case = $case.Name
            config = $case.Config
            negative_control = -not $case.Safe
            expected_invariant = $case.Expected
            exit_code = $exitCode
            passed = $passed
            elapsed_seconds = [math]::Round(((Get-Date) - $started).TotalSeconds, 3)
            log = $logPath
        }
        if (-not $passed) {
            throw "TLC case '$($case.Name)' failed its registered criterion."
        }
    }
}
finally {
    Pop-Location
}
$summaryPath = Join-Path $outputRoot "tlc_summary.json"
$summary | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $summaryPath -Encoding utf8
Write-Output $summaryPath
