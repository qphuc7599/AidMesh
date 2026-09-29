[CmdletBinding()]
param(
    [string]$OutputDirectory = "",
    [string]$ImageTag = "airproof-snarkjs-ceremony:0.7.6",
    [string]$ExpectedR1csSha256 = "a3417107f3e40f2ba9e62a4948e5f399569b4316d76fa99c8bcfc0e0cbbc6215",
    [string]$ExpectedPtauSha256 = "3ef2ecc5b75d687048cf2d59195119b42fb07c5af639c5f283d84bfa69829e7f",
    [string]$PythonExecutable = "",
    [string]$WslDistribution = "Ubuntu"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ceremonyRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = (Resolve-Path (Join-Path $ceremonyRoot "..\..")).Path
$r1csPath = Join-Path $repoRoot "zk\build\aidmesh_voucher.r1cs"
$ptauPath = Join-Path $repoRoot "zk\public\powersOfTau28_hez_final_15.ptau"
$packageLockPath = Join-Path $repoRoot "package-lock.json"
$dockerfilePath = Join-Path $ceremonyRoot "Dockerfile"
$validatorPath = Join-Path $ceremonyRoot "validate_transcript.py"

function Get-Sha256([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Assert-InputHash([string]$Path, [string]$Expected, [string]$Label) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Required $Label input is missing: $Path"
    }
    $actual = Get-Sha256 $Path
    if ($actual -ne $Expected.ToLowerInvariant()) {
        throw "$Label SHA-256 mismatch: expected $Expected, got $actual"
    }
}

function Invoke-Docker([string[]]$Arguments, [string]$Label) {
    $previousErrorPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $lines = @(
            & $script:DockerExecutable @script:DockerPrefix @Arguments 2>&1 |
                ForEach-Object { "$_" }
        )
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorPreference
    }
    if ($exitCode -ne 0) {
        throw "$Label failed (docker exit $exitCode):`n$($lines -join [Environment]::NewLine)"
    }
    return $lines
}

function Convert-ToDockerHostPath([string]$Path) {
    if ($script:DockerTransport -eq "native-windows-cli") {
        return $Path
    }
    if ($Path -notmatch "^([A-Za-z]):\\(.*)$") {
        throw "WSL Docker transport requires a drive-letter path: $Path"
    }
    $drive = $Matches[1].ToLowerInvariant()
    $suffix = $Matches[2].Replace("\", "/")
    return "/mnt/$drive/$suffix"
}

function New-Artifact([string]$Path, [string]$RelativePath) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Expected artifact was not created: $Path"
    }
    $item = Get-Item -LiteralPath $Path
    return [ordered]@{
        path = $RelativePath.Replace("\", "/")
        sha256 = Get-Sha256 $Path
        bytes = $item.Length
    }
}

Assert-InputHash $r1csPath $ExpectedR1csSha256 "R1CS"
Assert-InputHash $ptauPath $ExpectedPtauSha256 "PTAU"
if (-not (Test-Path -LiteralPath $packageLockPath -PathType Leaf)) {
    throw "package-lock.json is required for the pinned image build"
}
$nativeDocker = Get-Command docker -ErrorAction SilentlyContinue
if ($nativeDocker) {
    $script:DockerExecutable = $nativeDocker.Source
    $script:DockerPrefix = @()
    $script:DockerTransport = "native-windows-cli"
} elseif (Get-Command wsl.exe -ErrorAction SilentlyContinue) {
    $script:DockerExecutable = (Get-Command wsl.exe).Source
    $script:DockerPrefix = @("-d", $WslDistribution, "--", "docker")
    $script:DockerTransport = "wsl:$WslDistribution"
} else {
    throw "Neither a native Docker CLI nor WSL Docker transport is available."
}

Invoke-Docker @("info", "--format", "{{.ServerVersion}}") "Docker daemon check" | Out-Null

$startedUtc = [DateTime]::UtcNow
$runId = $startedUtc.ToString("yyyyMMddTHHmmssZ")
if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $OutputDirectory = Join-Path $ceremonyRoot "output\$runId"
} elseif (-not [IO.Path]::IsPathRooted($OutputDirectory)) {
    $OutputDirectory = Join-Path $repoRoot $OutputDirectory
}
if (Test-Path -LiteralPath $OutputDirectory) {
    throw "Refusing to overwrite existing output directory: $OutputDirectory"
}
New-Item -ItemType Directory -Path $OutputDirectory | Out-Null
$outputRoot = (Resolve-Path $OutputDirectory).Path
$dockerRepoRoot = Convert-ToDockerHostPath $repoRoot
$dockerDockerfilePath = Convert-ToDockerHostPath $dockerfilePath
$dockerR1csPath = Convert-ToDockerHostPath $r1csPath
$dockerPtauPath = Convert-ToDockerHostPath $ptauPath
$dockerOutputRoot = Convert-ToDockerHostPath $outputRoot

