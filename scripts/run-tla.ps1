param(
    [ValidateSet("safe", "unsafe")]
    [string]$Mode = "safe",
    [string]$JarPath = $env:TLA2TOOLS_JAR
)

$root = Split-Path -Parent $PSScriptRoot
if (-not $JarPath) {
    $JarPath = Join-Path $root "tools\tla2tools.jar"
}
if (-not (Test-Path -LiteralPath $JarPath)) {
    throw "TLA+ tools jar not found. Set TLA2TOOLS_JAR or place tla2tools.jar in tools\\."
}
$JarPath = (Resolve-Path -LiteralPath $JarPath).Path

$config = if ($Mode -eq "safe") { "AidMeshSafe.cfg" } else { "AidMeshUnsafe.cfg" }
Push-Location (Join-Path $root "formal")
try {
    & java -cp $JarPath tlc2.TLC -deadlock -config $config AidMeshSafety.tla
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
