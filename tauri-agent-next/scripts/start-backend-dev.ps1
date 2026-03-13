[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [int]$Port,

    [Parameter(Mandatory = $true)]
    [string]$DataDir
)

$ErrorActionPreference = "Stop"

$rootDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$backendDir = Join-Path $rootDir "python-backend"

$Host.UI.RawUI.WindowTitle = "tauri-agent-next backend"
$env:PYTHONUTF8 = "1"
$env:TAURI_AGENT_NEXT_DATA_DIR = $DataDir

Set-Location $backendDir

Write-Host "========================================"
Write-Host "  tauri-agent-next backend"
Write-Host "========================================"
Write-Host
Write-Host ("Runtime data dir: {0}" -f $DataDir)
Write-Host ("Starting FastAPI server on port {0}..." -f $Port)
Write-Host "Press Ctrl+C to stop the server"
Write-Host

python -m uvicorn main:app --reload --host 127.0.0.1 --port $Port
exit $LASTEXITCODE
