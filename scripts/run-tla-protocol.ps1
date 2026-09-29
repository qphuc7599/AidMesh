param(
    [string]$JarPath = $env:TLA2TOOLS_JAR,
    [string]$Output = "results\upgrade_20260718\formal\tlc",
    [int]$Workers = 4
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if (-not $JarPath) {
    $JarPath = Join-Path $root "tools\tla2tools.jar"
}
if (-not (Test-Path -LiteralPath $JarPath)) {
    throw "Khong tim thay tla2tools.jar. Dat TLA2TOOLS_JAR hoac chep jar vao tools\."
}
$JarPath = (Resolve-Path -LiteralPath $JarPath).Path
$outputRoot = Join-Path $root $Output
New-Item -ItemType Directory -Force -Path $outputRoot | Out-Null

$cases = @(
    @{ Name = "safe-quorum"; Module = "AidMeshProtocol.tla"; Config = "AidMeshProtocolSafe.cfg"; Safe = $true; Expected = "" },
    @{ Name = "safe-rights"; Module = "AidMeshProtocol.tla"; Config = "AidMeshProtocolSafeRights.cfg"; Safe = $true; Expected = "" },
    @{ Name = "safe-settlement"; Module = "AidMeshProtocol.tla"; Config = "AidMeshProtocolSafeSettlement.cfg"; Safe = $true; Expected = "" },
    @{ Name = "unsafe-quorum"; Module = "AidMeshProtocol.tla"; Config = "AidMeshProtocolUnsafeQuorum.cfg"; Safe = $false; Expected = "NoConflictingCertificates" },
    @{ Name = "unsafe-rollback"; Module = "AidMeshProtocol.tla"; Config = "AidMeshProtocolUnsafeRollback.cfg"; Safe = $false; Expected = "EpochIsolation" },
    @{ Name = "unsafe-conservation"; Module = "AidMeshProtocol.tla"; Config = "AidMeshProtocolUnsafeConservation.cfg"; Safe = $false; Expected = "RiskReleaseAccounting" },
    @{ Name = "unsafe-settlement"; Module = "AidMeshProtocol.tla"; Config = "AidMeshProtocolUnsafeSettlement.cfg"; Safe = $false; Expected = "AtMostOnceSettlement" },
    @{ Name = "integrated-safe"; Module = "AidMeshIntegrated.tla"; Config = "AidMeshIntegratedSafe.cfg"; Safe = $true; Expected = "" },
    @{ Name = "integrated-unsafe-bypass"; Module = "AidMeshIntegrated.tla"; Config = "AidMeshIntegratedUnsafeBypass.cfg"; Safe = $false; Expected = "SettlementRequiresRelease" },
    @{ Name = "integrated-unsafe-conservation"; Module = "AidMeshIntegrated.tla"; Config = "AidMeshIntegratedUnsafeConservation.cfg"; Safe = $false; Expected = "FallbackConservation" },
    @{ Name = "integrated-unsafe-settlement"; Module = "AidMeshIntegrated.tla"; Config = "AidMeshIntegratedUnsafeSettlement.cfg"; Safe = $false; Expected = "AtMostOnceSettlement" }
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
        & java -cp $JarPath tlc2.TLC -deadlock -workers $Workers -metadir $stateRoot -config $case.Config $case.Module *>&1 |
            Tee-Object -FilePath $logPath
        $exitCode = $LASTEXITCODE
        $text = Get-Content -LiteralPath $logPath -Raw
        if ($case.Safe) {
            $passed = ($exitCode -eq 0) -and ($text -match "Model checking completed. No error has been found")
        }
        else {
            $passed = ($exitCode -ne 0) -and ($text -match [regex]::Escape($case.Expected)) -and ($text -match "Error: Invariant")
        }
        $summary += [ordered]@{
            case = $case.Name
            config = $case.Config
            module = $case.Module
            negative_control = -not $case.Safe
            expected_invariant = $case.Expected
            exit_code = $exitCode
            passed = $passed
            elapsed_seconds = [math]::Round(((Get-Date) - $started).TotalSeconds, 3)
            log = $logPath
        }
        if (-not $passed) {
            throw "TLC case '$($case.Name)' khong dat tieu chi da dang ky. Xem $logPath"
        }
    }
}
finally {
    Pop-Location
}

$summaryPath = Join-Path $outputRoot "tlc_summary.json"
$summary | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $summaryPath -Encoding utf8
Write-Output $summaryPath