Write-Host "Building pinned snarkjs 0.7.6 image..."
Invoke-Docker @(
    "build", "--pull", "--tag", $ImageTag,
    "--file", $dockerDockerfilePath, $dockerRepoRoot
) "Pinned image build" | ForEach-Object { Write-Host $_ }

$imageInspectText = (
    Invoke-Docker @("image", "inspect", $ImageTag) "Image identity inspection"
) -join [Environment]::NewLine
$imageInspect = @($imageInspectText | ConvertFrom-Json)
if ($imageInspect.Count -ne 1 -or $imageInspect[0].Id -notmatch "^sha256:[0-9a-f]{64}$") {
    throw "Built image did not return one immutable sha256 image ID"
}
$imageId = $imageInspect[0].Id
$repoDigests = if ($imageInspect[0].PSObject.Properties.Name -contains "RepoDigests") {
    @($imageInspect[0].RepoDigests | Where-Object { $_ })
} else {
    @()
}
$nodeVersionLines = @(Invoke-Docker @(
    "run", "--rm", "--network=none", $ImageTag, "node", "--version"
) "Node version check")
$nodeVersion = $nodeVersionLines[-1].Trim()
$snarkjsVersionLines = @(Invoke-Docker @(
    "run", "--rm", "--network=none", $ImageTag,
    "npm", "list", "snarkjs", "--depth=0"
) "snarkjs version check")
if (-not ($snarkjsVersionLines -match "snarkjs@0\.7\.6")) {
    throw "Built image does not report pinned snarkjs 0.7.6"
}
$snarkjsVersion = "0.7.6"

$commonRun = @(
    "run", "--rm", "--init", "--network=none", "--read-only",
    "--cap-drop=ALL", "--security-opt=no-new-privileges",
    "--pids-limit=256", "--memory=3g", "--cpus=2",
    "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
    "--mount", "type=bind,source=$dockerR1csPath,target=/inputs/aidmesh_voucher.r1cs,readonly",
    "--mount", "type=bind,source=$dockerPtauPath,target=/inputs/powersOfTau28_hez_final_15.ptau,readonly",
    "--mount", "type=bind,source=$dockerOutputRoot,target=/output"
)
$snarkjs = @("node", "/app/node_modules/snarkjs/build/cli.cjs")
$r1csContainer = "/inputs/aidmesh_voucher.r1cs"
$ptauContainer = "/inputs/powersOfTau28_hez_final_15.ptau"
$initialName = "phase2_0000.zkey"
$initialPath = Join-Path $outputRoot $initialName
$commands = [Collections.Generic.List[object]]::new()

Write-Host "Creating a fresh phase-2 initial zkey..."
Invoke-Docker ($commonRun + @(
    "--name", "airproof-setup-$runId", $ImageTag
) + $snarkjs + @(
    "groth16", "setup", $r1csContainer, $ptauContainer, "/output/$initialName"
)) "Fresh Groth16 phase-2 setup" | ForEach-Object { Write-Host $_ }
$commands.Add([ordered]@{
    action = "groth16_setup"
    container = "airproof-setup-$runId"
    argv = @("snarkjs", "groth16", "setup", $r1csContainer, $ptauContainer, "/output/$initialName")
}) | Out-Null

Invoke-Docker ($commonRun + @(
    "--name", "airproof-verify-0-$runId", $ImageTag
) + $snarkjs + @(
    "zkey", "verify", $r1csContainer, $ptauContainer, "/output/$initialName"
)) "Initial zkey verification" | ForEach-Object { Write-Host $_ }
$commands.Add([ordered]@{
    action = "zkey_verify"
    container = "airproof-verify-0-$runId"
    argv = @("snarkjs", "zkey", "verify", $r1csContainer, $ptauContainer, "/output/$initialName")
}) | Out-Null
$initialArtifact = New-Artifact $initialPath $initialName
$contributions = [Collections.Generic.List[object]]::new()
$previousName = $initialName

