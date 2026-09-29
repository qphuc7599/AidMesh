param(
    [string]$Output = "second_host\reproduction_20260724",
    [switch]$SkipFullE2E,
    [int]$ZkIterations = 65,
    [string]$ReferenceHostFingerprint = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Khong tim thay .venv. Hay cai requirements.lock va npm ci truoc."
}

$outputRoot = Join-Path $root $Output
New-Item -ItemType Directory -Force -Path $outputRoot | Out-Null
$commandLog = Join-Path $outputRoot "commands.log"
$steps = [System.Collections.Generic.List[object]]::new()

function Get-HostFingerprint {
    $identity = @(
        $env:COMPUTERNAME
        (Get-CimInstance Win32_ComputerSystemProduct | Select-Object -First 1 -ExpandProperty UUID)
        (Get-CimInstance Win32_BIOS | Select-Object -First 1 -ExpandProperty SerialNumber)
        (Get-CimInstance Win32_Processor | Select-Object -First 1 -ExpandProperty ProcessorId)
    ) -join "|"
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($identity)
        return ([System.BitConverter]::ToString($sha.ComputeHash($bytes))).Replace("-", "").ToLower()
    }
    finally {
        $sha.Dispose()
    }
}

function Invoke-ReproductionStep {
    param(
        [string]$Name,
        [scriptblock]$Command
    )
    $started = Get-Date
    "[$($started.ToString('o'))] START $Name" | Add-Content -LiteralPath $commandLog -Encoding utf8
    try {
        & $Command *>&1 | Tee-Object -FilePath (Join-Path $outputRoot "$Name.log") -Append
        if ($LASTEXITCODE -ne 0) {
            throw "Exit code $LASTEXITCODE"
        }
        $status = "pass"
    }
    catch {
        $status = "fail"
        "[$((Get-Date).ToString('o'))] ERROR $Name $($_.Exception.Message)" |
            Add-Content -LiteralPath $commandLog -Encoding utf8
        throw
    }
    finally {
        $finished = Get-Date
        $steps.Add([ordered]@{
            name = $Name
            status = $status
            started_utc = $started.ToUniversalTime().ToString("o")
            finished_utc = $finished.ToUniversalTime().ToString("o")
            elapsed_seconds = [math]::Round(($finished - $started).TotalSeconds, 3)
        })
        "[$($finished.ToString('o'))] END $Name $status" |
            Add-Content -LiteralPath $commandLog -Encoding utf8
    }
}

