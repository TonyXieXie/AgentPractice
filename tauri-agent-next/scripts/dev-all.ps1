$ErrorActionPreference = "Stop"

$workspace = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$backend = Start-Process cmd.exe -ArgumentList "/c", "npm run dev:backend" -WorkingDirectory $workspace -PassThru

try {
    Push-Location $workspace
    npm run dev:desktop
}
finally {
    Pop-Location
    if ($backend -and -not $backend.HasExited) {
        Stop-Process -Id $backend.Id -Force
    }
}