for ($index = 1; $index -le 5; $index++) {
    $identity = "sim-node-$index"
    $outputName = if ($index -eq 5) { "aidmesh_final_simulated.zkey" } else { "phase2_{0:D4}.zkey" -f $index }
    $previousPath = Join-Path $outputRoot $previousName
    $nextPath = Join-Path $outputRoot $outputName
    $inputHash = Get-Sha256 $previousPath
    $containerName = "airproof-$identity-$runId"
    $contributionStarted = [DateTime]::UtcNow.ToString("o")

    Write-Host "Running isolated contribution $identity..."
    $contributionOutput = Invoke-Docker ($commonRun + @(
        "--name", $containerName, $ImageTag,
        "node", "/app/contribute.mjs",
        "/output/$previousName", "/output/$outputName", $identity
    )) "$identity contribution"
    $metadataLine = $contributionOutput | Where-Object { $_.TrimStart().StartsWith("{") } | Select-Object -Last 1
    if (-not $metadataLine) {
        throw "$identity did not return non-secret contribution metadata"
    }
    $metadata = $metadataLine | ConvertFrom-Json
    if ($metadata.identity -ne $identity -or $metadata.entropy_logged -ne $false -or $metadata.entropy_persisted -ne $false) {
        throw "$identity returned invalid contribution metadata"
    }
    if ($metadata.contribution_hash -notmatch "^[0-9a-f]{128}$") {
        throw "$identity returned an invalid contribution hash"
    }
    $commands.Add([ordered]@{
        action = "contribute"
        identity = $identity
        container = $containerName
        argv = @("node", "/app/contribute.mjs", "/output/$previousName", "/output/$outputName", $identity)
    }) | Out-Null

    Invoke-Docker ($commonRun + @(
        "--name", "airproof-verify-$index-$runId", $ImageTag
    ) + $snarkjs + @(
        "zkey", "verify", $r1csContainer, $ptauContainer, "/output/$outputName"
    )) "$identity zkey verification" | ForEach-Object { Write-Host $_ }
    $commands.Add([ordered]@{
        action = "zkey_verify"
        identity = $identity
        container = "airproof-verify-$index-$runId"
        argv = @("snarkjs", "zkey", "verify", $r1csContainer, $ptauContainer, "/output/$outputName")
    }) | Out-Null

    $outputHash = Get-Sha256 $nextPath
    $contributions.Add([ordered]@{
        sequence = $index
        identity = $identity
        container_name = $containerName
        started_utc = $contributionStarted
        completed_utc = [DateTime]::UtcNow.ToString("o")
        input = [ordered]@{ path = $previousName; sha256 = $inputHash }
        output = New-Artifact $nextPath $outputName
        contribution_hash = $metadata.contribution_hash
        entropy_source = "container OS CSPRNG via node:crypto.randomBytes(64)"
        entropy_logged = $false
        entropy_persisted = $false
        zkey_verified_against_r1cs_ptau = $true
    }) | Out-Null
    $previousName = $outputName
}

$finalPath = Join-Path $outputRoot $previousName
$vkName = "verification_key.json"
$verifierName = "AidMeshGroth16Verifier.sol"
$vkPath = Join-Path $outputRoot $vkName
$verifierPath = Join-Path $outputRoot $verifierName

Invoke-Docker ($commonRun + @(
    "--name", "airproof-export-vk-$runId", $ImageTag
) + $snarkjs + @(
    "zkey", "export", "verificationkey", "/output/$previousName", "/output/$vkName"
)) "Verification-key export" | ForEach-Object { Write-Host $_ }
Invoke-Docker ($commonRun + @(
    "--name", "airproof-export-solidity-$runId", $ImageTag
) + $snarkjs + @(
    "zkey", "export", "solidityverifier", "/output/$previousName", "/output/$verifierName"
)) "Solidity-verifier export" | ForEach-Object { Write-Host $_ }
$commands.Add([ordered]@{
    action = "export_verification_key"
    container = "airproof-export-vk-$runId"
    argv = @("snarkjs", "zkey", "export", "verificationkey", "/output/$previousName", "/output/$vkName")
}) | Out-Null
$commands.Add([ordered]@{
    action = "export_solidity_verifier"
    container = "airproof-export-solidity-$runId"
    argv = @("snarkjs", "zkey", "export", "solidityverifier", "/output/$previousName", "/output/$verifierName")
}) | Out-Null

