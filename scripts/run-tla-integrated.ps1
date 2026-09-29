param(
    [string]$JarPath = $env:TLA2TOOLS_JAR,
    [string]$Output = "results\extension_20260724\formal\tlc_integrated_protocol_faithful",
    [int]$Workers = 4
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if (-not $JarPath) {
    $JarPath = Join-Path $root "tools\tla2tools.jar"
}
if (-not (Test-Path -LiteralPath $JarPath)) {
    throw "Khong tim thay tla2tools.jar."
}
$JarPath = (Resolve-Path -LiteralPath $JarPath).Path
$outputRoot = Join-Path $root $Output
New-Item -ItemType Directory -Force -Path $outputRoot | Out-Null

$cases = @(
    @{ Name = "integrated-safe-mixed-path"; Config = "AidMeshIntegratedSafe.cfg"; Safe = $true; Expected = "" },
    @{ Name = "integrated-safe-conflict"; Config = "AidMeshIntegratedSafeConflict.cfg"; Safe = $true; Expected = "" },
    @{ Name = "integrated-unsafe-bypass"; Config = "AidMeshIntegratedUnsafeBypass.cfg"; Safe = $false; Expected = "SettlementRequiresRelease" },
    @{ Name = "integrated-unsafe-conservation"; Config = "AidMeshIntegratedUnsafeConservation.cfg"; Safe = $false; Expected = "FallbackConservation" },
    @{ Name = "integrated-unsafe-settlement"; Config = "AidMeshIntegratedUnsafeSettlement.cfg"; Safe = $false; Expected = "AtMostOnceSettlement" }
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
        & java -XX:+UseParallelGC -cp $JarPath tlc2.TLC -deadlock -workers $Workers `
            -metadir $stateRoot -config $case.Config AidMeshIntegrated.tla *>&1 |
            Tee-Object -FilePath $logPath
        $exitCode = $LASTEXITCODE
        $content = Get-Content -LiteralPath $logPath -Raw
        if ($case.Safe) {
            $passed = ($exitCode -eq 0) -and
                ($content -match "Model checking completed. No error has been found")
        }
        else {
            $passed = ($exitCode -ne 0) -and
                ($content -match [regex]::Escape($case.Expected)) -and
                ($content -match "Error: Invariant")
        }
        $stateMatches = [regex]::Matches(
            $content,
            "([0-9,]+) states generated, ([0-9,]+) distinct states found"
        )
        $generatedStates = $null
        $distinctStates = $null
        if ($stateMatches.Count -gt 0) {
            $lastStateMatch = $stateMatches[$stateMatches.Count - 1]
            $generatedStates = [int64]($lastStateMatch.Groups[1].Value.Replace(",", ""))
            $distinctStates = [int64]($lastStateMatch.Groups[2].Value.Replace(",", ""))
        }
        $depthMatch = [regex]::Match(
            $content,
            "depth of the complete state graph search is ([0-9]+)"
        )
        $depth = if ($depthMatch.Success) {
            [int]$depthMatch.Groups[1].Value
        }
        else {
            $null
        }
        $summary += [ordered]@{
            case = $case.Name
            module = "AidMeshIntegrated.tla"
            config = $case.Config
            negative_control = -not $case.Safe
            expected_invariant = $case.Expected
            exit_code = $exitCode
            passed = $passed
            generated_states = $generatedStates
            distinct_states = $distinctStates
            depth = $depth
            elapsed_seconds = [math]::Round(((Get-Date) - $started).TotalSeconds, 3)
            log = $logPath
        }
        if (-not $passed) {
            throw "TLC case '$($case.Name)' khong dat tieu chi da dang ky."
        }
    }
}
finally {
    Pop-Location
}

$summaryPath = Join-Path $outputRoot "tlc_integrated_summary.json"
$summary | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $summaryPath -Encoding utf8
$checksumPath = Join-Path $outputRoot "CHECKSUMS.sha256"
Get-ChildItem -LiteralPath $outputRoot -Recurse -File |
    Where-Object {
        $_.FullName -ne $checksumPath -and
        $_.FullName -notmatch '\\states\\'
    } |
    Sort-Object FullName |
    ForEach-Object {
        $relative = $_.FullName.Substring($outputRoot.Length).TrimStart("\").Replace("\", "/")
        "$(($_ | Get-FileHash -Algorithm SHA256).Hash.ToLower())  $relative"
    } | Set-Content -LiteralPath $checksumPath -Encoding ascii
Write-Output $summaryPath
Write-Output $checksumPath
