<#
    VR Auto Launcher - per-user install for Windows (no administrator needed).

    Run it from the folder you unpacked the project into:

        powershell -ExecutionPolicy Bypass -File windows\install.ps1

    Options: -NoAutostart   do not start with Windows
             -NoStart       do not start the program at the end
#>
param(
    [switch]$NoAutostart,
    [switch]$NoStart
)

$ErrorActionPreference = "Stop"

$source    = Split-Path -Parent $PSScriptRoot
$target    = Join-Path $env:LOCALAPPDATA "VRAutoLauncher"
$startMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
$launcher  = Join-Path $target "vr-autolauncher.cmd"
$iconFile  = Join-Path $target "vr-autolauncher.ico"

# --- Python + PySide6 ---------------------------------------------------------

function Find-Python {
    foreach ($candidate in @("pythonw.exe", "python.exe")) {
        $found = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($found) { return $found.Source }
    }
    if (Get-Command "py.exe" -ErrorAction SilentlyContinue) {
        return (& py -3 -c "import sys; print(sys.executable)")
    }
    return $null
}

$python = Find-Python
if (-not $python) {
    Write-Host "Python 3 was not found. Install it from https://www.python.org/downloads/"
    Write-Host 'and tick "Add python.exe to PATH" during setup.'
    exit 1
}
# pythonw.exe runs without a console window; python.exe is the one that can install packages.
$pythonw = $python -replace "python\.exe$", "pythonw.exe"
$pythonc = $python -replace "pythonw\.exe$", "python.exe"
if (-not (Test-Path $pythonw)) { $pythonw = $python }
if (-not (Test-Path $pythonc)) { $pythonc = $python }

& $pythonc -c "import PySide6.QtWidgets" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing PySide6 (the graphical interface)..."
    & $pythonc -m pip install --user pyside6
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Could not install PySide6. Try it by hand: $pythonc -m pip install --user pyside6"
        exit 1
    }
}

# --- Stop a running copy, so the new code is picked up -------------------------

Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe' OR Name = 'python.exe'" |
    Where-Object { $_.CommandLine -and $_.CommandLine -like "*vr_autolauncher*" } |
    ForEach-Object {
        Write-Host "Stopping the running copy (pid $($_.ProcessId))..."
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
Start-Sleep -Seconds 1

# --- Copy the program ----------------------------------------------------------

if (Test-Path (Join-Path $target "vr_autolauncher")) {
    Remove-Item (Join-Path $target "vr_autolauncher") -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $target | Out-Null
Copy-Item (Join-Path $source "vr_autolauncher") $target -Recurse -Force
Get-ChildItem $target -Recurse -Directory -Filter "__pycache__" |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

@"
@echo off
rem Starts VR Auto Launcher from this folder.
set "PYTHONPATH=%~dp0"
start "" "$pythonw" -m vr_autolauncher %*
"@ | Set-Content -Path $launcher -Encoding ASCII

$env:PYTHONPATH = $target
& $pythonc -c "from vr_autolauncher import qt_ui; qt_ui.save_icon_file(r'$iconFile')" 2>$null

# --- Shortcuts -----------------------------------------------------------------

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut((Join-Path $startMenu "VR Auto Launcher.lnk"))
$shortcut.TargetPath = $pythonw
$shortcut.Arguments = "-m vr_autolauncher"
$shortcut.WorkingDirectory = $target
$shortcut.Description = "Starts VR companion apps when SteamVR and VRChat come up"
if (Test-Path $iconFile) { $shortcut.IconLocation = $iconFile }
$shortcut.Save()

# The program writes its own Startup entry, exactly as the switch in its settings does.
if (-not $NoAutostart) {
    & $pythonc -c "from vr_autolauncher import discovery; discovery.set_autostart(True)"
}

Write-Host ""
Write-Host "Installed to $target"
Write-Host "  start menu: VR Auto Launcher"
Write-Host "  config:     $env:LOCALAPPDATA\VRAutoLauncher\config.json (created on first run)"
if (-not $NoAutostart) { Write-Host "  autostart:  on (switch it off in the settings window)" }

if (-not $NoStart) {
    Start-Process -FilePath $pythonw -ArgumentList "-m vr_autolauncher" -WorkingDirectory $target
    Write-Host "Started."
}
