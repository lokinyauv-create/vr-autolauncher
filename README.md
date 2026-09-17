# VR Auto Launcher

A tray app for **Linux and Windows** that watches for SteamVR (and VRChat) and
automatically starts your VR companion apps once they're up — with a countdown
in the tray, no duplicates, and optional clean-up when VR closes.

Default setup on Linux:

| App | Starts when | Notes |
| --- | --- | --- |
| [OVR Advanced Settings](https://store.steampowered.com/app/1009850/) | SteamVR + VRChat, after 10 s | Steam version via `steam://rungameid/1009850` |
| [WayVR](https://github.com/wlx-team/wayvr) | SteamVR, after 8 s | your desktop inside the headset (`--openvr --show --replace`) |
| [VRCX](https://github.com/vrcx-team/VRCX) | VRChat | friends/world log companion |

Plus, for the whole SteamVR session:

- **Screen stays awake** — GNOME won't blank or lock the monitor while you're in the
  headset (otherwise desktop overlays end up showing the lock screen).
- **Pause processes** (`SIGSTOP`/`SIGCONT`), e.g. `linux-wallpaperengine`, and resume them afterwards.
- **Hooks** — any shell commands on VR start/stop (power profile, RGB, OBS, …).

On Windows the defaults match the original tool: OVR Advanced Settings plus
pausing/resuming Wallpaper Engine.

## Features

- **Settings window** (Linux): add programs from your Steam library, installed applications or
  any file/AppImage; choose what triggers each one (SteamVR, VRChat, both, or any running
  process, all/any of them) and the delay in seconds; test-launch before saving.
- Any number of apps, each with its own trigger processes, delay and command
  (a program, an AppImage glob like `~/Applications/WayVR-*.AppImage`, or a `steam://` URL).
- Skips a launch if the app is already running; optionally closes it again when VR stops,
  or restarts it if it crashes (up to 5 times per session).
- Per-app environment variables.
- Tray menu: status/countdown, settings, pause auto-launch, enable/disable each app,
  **Launch now**, open log.
- Start with the system: a switch in the settings window.
- The config file is re-read automatically when you save it.
- Desktop notifications on Linux (`notify-send`).
- Detects Proton games by their Windows exe name (`VRChat.exe` → `vrchat`).
- Single-instance guard on both platforms.
- UI in English or Ukrainian, following the system locale.

## Linux

Dependencies come from your distro (no pip needed):

```bash
# Fedora
sudo dnf install python3-gobject libappindicator-gtk3 python3-pillow
# Debian/Ubuntu
sudo apt install python3-gi gir1.2-ayatanaappindicator3-0.1 python3-pil
# Arch
sudo pacman -S python-gobject libayatana-appindicator python-pillow
```

On **GNOME** the tray icon needs the
[AppIndicator and KStatusNotifierItem Support](https://extensions.gnome.org/extension/615/appindicator-support/)
extension. KDE and most other desktops show it out of the box.

Install for your user (adds it to the app menu and to autostart, then starts it):

```bash
linux/install.sh                 # --no-autostart, --no-start
linux/uninstall.sh               # keeps config and logs
```

Opening **VR Auto Launcher** from the app menu (or running it again) shows the settings
window; autostart puts it in the tray only.

Or run it straight from the checkout:

```bash
./vr-autolauncher                # tray + settings window
./vr-autolauncher --background   # tray only
./vr-autolauncher --status       # what it sees right now, then exit
./vr-autolauncher --headless -v  # no tray, log to terminal
```

Files:

- Config: `~/.config/vr-autolauncher/config.json`
- Log: `~/.local/state/vr-autolauncher/VR-AutoLauncher.log`
- Output of launched apps: `~/.local/state/vr-autolauncher/apps/<name>.log`

Linux notes:

- OVR Advanced Settings from Steam is an AppImage, and the Steam runtime container has no
  `fusermount`. If it closes instantly, set its Steam launch options to
  `APPIMAGE_EXTRACT_AND_RUN=1 %command%`.
- Process names are matched against the process name, `argv[0]` and the executable path
  (never the arguments), so a script that merely mentions `wayvr` doesn't count as WayVR.

## Windows

Requires Python 3 with `pystray` and `pillow`:

```
pip install pystray pillow
python vr_autolauncher_tray.pyw
```

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

Config and log live in `%LocalAppData%\VRAutoLauncher\`.

## Configuration

Everything below can be changed in the settings window; the file is for Windows
(no settings window there yet) or if you prefer editing JSON.
`config.json` is created with the defaults above on first run. Example:

```json
{
  "check_interval": 2,
  "notifications": true,
  "apps": [
    {
      "name": "WayVR",
      "enabled": true,
      "when": ["vrserver"],
      "delay": 8,
      "command": "~/Applications/WayVR-*.AppImage --openvr --show --replace",
      "running": ["wayvr"],
      "trigger": "all",
      "close_on_exit": false,
      "keep_alive": false,
      "env": {}
    }
  ],
  "session": {
    "processes": ["vrserver"],
    "inhibit_idle": true,
    "pause_processes": ["linux-wallpaperengine"],
    "on_start": ["tuned-adm profile throughput-performance"],
    "on_stop": ["tuned-adm profile balanced"]
  }
}
```

| Key | Meaning |
| --- | --- |
| `when` | Process names that trigger the launch (`.exe` / `.AppImage` suffixes are ignored). |
| `trigger` | `"all"`: every `when` process must run; `"any"`: one is enough. |
| `delay` | Seconds to wait after that, so SteamVR/VRChat can settle. |
| `command` | Program and arguments, or a URL such as `steam://rungameid/<appid>`. `~`, `$VARS` and globs in the program path are expanded (newest match wins). |
| `running` | Substrings of a process name/path that mean "already running" — the launch is skipped. |
| `close_on_exit` | Terminate the app when the `when` processes go away (only if we started it). |
| `keep_alive` | Start it again if it exits while the trigger is still up (max 5 restarts). |
| `env` | Extra environment variables, e.g. `{"APPIMAGE_EXTRACT_AND_RUN": "1"}` (not for `steam://` links). |
| `paused` (top level) | Don't launch anything for now; the tray has a toggle for it. |
| `session.processes` | What counts as "in VR" for the session actions (default: `vrserver`). |
| `session.inhibit_idle` | Keep the screen from blanking/locking and the PC from suspending (Linux). |
| `session.pause_processes` | Processes to freeze during the session and resume afterwards (Linux). |
| `session.on_start` / `on_stop` | Shell commands run when the session starts/ends. |

More ideas for `apps`: OBS with the replay buffer
(`"command": "obs --startreplaybuffer --minimize-to-tray"`), a Discord client
(`"command": "flatpak run com.discordapp.Discord"`), or any other game/overlay by Steam app id.
