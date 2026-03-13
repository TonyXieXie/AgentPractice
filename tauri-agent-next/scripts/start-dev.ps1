[CmdletBinding()]
param(
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"

$rootDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$backendDir = Join-Path $rootDir "python-backend"
$runtimeDir = Join-Path $rootDir ".tauri-agent-next-data"
$portsFile = Join-Path $runtimeDir "last-dev-ports.json"
$backendScript = Join-Path $PSScriptRoot "start-backend-dev.ps1"
$frontendScript = Join-Path $PSScriptRoot "start-frontend-dev.ps1"

foreach ($commandName in @("python", "npm")) {
    if (-not (Get-Command $commandName -ErrorAction SilentlyContinue)) {
        throw "$commandName was not found in PATH."
    }
}

New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null

function Get-FreePort {
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
    try {
        $listener.Start()
        return $listener.LocalEndpoint.Port
    }
    finally {
        $listener.Stop()
    }
}

$backendPort = Get-FreePort
do {
    $frontendPort = Get-FreePort
} while ($frontendPort -eq $backendPort)

$backendUrl = "http://127.0.0.1:$backendPort"
$frontendUrl = "http://127.0.0.1:$frontendPort"

[ordered]@{
    backend_port = $backendPort
    frontend_port = $frontendPort
    backend_url = $backendUrl
    frontend_url = $frontendUrl
} | ConvertTo-Json | Set-Content -Path $portsFile -Encoding UTF8

Write-Host "========================================"
Write-Host "  tauri-agent-next dev launcher"
Write-Host "========================================"
Write-Host
Write-Host ("Backend hot reload:  {0}" -f $backendUrl)
Write-Host ("Frontend hot reload: {0}" -f $frontendUrl)
Write-Host ("Port record:         {0}" -f $portsFile)
Write-Host
Write-Host "Starting backend window..."
Start-Process powershell -WorkingDirectory $backendDir -ArgumentList @(
    "-NoProfile",
    "-NoExit",
    "-ExecutionPolicy", "Bypass",
    "-File", $backendScript,
    "-Port", $backendPort,
    "-DataDir", $runtimeDir
)

Write-Host "Starting frontend window..."
Start-Process powershell -WorkingDirectory $rootDir -ArgumentList @(
    "-NoProfile",
    "-NoExit",
    "-ExecutionPolicy", "Bypass",
    "-File", $frontendScript,
    "-Port", $frontendPort,
    "-BackendUrl", $backendUrl,
    "-RootDir", $rootDir
)

if (-not $NoBrowser) {
    Write-Host "Opening browser..."
    Start-Process $frontendUrl
}

Write-Host
Write-Host "Done. Close the two spawned terminal windows to stop the dev servers."
