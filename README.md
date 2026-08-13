# VR Auto Launcher

A Windows tray app that watches for SteamVR + VRChat and automatically launches
[OVR Advanced Settings](https://store.steampowered.com/app/1009850/OVR_Advanced_Settings/)
once both are up. It also pauses [Wallpaper Engine](https://www.wallpaperengine.io/)
while SteamVR is running (independently of VRChat) and resumes it when SteamVR closes.

## Features

- Auto-launches OVR Advanced Settings once SteamVR + VRChat are both detected, with a
  10-second countdown shown in the tray icon.
- Pauses/resumes Wallpaper Engine based on SteamVR alone.
- Single-instance guard (Windows can replay the Startup folder on `explorer.exe`
  restarts, not just at logon — this prevents duplicate copies from piling up).
- UI language auto-detects from Windows' locale: defaults to English, switches to
  Ukrainian automatically when the system locale is Ukrainian (`uk-*`). No manual
  toggle — it just follows the OS.
- Logs to `%LocalAppData%\VRAutoLauncher\VR-AutoLauncher.log`.

## Running from source

Requires Python 3 with `pystray` and `pillow`:

```
pip install pystray pillow
python vr_autolauncher_tray.pyw
```

## Building

Standalone exe via [PyInstaller](https://pyinstaller.org/):

```
pip install pyinstaller
pyinstaller --onefile --windowed --name VRAutoLauncher vr_autolauncher_tray.pyw
```

Installer via [NSIS](https://nsis.sourceforge.io/) (per-user install, no admin
required, sets up a Startup shortcut and an uninstaller):

```
makensis installer.nsi
```

## Configuration

Edit the constants near the top of `vr_autolauncher_tray.pyw`:

- `TARGET_APP` — Steam launch URL for the app to auto-start (default: OVR Advanced
  Settings, Steam app ID `1009850`).
- `WATCH_PROCESSES` — process names to wait for (default: `vrserver`, `vrchat`).
- `WALLPAPER_ENGINE_EXE` — path to `wallpaper64.exe`.
