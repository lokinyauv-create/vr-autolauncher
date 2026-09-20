# -*- coding: utf-8 -*-
"""Things the settings window offers to pick from: Steam games, running processes,
plus autostart handling. No GUI code here."""

import os
import re
import shlex
import sys

from vr_autolauncher import APP_ID, osdeps

STEAM_ROOTS = [
    "~/.local/share/Steam",
    "~/.steam/steam",
    "~/.var/app/com.valvesoftware.Steam/.local/share/Steam",
] if not osdeps.IS_WINDOWS else [
    r"C:\Program Files (x86)\Steam",
    r"C:\Program Files\Steam",
    os.path.expandvars(r"%ProgramFiles(x86)%\Steam"),
]
# Tools that show up as "games" in the Steam library.
_STEAM_TOOLS = re.compile(r"^(Proton|Steam Linux Runtime|Steamworks Common|SteamVR Performance)", re.I)


class SteamGame:
    def __init__(self, appid, name, installdir, library):
        self.appid = appid
        self.name = name
        self.installdir = installdir
        self.path = os.path.join(library, "steamapps", "common", installdir)

    @property
    def command(self):
        return "steam://rungameid/%s" % self.appid


def _vdf_values(text, key):
    return re.findall(r'"%s"\s+"([^"]*)"' % re.escape(key), text, re.I)


def steam_games():
    libraries = []
    for root in STEAM_ROOTS:
        root = os.path.realpath(os.path.expanduser(root))
        vdf = os.path.join(root, "steamapps", "libraryfolders.vdf")
        if root in libraries or not os.path.exists(vdf):
            continue
        libraries.append(root)
        try:
            with open(vdf, encoding="utf-8", errors="replace") as f:
                libraries += [os.path.realpath(p) for p in _vdf_values(f.read(), "path")]
        except OSError:
            pass

    games = {}
    for library in dict.fromkeys(libraries):
        apps_dir = os.path.join(library, "steamapps")
        try:
            manifests = [n for n in os.listdir(apps_dir) if n.startswith("appmanifest_")]
        except OSError:
            continue
        for manifest in manifests:
            try:
                with open(os.path.join(apps_dir, manifest), encoding="utf-8", errors="replace") as f:
                    text = f.read()
                appid = _vdf_values(text, "appid")[0]
                name = _vdf_values(text, "name")[0]
                installdir = _vdf_values(text, "installdir")[0]
            except (OSError, IndexError):
                continue
            if not _STEAM_TOOLS.match(name):
                games[appid] = SteamGame(appid, name, installdir, library)
    return sorted(games.values(), key=lambda g: g.name.lower())


def running_process_names():
    """Sorted unique names of user-visible processes (kernel threads have no path)."""
    names = set()
    for proc in osdeps.list_processes():
        if proc.path.strip():
            # Skip retitled processes like "avahi-daemon: running [host]".
            names.update(n for n in proc.names if n and len(n) <= 40 and not re.search(r"[\s:\[]", n))
    # comm is cut at 15 chars; hide "gsd-a11y-settin" when "gsd-a11y-settings" is there.
    return sorted(n for n in names if not (len(n) == 15 and any(o != n and o.startswith(n) for o in names)))


class DesktopApp:
    def __init__(self, name, command, icon=None):
        self.name = name
        self.command = command
        self.icon = icon  # icon name/path (Linux) or the .lnk path (Windows)


def installed_apps():
    """Applications from the system menu, for the "pick an application" dialog."""
    return _installed_apps_windows() if osdeps.IS_WINDOWS else _installed_apps_linux()