$finalArtifact = New-Artifact $finalPath $previousName
$vkArtifact = New-Artifact $vkPath $vkName
$verifierArtifact = New-Artifact $verifierPath $verifierName
$transcriptPath = Join-Path $outputRoot "transcript.json"
$transcript = [ordered]@{
    schema_version = 1
    run_id = $runId
    status = "completed"
    single_operator_single_host_simulation = $true
    production_trusted_setup = $false
    independent_multiparty_trust = $false
    toxic_waste_unknowable_claim = $false
    operator_assumption = "The single researcher is assumed honest only for development and reproducibility."
    contributor_container_count = 5
    five_contributor_containers_completed = $true
    started_utc = $startedUtc.ToString("o")
    completed_utc = [DateTime]::UtcNow.ToString("o")
    inputs = [ordered]@{
        r1cs = New-Artifact $r1csPath "zk/build/aidmesh_voucher.r1cs"
        ptau = New-Artifact $ptauPath "zk/public/powersOfTau28_hez_final_15.ptau"
        package_lock = New-Artifact $packageLockPath "package-lock.json"
    }
    toolchain = [ordered]@{
        docker_transport = $script:DockerTransport
        image_tag = $ImageTag
        immutable_image_id = $imageId
        repository_digests = $repoDigests
        base_image = "node:22.17.0-bookworm-slim@sha256:b04ce4ae4e95b522112c2e5c52f781471a5cbc3b594527bcddedee9bc48c03a0"
        node_version = $nodeVersion
        snarkjs_version = $snarkjsVersion
    }
    isolation = [ordered]@{
        execution = "five sequential docker run --rm contributor containers"
        network = "none"
        root_filesystem = "read-only"
        capabilities = "all dropped"
        no_new_privileges = $true
        entropy_policy = "Per-container OS CSPRNG; never logged, persisted, or passed on command line."
        host_boundary = "All containers are controlled by one researcher on one host."
    }
    initial_zkey = $initialArtifact
    contributions = @($contributions)
    commands = @($commands)
    artifacts = [ordered]@{
        final_zkey = $finalArtifact
        verification_key = $vkArtifact
        solidity_verifier = $verifierArtifact
    }
    verification = [ordered]@{
        initial_zkey_verified = $true
        every_contribution_verified = $true
        final_zkey_verified_against_actual_r1cs_ptau = $true
        chain_hash_continuity_recorded = $true
    }
}
$transcript | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $transcriptPath -Encoding utf8

if ([string]::IsNullOrWhiteSpace($PythonExecutable)) {
    $venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
    $PythonExecutable = if (Test-Path -LiteralPath $venvPython) { $venvPython } else { "python" }
}
& $PythonExecutable $validatorPath --transcript $transcriptPath --output-root $outputRoot
if ($LASTEXITCODE -ne 0) {
    throw "Transcript validation failed"
}

$summaryPath = Join-Path $outputRoot "SUMMARY.md"
$transcriptHash = Get-Sha256 $transcriptPath
@"
# Five-contributor Docker workflow simulation

- Run ID: ``$runId``
- Status: completed
- ``single_operator_single_host_simulation=true``
- ``production_trusted_setup=false``
- Contributors: ``sim-node-1`` through ``sim-node-5`` in sequential ``docker run --rm`` containers
- R1CS SHA-256: ``$($transcript.inputs.r1cs.sha256)``
- PTAU SHA-256: ``$($transcript.inputs.ptau.sha256)``
- Final zkey SHA-256: ``$($finalArtifact.sha256)``
- Verification key SHA-256: ``$($vkArtifact.sha256)``
- Solidity verifier SHA-256: ``$($verifierArtifact.sha256)``
- Transcript SHA-256: ``$transcriptHash``

This is a single-operator, single-host, five-container workflow simulation. It assumes
the researcher behaves honestly for development and reproducibility. It is not an
independent multiparty ceremony, not a production trusted setup, and is not evidence
that toxic waste is unknowable.
"@ | Set-Content -LiteralPath $summaryPath -Encoding utf8

$checksumPath = Join-Path $outputRoot "CHECKSUMS.txt"
$checksumFiles = @(
    $initialName,
    "phase2_0001.zkey",
    "phase2_0002.zkey",
    "phase2_0003.zkey",
    "phase2_0004.zkey",
    $previousName,
    $vkName,
    $verifierName,
    "transcript.json",
    "SUMMARY.md"
)
$checksumLines = foreach ($name in $checksumFiles) {
    $path = Join-Path $outputRoot $name
    "$(Get-Sha256 $path)  $name"
}
$checksumLines | Set-Content -LiteralPath $checksumPath -Encoding ascii

Write-Host "Completed Docker workflow simulation: $outputRoot"
Write-Host "Final zkey SHA-256: $($finalArtifact.sha256)"
Write-Output $outputRoot
