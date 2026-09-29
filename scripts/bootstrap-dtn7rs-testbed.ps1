param(
    [string]$Destination = "tmp\dtn7rs-v0.21.0"
)

$ErrorActionPreference = "Stop"
$workspaceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$target = Join-Path $workspaceRoot $Destination
$archive = Join-Path $target "dtn7.tar.gz"
$expected = "fcd58cbbd4537054ddf7bee9d6276795c627a89970e53b87251fb4d26033248926504c038b368f4856a26290058d8279f4a43dca6818ec4114ceaf6cfdb25379"
$url = "https://github.com/dtn7/dtn7-rs/releases/download/v0.21.0/dtn7-0.21.0-x86_64-unknown-linux-musl.tar.gz"

New-Item -ItemType Directory -Force -Path $target | Out-Null
if (-not (Test-Path -LiteralPath $archive)) {
    Invoke-WebRequest -Uri $url -OutFile $archive
}
$actual = (Get-FileHash -Algorithm SHA512 -LiteralPath $archive).Hash.ToLowerInvariant()
if ($actual -ne $expected) {
    throw "dtn7-rs archive checksum mismatch"
}
$binary = Join-Path $target "dtn7-0.21.0\bin\dtnd"
if (-not (Test-Path -LiteralPath $binary)) {
    tar -xzf $archive -C $target
}
Write-Output (Join-Path $target "dtn7-0.21.0\bin")