def _installed_apps_linux():
    dirs = [os.path.join(d, "applications") for d in
            [os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")]
            + (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":")]
    lang = (os.environ.get("LC_ALL") or os.environ.get("LANG") or "")[:2]
    apps = {}
    for directory in dirs:
        try:
            entries = sorted(os.listdir(directory))
        except OSError:
            continue
        for entry in entries:
            if not entry.endswith(".desktop") or entry in apps:
                continue
            app = _parse_desktop_file(os.path.join(directory, entry), lang)
            if app:
                apps[entry] = app
    return sorted(apps.values(), key=lambda a: a.name.lower())


def _parse_desktop_file(path, lang):
    name = localized = exec_line = icon = ""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            in_entry = False
            for line in f:
                line = line.strip()
                if line.startswith("["):
                    if in_entry:
                        break  # only the first [Desktop Entry] section
                    in_entry = line == "[Desktop Entry]"
                    continue
                if not in_entry or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                if key == "Name":
                    name = value
                elif lang and key == "Name[%s]" % lang:
                    localized = value
                elif key == "Exec":
                    exec_line = value
                elif key == "Icon":
                    icon = value
                elif key in ("NoDisplay", "Hidden") and value.strip().lower() == "true":
                    return None
                elif key == "Type" and value.strip() != "Application":
                    return None
    except OSError:
        return None
    if not exec_line:
        return None
    return DesktopApp(localized or name or os.path.basename(path), clean_exec(exec_line), icon)


def _installed_apps_windows():
    menus = [os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs"),
             os.path.expandvars(r"%ProgramData%\Microsoft\Windows\Start Menu\Programs")]
    apps = {}
    for menu in menus:
        for root, _dirs, files in os.walk(menu):
            for filename in files:
                if not filename.lower().endswith(".lnk"):
                    continue
                name = os.path.splitext(filename)[0]
                path = os.path.join(root, filename)
                # Shortcuts are launched as-is; osdeps.spawn hands .lnk to the shell.
                apps.setdefault(name, DesktopApp(name, '"%s"' % path, path))
    return sorted(apps.values(), key=lambda a: a.name.lower())


_EXEC_FIELD_CODES = re.compile(r"\s%[fFuUdDnNickvm]")


def clean_exec(exec_line):
    """Strip .desktop field codes like %U."""
    return _EXEC_FIELD_CODES.sub("", " " + exec_line).strip()


def suggest_running(command):
    """Best guess for the "already running" pattern of a command."""
    command = command.strip()
    if not command:
        return ""
    m = re.match(r"steam://(?:rungameid|launch|run)/(\d+)", command)
    if m:
        game = next((g for g in steam_games() if g.appid == m.group(1)), None)
        return game.installdir.lower() if game else ""
    try:
        argv = shlex.split(command)
    except ValueError:
        argv = command.split()
    if not argv:
        return ""
    if os.path.basename(argv[0]) == "flatpak":
        app_ids = [a for a in argv[1:] if not a.startswith("-") and a != "run" and "." in a]
        if app_ids:
            return app_ids[0].rsplit(".", 1)[-1].lower()  # com.discordapp.Discord -> discord
    if os.path.basename(argv[0]) == "env":
        argv = [a for a in argv[1:] if "=" not in a] or argv
    name = osdeps.normalize_name(argv[0])
    # WayVR-v26.8.0-x86_64 -> wayvr, VRCX-2026.01 -> vrcx
    return re.split(r"[-_ ]v?\d", name, maxsplit=1)[0]


# --- Autostart (XDG autostart entry / Windows Startup folder) --------------------

def autostart_file():
    if osdeps.IS_WINDOWS:
        return os.path.expandvars(
            r"%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\VR Auto Launcher.cmd")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "autostart", APP_ID + ".desktop")


def autostart_enabled():
    return os.path.exists(autostart_file())


def launcher_command():
    """How to start this very copy of the program (installed wrapper or source tree)."""
    installed = os.path.expanduser("~/.local/bin/" + APP_ID)
    package_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.path.exists(installed) and package_parent == os.path.join(
            os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), APP_ID):
        return '"%s"' % installed
    # .desktop Exec only understands double quotes.
    return 'env "PYTHONPATH=%s" "%s" -m vr_autolauncher' % (package_parent, sys.executable)


def set_autostart(enabled):
    path = autostart_file()
    if not enabled:
        if os.path.exists(path):
            os.remove(path)
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if osdeps.IS_WINDOWS:
        # pythonw.exe keeps it windowless; "start" lets the shell exit immediately.
        runner = sys.executable.replace("python.exe", "pythonw.exe")
        with open(path, "w", encoding="utf-8") as f:
            f.write('@echo off\r\nstart "" "%s" -m vr_autolauncher --background\r\n' % runner)
        return
    with open(path, "w", encoding="utf-8") as f:
        f.write("[Desktop Entry]\n"
                "Type=Application\n"
                "Name=VR Auto Launcher\n"
                "Exec=%s --background\n"
                "Icon=vr-autolauncher\n"
                "Terminal=false\n"
                "NoDisplay=true\n"
                "X-GNOME-Autostart-Delay=5\n" % launcher_command())
