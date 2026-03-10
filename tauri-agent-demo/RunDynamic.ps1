$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

param(
  [int]$BackendPort = 0,
  [int]$WaitSeconds = 30,
  [switch]$NoFrontend
)

function Get-FreeTcpPort {
  $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
  $listener.Start()
  try {
    return $listener.LocalEndpoint.Port
  } finally {
    $listener.Stop()
  }
}

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

if ($BackendPort -le 0) {
  $BackendPort = Get-FreeTcpPort
}

$backendUrl = "http://127.0.0.1:$BackendPort"

Write-Host "========================================"
Write-Host "  Full Stack Launcher (Dynamic Ports)"
Write-Host "========================================"
Write-Host ""
Write-Host "[Info] Backend URL: $backendUrl"
Write-Host "[Info] Frontend (Tauri/Vite) dev URL remains fixed in config (default http://localhost:1420)"
Write-Host ""

Write-Host "[1/2] Starting backend on port $BackendPort..."
Start-Process -FilePath (Join-Path $root "StartBackend.bat") -ArgumentList @("$BackendPort") -WorkingDirectory $root

Write-Host "[Info] Waiting for backend readiness..."
$deadline = (Get-Date).AddSeconds([Math]::Max(1, $WaitSeconds))
$ready = $false
while ((Get-Date) -lt $deadline) {
  try {
    $resp = Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 -Uri "$backendUrl/"
    if ($resp.StatusCode -eq 200) {
      $ready = $true
      break
    }
  } catch {
    # ignore and retry
  }
  Start-Sleep -Milliseconds 250
}

if (-not $ready) {
  Write-Warning "Backend not ready after $WaitSeconds seconds. Continuing anyway."
}

if ($NoFrontend) {
  Write-Host "[2/2] Skipping frontend (NoFrontend set)."
  exit 0
}

Write-Host "[2/2] Starting Tauri desktop app..."
$frontendCmd = "set VITE_API_BASE_URL=$backendUrl&& call `"$root\StartFrontend.bat`""
Start-Process -FilePath "cmd.exe" -ArgumentList "/c", $frontendCmd -WorkingDirectory $root

Write-Host ""
Write-Host "========================================"
Write-Host "[Done] Started both services."
Write-Host "- Backend:  $backendUrl"
Write-Host "- Frontend: Tauri Desktop Window"
Write-Host "========================================"