Push-Location $root
try {
    $hostFingerprint = Get-HostFingerprint
    if ($ReferenceHostFingerprint -and
        $hostFingerprint -eq $ReferenceHostFingerprint.ToLower()) {
        throw "Host fingerprint trung reference host; day khong phai second-host reproduction."
    }
    $environmentPath = Join-Path $outputRoot "environment.txt"
    @(
        "captured_utc=$((Get-Date).ToUniversalTime().ToString('o'))"
        "computer=$env:COMPUTERNAME"
        "host_fingerprint_sha256=$hostFingerprint"
        "os=$([System.Environment]::OSVersion.VersionString)"
        "powershell=$($PSVersionTable.PSVersion)"
        "cpu=$((Get-CimInstance Win32_Processor | Select-Object -First 1 -ExpandProperty Name))"
        "ram_bytes=$((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory)"
        "python=$(& $python --version 2>&1)"
        "node=$(& node --version 2>&1)"
        "npm=$(& npm --version 2>&1)"
        "java=$((& java -version 2>&1) -join ' | ')"
        "solc=$((& (Join-Path $root 'tools\solc\solc-windows-v0.8.36.exe') --version 2>&1) -join ' | ')"
    ) | Set-Content -LiteralPath $environmentPath -Encoding utf8

    Invoke-ReproductionStep "pytest" {
        & $python -m pytest -q
    }
    Invoke-ReproductionStep "tlc_integrated" {
        & powershell -ExecutionPolicy Bypass -File scripts\run-tla-integrated.ps1 `
            -Output (Join-Path $Output "formal\tlc_integrated")
    }
    Invoke-ReproductionStep "protocol_refinement" {
        & $python -m aidmesh.cli protocol-refinement `
            --output (Join-Path $Output "formal\protocol_refinement") `
            --traces 256 --steps 64
    }
    Invoke-ReproductionStep "zk_p1_v2_tagged" {
        & $python -m aidmesh.cli zk-bench `
            --iterations $ZkIterations --warmup 5 `
            --output (Join-Path $Output "zk_p1_v2_tagged.csv")
    }
    Invoke-ReproductionStep "evm_solidity_0_8_36" {
        & $python -m aidmesh.cli evm-bench `
            --output (Join-Path $Output "evm")
    }
    Invoke-ReproductionStep "heldout_primary_baselines" {
        & $python -m aidmesh.cli heldout-temporal-frontier `
            --trace-root external\sociopatterns\normalized `
            --output (Join-Path $Output "heldout_primary_baselines") `
            --seed-start 7301 --seeds 8 --buffer-bytes 98304
    }
    Invoke-ReproductionStep "privacy_sparse_cover" {
        & $python -m aidmesh.cli supporting-privacy-pareto `
            --registry configs\supporting_2026Q3\privacy_pareto\registry_sparse_preregistered.yaml `
            --output (Join-Path $Output "privacy_sparse_cover")
    }
    Invoke-ReproductionStep "distributed_rights" {
        & $python -m aidmesh.cli distributed-rights-campaign `
            --output (Join-Path $Output "distributed_rights") --trials 8
    }

    if (-not $SkipFullE2E) {
        $e2eOutput = Join-Path $Output "e2e_p1_v2_tagged_full"
        Invoke-ReproductionStep "e2e_p1_v2_tagged_full" {
            & $python -m aidmesh.cli e2e-local-run `
                --registry configs\e2e\registry_p1_v2_tagged_full.yaml `
                --trace-root external\sociopatterns\normalized `
                --output $e2eOutput
        }
        Invoke-ReproductionStep "e2e_analysis" {
            & $python -m aidmesh.cli e2e-local-analyze `
                --input $e2eOutput --output (Join-Path $e2eOutput "analysis")
        }
        Invoke-ReproductionStep "e2e_checksum_verify" {
            & $python -m aidmesh.cli e2e-local-verify --input $e2eOutput
        }
        Invoke-ReproductionStep "integrated_trace_refinement" {
            & $python -m aidmesh.formal.integrated_refinement `
                --campaign $e2eOutput `
                --output (Join-Path $Output "formal\integrated_trace_refinement")
        }
    }

    $result = [ordered]@{
        schema_version = 1
        status = "pass"
        independent_claim = "Reviewer must complete and sign reviewer_declaration.md"
        canonical_profile = "aidmesh-voucher-p1-groth16-bn128-domain-v2-tagged"
        expected_constraints = 31011
        expected_public_signals = 17
        expected_fault_controls = 15
        full_e2e_skipped = [bool]$SkipFullE2E
        host_fingerprint_sha256 = $hostFingerprint
        reference_host_fingerprint_sha256 = $ReferenceHostFingerprint.ToLower()
        fingerprint_differs_from_reference = (
            -not $ReferenceHostFingerprint -or
            $hostFingerprint -ne $ReferenceHostFingerprint.ToLower()
        )
        steps = $steps
    }
    $result | ConvertTo-Json -Depth 8 |
        Set-Content -LiteralPath (Join-Path $outputRoot "second_host_result.json") -Encoding utf8

    $checksumPath = Join-Path $outputRoot "CHECKSUMS.sha256"
    Get-ChildItem $outputRoot -Recurse -File |
        Where-Object { $_.FullName -ne $checksumPath } |
        Sort-Object FullName |
        ForEach-Object {
            $relative = $_.FullName.Substring($outputRoot.Length).TrimStart("\").Replace("\", "/")
            "$(($_ | Get-FileHash -Algorithm SHA256).Hash.ToLower())  $relative"
        } | Set-Content -LiteralPath $checksumPath -Encoding ascii

    Write-Output (Join-Path $outputRoot "second_host_result.json")
    Write-Output $checksumPath
}
finally {
    Pop-Location
}
