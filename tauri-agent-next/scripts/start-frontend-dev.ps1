[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [int]$Port,

    [Parameter(Mandatory = $true)]
    [string]$BackendUrl,

    [Parameter(Mandatory = $true)]
    [string]$RootDir
)

$ErrorActionPreference = "Stop"

$Host.UI.RawUI.WindowTitle = "tauri-agent-next frontend"
$env:VITE_API_PROXY_TARGET = $BackendUrl

Set-Location $RootDir

Write-Host "========================================"
Write-Host "  tauri-agent-next frontend"
Write-Host "========================================"
Write-Host
Write-Host ("Proxy target: {0}" -f $BackendUrl)
Write-Host ("Starting Vite HMR server on port {0}..." -f $Port)
Write-Host "Press Ctrl+C to stop the server"
Write-Host

npm run dev:frontend -- --host 127.0.0.1 --port $Port
exit $LASTEXITCODE
