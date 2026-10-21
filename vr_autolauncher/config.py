# -*- coding: utf-8 -*-
"""User config: a JSON file created with sensible defaults on first run.

Linux:   ~/.config/vr-autolauncher/config.json
Windows: %LocalAppData%\\VRAutoLauncher\\config.json
"""

import copy
import json
import logging
import os

from vr_autolauncher import osdeps

CONFIG_FILE = os.path.join(osdeps.config_dir(), "config.json")

# Every app entry understands these keys; missing ones fall back to this.
APP_DEFAULTS = {
    "name": "",
    "enabled": True,
    # Processes that trigger the launch...
    "when": ["vrserver"],
    # ...all of them ("all") or at least one ("any").
    "trigger": "all",
    # Seconds to wait after "when" became true (lets SteamVR/VRChat settle).
    "delay": 5,
    # Program + args, or a URL such as steam://rungameid/<appid>.
    "command": "",
    # Substrings of a process name/executable path meaning "already running":
    # the launch is skipped instead of starting a duplicate.
    "running": [],
    # Close the app again when the "when" processes go away.
    "close_on_exit": False,
    # ...after waiting this many seconds (0: at once).
    "close_delay": 0,
    # Start it again if it exits/crashes while the trigger is still up.
    "keep_alive": False,
    # Extra environment variables for the launched program.
    "env": {},
}

# Shortcuts shown in the settings window.
TRIGGER_PRESETS = {
    "steamvr": ["vrserver"],
    "vrchat": ["vrchat"],
    "steamvr+vrchat": ["vrserver", "vrchat"],
}

LINUX_DEFAULTS = {
    "check_interval": 2,
    "notifications": True,
    # Temporarily stop launching anything (session actions still run).
    "paused": False,
    "apps": [
        {
            "name": "OVR Advanced Settings",
            "when": ["vrserver", "vrchat"],
            "delay": 10,
            # Steam version; its launch options need APPIMAGE_EXTRACT_AND_RUN=1 %command%
            # because the Steam runtime container has no fusermount.
            "command": "steam://rungameid/1009850",
            "running": ["advancedsettings", "advanced_settings"],
        },
        {
            "name": "WayVR",
            # vrcompositor appears once SteamVR is really up: starting earlier makes
            # WayVR fail with "Will not use OpenVR: Context init failed".
            "when": ["vrcompositor"],
            "delay": 15,
            "command": "~/Applications/WayVR-*.AppImage --openvr --show --replace",
            "running": ["wayvr"],
            "keep_alive": True,
        },
        {
            "name": "VRCX",
            "when": ["vrchat"],
            "delay": 0,
            "command": "~/Applications/VRCX*.AppImage --ozone-platform-hint=auto",
            "running": ["vrcx"],
        },
    ],
    # Things done for the whole SteamVR session (vrserver up), independent of VRChat.
    "session": {
        "processes": ["vrserver"],
        # Keep GNOME from blanking/locking the screen while you're in the headset.
        "inhibit_idle": True,
        # Freeze these while in VR and unfreeze afterwards (SIGSTOP/SIGCONT),
        # e.g. "linux-wallpaperengine". Matched like "running" above.
        "pause_processes": [],
        # Shell commands, e.g. "tuned-adm profile throughput-performance".
        "on_start": [],
        "on_stop": [],
    },
}

WINDOWS_DEFAULTS = {
    "check_interval": 5,
    "notifications": False,
    "paused": False,
    "apps": [
        {
            "name": "OVR Advanced Settings",
            "when": ["vrserver", "vrchat"],
            "delay": 10,
            "command": "steam://launch/1009850",
            "running": ["advancedsettings"],
        },
    ],
    "session": {
        "processes": ["vrserver"],
        "inhibit_idle": False,
        "pause_processes": [],
        "on_start": [r'"C:\Program Files (x86)\Steam\steamapps\common\wallpaper_engine\wallpaper64.exe" -control pause'],
        "on_stop": [r'"C:\Program Files (x86)\Steam\steamapps\common\wallpaper_engine\wallpaper64.exe" -control play'],
    },
}


def defaults():
    return copy.deepcopy(WINDOWS_DEFAULTS if osdeps.IS_WINDOWS else LINUX_DEFAULTS)


def _normalize(cfg):
    base = defaults()
    out = {
        "check_interval": max(1, float(cfg.get("check_interval", base["check_interval"]))),
        "notifications": bool(cfg.get("notifications", base["notifications"])),
        "paused": bool(cfg.get("paused", False)),
        "apps": [],
        "session": dict(base["session"], **cfg.get("session", {})),
    }
    out["apps"] = [normalize_app(raw) for raw in cfg.get("apps", [])]
    session = out["session"]
    session["processes"] = [osdeps.normalize_name(p) for p in session["processes"]]
    session["pause_processes"] = [s.lower() for s in session["pause_processes"]]
    return out


def normalize_app(raw):
    app = dict(APP_DEFAULTS, **raw)
    if not app["name"]:
        app["name"] = app["command"].split(None, 1)[0] if app["command"] else "?"
    app["when"] = [osdeps.normalize_name(p) for p in app["when"] if p.strip()]
    app["running"] = [s.strip().lower() for s in app["running"] if s.strip()]
    app["trigger"] = "any" if app["trigger"] == "any" else "all"
    app["delay"] = max(0.0, float(app["delay"]))
    app["close_delay"] = max(0.0, float(app["close_delay"]))
    app["env"] = {str(k): str(v) for k, v in dict(app["env"] or {}).items()}
    return app


def load(path=CONFIG_FILE):
    """Read the config, writing the defaults first if there is none.

    A broken file is logged and replaced by defaults in memory only, so a
    typo never silently wipes the user's edits.
    """
    if not os.path.exists(path):
        save(defaults(), path)
    try:
        with open(path, encoding="utf-8") as f:
            return _normalize(json.load(f))
    except (OSError, ValueError, TypeError, AttributeError) as e:
        logging.error("Config %s is invalid (%s); using defaults", path, e)
        return _normalize(defaults())


def load_raw(path=CONFIG_FILE):
    """The file as written (no defaults merged in), for editing and saving back."""
    if not os.path.exists(path):
        save(defaults(), path)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def set_app_enabled(name, enabled, path=CONFIG_FILE):
    """Flip one app's "enabled" flag in the file, keeping everything else as written."""
    raw = load_raw(path)
    for app in raw.get("apps", []):
        if app.get("name") == name:
            app["enabled"] = enabled
    save(raw, path)


def set_paused(paused, path=CONFIG_FILE):
    raw = load_raw(path)
    raw["paused"] = paused
    save(raw, path)


def save(cfg, path=CONFIG_FILE):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)
