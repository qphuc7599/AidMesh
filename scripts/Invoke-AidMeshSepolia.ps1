param(
    [ValidateSet("Preflight", "Deploy", "Parity", "PlanWindow")]
    [string]$Stage = "Preflight",
    [string]$RunId = "",
    [ValidateSet(0, 1, 2, 3)]
    [int]$Window = 0,
    [int]$Confirmations = 3,
    [string]$DeploymentManifest = "results\testnet\deployment_sepolia.json",
    [string]$Output = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Khong tim thay Python tai $Python. Hay tao/cai .venv truoc."
}
if ($Stage -eq "Parity" -and [string]::IsNullOrWhiteSpace($RunId)) {
    throw "Stage Parity bat buoc co -RunId, vi du 20260727T0900Z-r1."
}
if ($Stage -eq "PlanWindow" -and $Window -eq 0) {
    throw "Stage PlanWindow bat buoc co -Window 1, 2 hoac 3."
}

function ConvertFrom-SecureStringTemporary {
    param([Security.SecureString]$Value)
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Value)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
}

$secureRpc = Read-Host "Sepolia RPC URL (khong ghi vao file)" -AsSecureString
$secureKey = Read-Host "Private key cua vi CHI DUNG CHO SEPOLIA" -AsSecureString

try {
    $env:SEPOLIA_RPC_URL = ConvertFrom-SecureStringTemporary $secureRpc
    $env:SEPOLIA_PRIVATE_KEY = ConvertFrom-SecureStringTemporary $secureKey
    Set-Location -LiteralPath $ProjectRoot

    switch ($Stage) {
        "Preflight" {
            if ([string]::IsNullOrWhiteSpace($Output)) {
                $Output = "results\testnet\sepolia_preflight.json"
            }
            & $Python -m aidmesh.cli sepolia-preflight --output $Output
        }
        "Deploy" {
            if ([string]::IsNullOrWhiteSpace($Output)) {
                $Output = $DeploymentManifest
            }
            & $Python -m aidmesh.cli testnet-deploy --deploy --output $Output
        }
        "Parity" {
            if ([string]::IsNullOrWhiteSpace($Output)) {
                $Output = "results\testnet\sepolia_parity\$RunId"
            }
            & $Python -m aidmesh.cli sepolia-parity `
                --deployment $DeploymentManifest `
                --output $Output `
                --run-id $RunId `
                --confirmations $Confirmations
        }
        "PlanWindow" {
            & $Python -m aidmesh.cli sepolia-plan-window `
                --window $Window `
                --plan "configs\sepolia\campaign_plan.yaml" `
                --checksum "configs\sepolia\campaign_plan.sha256" `
                --analysis-output "results\testnet\sepolia_analysis_registered"
        }
    }
    if ($LASTEXITCODE -ne 0) {
        throw "AidMesh Sepolia stage '$Stage' that bai voi exit code $LASTEXITCODE."
    }
}
finally {
    Remove-Item Env:SEPOLIA_PRIVATE_KEY -ErrorAction SilentlyContinue
    Remove-Item Env:SEPOLIA_RPC_URL -ErrorAction SilentlyContinue
    $secureKey = $null
    $secureRpc = $null
}
