param(
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

$tools = @(
    @{
        Name = "Solidity 0.8.36 Windows compiler"
        Url = "https://github.com/argotorg/solidity/releases/download/v0.8.36/solc-windows.exe"
        RelativePath = "tools\solc\solc-windows-v0.8.36.exe"
        Sha256 = "38cf5df656da822cf88b854cf736475a196c679f4f5f3f15ddd8c85ce5dbb36a"
    },
    @{
        Name = "TLA+ tools 1.7.4 / TLC rev. 5a47802"
        Url = "https://github.com/tlaplus/tlaplus/releases/download/v1.7.4/tla2tools.jar"
        RelativePath = "tools\tla2tools.jar"
        Sha256 = "936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88"
    }
)

foreach ($tool in $tools) {
    $destination = Join-Path $root $tool.RelativePath
    $parent = Split-Path -Parent $destination
    New-Item -ItemType Directory -Force -Path $parent | Out-Null

    if ((Test-Path -LiteralPath $destination) -and -not $Force) {
        $actual = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash.ToLower()
        if ($actual -eq $tool.Sha256) {
            Write-Output "OK (already present): $($tool.Name)"
            continue
        }
        throw "Hash mismatch for existing file '$destination'. Use -Force only after auditing it."
    }

    $temporary = "$destination.download"
    Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
    try {
        Invoke-WebRequest -UseBasicParsing -Uri $tool.Url -OutFile $temporary
        $actual = (Get-FileHash -LiteralPath $temporary -Algorithm SHA256).Hash.ToLower()
        if ($actual -ne $tool.Sha256) {
            throw "Downloaded hash mismatch for $($tool.Name): expected $($tool.Sha256), got $actual"
        }
        Move-Item -LiteralPath $temporary -Destination $destination -Force
        Write-Output "Installed and verified: $($tool.Name)"
    }
    finally {
        Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
    }
}
