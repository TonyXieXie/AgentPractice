# Project dependency setup for Windows
# - Installs project-local deps (node_modules, python venv requirements)
# - Checks system deps (Node, Python, Rust); prompts to install if missing

$ErrorActionPreference = "Stop"

$script:Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $script:Root

$missing = New-Object System.Collections.Generic.List[string]
$installed = New-Object System.Collections.Generic.List[string]

function Write-Section([string]$title) {
    Write-Host ""
    Write-Host "== $title =="
}

function Prompt-YesNo([string]$message) {
    while ($true) {
        $resp = Read-Host "$message (y/n)"
        if ($resp -match '^[Yy]$') { return $true }
        if ($resp -match '^[Nn]$') { return $false }
    }
}

function Normalize-Path([string]$path) {
    return $path.TrimEnd('\')
}

function Add-SessionPath([string]$path) {
    if (-not $path) { return }
    $normalized = Normalize-Path $path
    $parts = $env:PATH -split ';' | Where-Object { $_ -ne '' }
    foreach ($p in $parts) {
        if ((Normalize-Path $p).ToLowerInvariant() -eq $normalized.ToLowerInvariant()) {
            return
        }
    }
    $env:PATH = "$normalized;$env:PATH"
}

function Add-UserPath([string]$path) {
    if (-not $path) { return }
    $normalized = Normalize-Path $path
    $current = [Environment]::GetEnvironmentVariable("PATH", "User")
    if (-not $current) { $current = "" }
    $parts = $current -split ';' | Where-Object { $_ -ne '' }
    foreach ($p in $parts) {
        if ((Normalize-Path $p).ToLowerInvariant() -eq $normalized.ToLowerInvariant()) {
            return
        }
    }
    $newPath = if ($current) { "$current;$normalized" } else { $normalized }
    [Environment]::SetEnvironmentVariable("PATH", $newPath, "User")
}

function Get-NodeInfo([string]$nodeHome) {
    $nodeExe = Join-Path $nodeHome "node.exe"
    if (Test-Path $nodeExe) {
        return @{ Exe = $nodeExe; Home = $nodeHome; Source = "tools" }
    }
    $cmd = Get-Command node -ErrorAction SilentlyContinue
    if ($cmd) {
        return @{ Exe = $cmd.Source; Home = (Split-Path $cmd.Source -Parent); Source = "path" }
    }
    return $null
}

function Get-NodeVersion([string]$nodeExe) {
    try {
        $raw = & $nodeExe --version 2>$null
        if ($raw -match '^v(\d+)\.(\d+)\.(\d+)') {
            return [version]"$($matches[1]).$($matches[2]).$($matches[3])"
        }
    } catch {}
    return $null
}

function Get-PythonCommand {
    $localPython = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
    if (Test-Path $localPython) { return @($localPython) }
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) { return @("py", "-3.12") }
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) { return @($python.Source) }
    return $null
}

function Get-PythonVersion([string[]]$cmd) {
    try {
        $args = @()
        if ($cmd.Length -gt 1) { $args = $cmd[1..($cmd.Length - 1)] }
        $raw = & $cmd[0] @args -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}.{sys.version_info[2]}')" 2>$null
        if ($raw -match '^(\d+)\.(\d+)\.(\d+)') {
            return [version]"$($matches[1]).$($matches[2]).$($matches[3])"
        }
    } catch {}
    return $null
}

function Invoke-PythonCommand([string[]]$cmd, [string[]]$args) {
    if (-not $cmd -or $cmd.Length -eq 0) { return }
    if ($cmd.Length -gt 1) {
        & $cmd[0] @($cmd[1..($cmd.Length - 1)]) @args
    } else {
        & $cmd[0] @args
    }
}

function Install-Node([string]$nodeVersion, [string]$toolsRoot) {
    $nodeDir = "node-$nodeVersion-win-x64"
    $nodeHome = Join-Path $toolsRoot $nodeDir
    $nodeExe = Join-Path $nodeHome "node.exe"
    if (Test-Path $nodeExe) { return $nodeHome }

    if (-not (Test-Path $toolsRoot)) {
        New-Item -ItemType Directory -Path $toolsRoot | Out-Null
    }

    $zipUrl = "https://nodejs.org/dist/$nodeVersion/$nodeDir.zip"
    $zipPath = Join-Path $toolsRoot "$nodeDir.zip"

    Write-Host "Downloading Node.js $nodeVersion..."
    Invoke-WebRequest -Uri $zipUrl -OutFile $zipPath
    Write-Host "Extracting Node.js..."
    Expand-Archive -Path $zipPath -DestinationPath $toolsRoot -Force
    Remove-Item $zipPath -Force

    if (-not (Test-Path $nodeExe)) {
        throw "Node.js install failed: $nodeExe not found."
    }
    return $nodeHome
}

function Install-Python {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        Write-Host "Installing Python via winget..."
        & winget install -e --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
        return
    }

    $pyVersion = "3.12.7"
    $installerUrl = "https://www.python.org/ftp/python/$pyVersion/python-$pyVersion-amd64.exe"
    $tempInstaller = Join-Path $env:TEMP "python-$pyVersion-amd64.exe"
    Write-Host "Downloading Python $pyVersion installer..."
    Invoke-WebRequest -Uri $installerUrl -OutFile $tempInstaller
    Write-Host "Running Python installer..."
    Start-Process -FilePath $tempInstaller -ArgumentList "/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_test=0" -Wait
    Remove-Item $tempInstaller -Force
}

