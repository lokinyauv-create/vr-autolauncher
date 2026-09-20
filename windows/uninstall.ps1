<#
    Removes VR Auto Launcher; keeps your settings and log.

        powershell -ExecutionPolicy Bypass -File windows\uninstall.ps1
#>
$ErrorActionPreference = "Stop"

$target    = Join-Path $env:LOCALAPPDATA "VRAutoLauncher"
$startMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\VR Auto Launcher.lnk"
$startup   = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup\VR Auto Launcher.cmd"

Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe' OR Name = 'python.exe'" |
    Where-Object { $_.CommandLine -and $_.CommandLine -like "*vr_autolauncher*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

foreach ($path in @($startMenu, $startup)) {
    if (Test-Path $path) { Remove-Item $path -Force }
}
foreach ($name in @("vr_autolauncher", "vr-autolauncher.cmd", "vr-autolauncher.ico")) {
    $path = Join-Path $target $name
    if (Test-Path $path) { Remove-Item $path -Recurse -Force }
}

Write-Host "Uninstalled. Settings and log kept in $target"
