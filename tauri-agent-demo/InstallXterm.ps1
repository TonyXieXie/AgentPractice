# Installs xterm into this project with a local npm cache to avoid global config issues.

param(
    [switch]$NoPause
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

function Find-NpmCmd {
    $localNpm = Join-Path $root "..\.tools\node-v20.19.0-win-x64\npm.cmd"
    if (Test-Path $localNpm) { return $localNpm }
    $cmd = Get-Command npm -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

$npmCmd = Find-NpmCmd
if (-not $npmCmd) {
    Write-Error "npm not found. Install Node.js or ensure npm is in PATH."
}

$cacheDir = Join-Path $root ".npm-cache-install"
$tmpDir = Join-Path $root ".npm-tmp-install"
New-Item -ItemType Directory -Force -Path $cacheDir | Out-Null
New-Item -ItemType Directory -Force -Path $tmpDir | Out-Null

# Avoid offline mode and local proxy overrides for this session.
$env:npm_config_offline = "false"
$env:npm_config_cache = $cacheDir
$env:npm_config_tmp = $tmpDir
$env:HTTP_PROXY = ""
$env:HTTPS_PROXY = ""
$env:ALL_PROXY = ""

Write-Host "Using npm: $npmCmd"
Write-Host "Cache: $cacheDir"
Write-Host "Tmp:   $tmpDir"
Write-Host ""

$exitCode = 0
try {
    & $npmCmd install xterm --no-fund --no-audit --prefer-online
    Write-Host ""
    Write-Host "xterm install complete."
} catch {
    $exitCode = 1
    Write-Host ""
    Write-Host "xterm install failed."
    Write-Host "If you see EPERM unlink errors:"
    Write-Host "1) Close any antivirus or file indexer temporarily."
    Write-Host "2) Re-run this script in an Administrator PowerShell."
    Write-Host "3) Delete $cacheDir and $tmpDir, then re-run."
}

if (-not $NoPause) {
    Write-Host ""
    Read-Host "Press Enter to exit"
}

if ($exitCode -ne 0) {
    $global:LASTEXITCODE = $exitCode
}