function Install-Rust {
    $rustupUrl = "https://win.rustup.rs/x86_64"
    $rustupExe = Join-Path $env:TEMP "rustup-init.exe"
    Write-Host "Downloading rustup..."
    Invoke-WebRequest -Uri $rustupUrl -OutFile $rustupExe
    Write-Host "Running rustup installer..."
    Start-Process -FilePath $rustupExe -ArgumentList "-y" -Wait
    Remove-Item $rustupExe -Force
}

# ==================== System dependencies ====================

Write-Section "System dependencies"

$requiredNodeVersion = [version]"20.19.0"
$nodeVersionTag = "v20.19.0"
$toolsRoot = Join-Path (Split-Path $script:Root -Parent) ".tools"
$nodeHome = Join-Path $toolsRoot "node-$nodeVersionTag-win-x64"

$nodeInfo = Get-NodeInfo $nodeHome
$nodeOk = $false
if ($nodeInfo) {
    $nodeVer = Get-NodeVersion $nodeInfo.Exe
    if ($nodeVer -and $nodeVer -ge $requiredNodeVersion) {
        $nodeOk = $true
        Add-SessionPath $nodeInfo.Home
        $env:NODE_HOME = $nodeInfo.Home
        Write-Host "Node.js found: v$nodeVer ($($nodeInfo.Source))"
    } else {
        Write-Host "Node.js found but version is too old or unknown."
    }
}

if (-not $nodeOk) {
    if (Prompt-YesNo "Node.js $nodeVersionTag not found. Install it to $nodeHome?") {
        try {
            $installedHome = Install-Node $nodeVersionTag $toolsRoot
            Add-SessionPath $installedHome
            $env:NODE_HOME = $installedHome
            Add-UserPath $installedHome
            $installed.Add("Node.js $nodeVersionTag")
            $nodeOk = $true
        } catch {
            Write-Host "Node.js install failed: $($_.Exception.Message)"
            $missing.Add("Node.js $nodeVersionTag (or newer)")
        }
    } else {
        $missing.Add("Node.js $nodeVersionTag (or newer)")
    }
}

$venvRoot = Join-Path $script:Root "python-backend\venv"
$venvPython = Join-Path $venvRoot "Scripts\python.exe"
$pythonOk = $true

if (-not (Test-Path $venvPython)) {
    $pythonOk = $false
    $pythonCmd = Get-PythonCommand
    if ($pythonCmd) {
        $pyVer = Get-PythonVersion $pythonCmd
        if ($pyVer -and $pyVer -ge [version]"3.10.0") {
            Write-Host "System Python found: v$pyVer"
            $pythonOk = $true
            Write-Host "Creating venv..."
            Invoke-PythonCommand $pythonCmd @("-m", "venv", $venvRoot)
        } else {
            Write-Host "System Python version is too old or unknown."
        }
    }

    if (-not $pythonOk) {
        if (Prompt-YesNo "Python 3.12 not found. Install it now?") {
            try {
                Install-Python
                $pythonCmd = Get-PythonCommand
                if ($pythonCmd) {
                    Write-Host "Creating venv..."
                    Invoke-PythonCommand $pythonCmd @("-m", "venv", $venvRoot)
                    $pythonOk = $true
                    $installed.Add("Python 3.12")
                } else {
                    $missing.Add("Python 3.12")
                }
            } catch {
                Write-Host "Python install failed: $($_.Exception.Message)"
                $missing.Add("Python 3.12")
            }
        } else {
            $missing.Add("Python 3.12")
        }
    }
}

$cargoCmd = Get-Command cargo -ErrorAction SilentlyContinue
$cargoPath = if ($cargoCmd) { $cargoCmd.Source } else { Join-Path $env:USERPROFILE ".cargo\bin\cargo.exe" }
$rustOk = Test-Path $cargoPath

if ($rustOk) {
    Add-SessionPath (Split-Path $cargoPath -Parent)
    Add-UserPath (Split-Path $cargoPath -Parent)
    Write-Host "Rust (cargo) found."
} else {
    if (Prompt-YesNo "Rust (cargo) not found. Install via rustup?") {
        try {
            Install-Rust
            $cargoPath = Join-Path $env:USERPROFILE ".cargo\bin\cargo.exe"
            if (Test-Path $cargoPath) {
                Add-SessionPath (Split-Path $cargoPath -Parent)
                Add-UserPath (Split-Path $cargoPath -Parent)
                $installed.Add("Rust toolchain")
                $rustOk = $true
            } else {
                $missing.Add("Rust toolchain (cargo)")
            }
        } catch {
            Write-Host "Rust install failed: $($_.Exception.Message)"
            $missing.Add("Rust toolchain (cargo)")
        }
    } else {
        $missing.Add("Rust toolchain (cargo)")
    }
}

# ==================== Project dependencies ====================

Write-Section "Project dependencies"

if ($nodeOk) {
    $nodeModules = Join-Path $script:Root "node_modules"
    if (-not (Test-Path $nodeModules)) {
        Write-Host "Installing npm dependencies..."
        npm install
    } else {
        Write-Host "node_modules already exists. Skipping npm install."
    }
} else {
    Write-Host "Skipping npm install (Node.js missing)."
}

if (Test-Path $venvPython) {
    Write-Host "Installing Python requirements..."
    & $venvPython -m pip install -r (Join-Path $script:Root "python-backend\requirements.txt")
} else {
    Write-Host "Skipping Python requirements (venv missing)."
}

Write-Section "Summary"
if ($installed.Count -gt 0) {
    Write-Host "Installed:"
    $installed | ForEach-Object { Write-Host " - $_" }
}
if ($missing.Count -gt 0) {
    Write-Host "Missing:"
    $missing | ForEach-Object { Write-Host " - $_" }
    Write-Host ""
    Write-Host "Please install missing items and re-run setup."
} else {
    Write-Host "All required dependencies are ready."
}
