# -*- coding: utf-8 -*-
"""
VR Auto Launcher with tray icon
Requirements: pip install pystray pillow
"""

import threading
import ctypes
import ctypes.wintypes

# Hide console window on both Windows 10 and 11
def hide_console():
    hwnd = ctypes.windll.kernel32.GetConsoleWindow()
    if hwnd:
        ctypes.windll.user32.ShowWindow(hwnd, 0)
        ctypes.windll.user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0080 | 0x0001 | 0x0002)

hide_console()


import sys
import subprocess
import time
import os
import logging
from PIL import Image, ImageDraw, ImageFont
import pystray._win32
import pystray
from pystray import MenuItem as item

# --- CONFIG ---
TARGET_APP = "steam://launch/1009850"
TARGET_ARGS = []
DELAY_SECONDS = 10
CHECK_INTERVAL = 5
LOOP_MODE = True
WATCH_PROCESSES = ["vrserver", "vrchat"]
LOG_DIR = os.path.join(os.environ["LOCALAPPDATA"], "VRAutoLauncher")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "VR-AutoLauncher.log")
WALLPAPER_ENGINE_EXE = r"C:\Program Files (x86)\Steam\steamapps\common\wallpaper_engine\wallpaper64.exe"

# --- States ---
ST_WAITING  = "waiting"
ST_DETECTED = "detected"
ST_LAUNCHED = "launched"
ST_CLOSED   = "vr_closed"

COLORS = {
    ST_WAITING:  "#4a90d9",
    ST_DETECTED: "#f5a623",
    ST_LAUNCHED: "#27ae60",
    ST_CLOSED:   "#e74c3c",
}


def detect_language():
    """English by default; Ukrainian only if Windows' own UI/locale is Ukrainian."""
    try:
        buf = ctypes.create_unicode_buffer(85)
        ctypes.windll.kernel32.GetUserDefaultLocaleName(buf, 85)
        return "uk" if buf.value.lower().startswith("uk") else "en"
    except Exception:
        return "en"


LANG = detect_language()

LABELS_EN = {
    ST_WAITING:  "Waiting: SteamVR + VRChat...",
    ST_DETECTED: "VR detected! Launching in {}s...",
    ST_LAUNCHED: "OpenVR Advanced Settings launched",
    ST_CLOSED:   "VR closed. Waiting next session...",
}

LABELS_UK = {
    ST_WAITING:  "Очікування: SteamVR + VRChat...",
    ST_DETECTED: "VR виявлено! Запуск через {}с...",
    ST_LAUNCHED: "OpenVR Advanced Settings запущено",
    ST_CLOSED:   "VR закрито. Очікування наступної сесії...",
}

MENU_LABELS = {
    "en": {"open_log": "Open log", "quit": "Quit"},
    "uk": {"open_log": "Відкрити лог", "quit": "Вийти"},
}

LABELS = LABELS_UK if LANG == "uk" else LABELS_EN
MENU = MENU_LABELS[LANG]

current_state   = ST_WAITING
delay_remaining = 0
tray_icon       = None
stop_event      = threading.Event()
_instance_mutex = None


def ensure_single_instance():
    """Exit immediately if another copy is already running.

    Without this, every explorer.exe restart (crash/GPU driver reset, not
    just logon) replays the Startup folder and spawns a duplicate instance
    on top of the still-running one.
    """
    global _instance_mutex
    ERROR_ALREADY_EXISTS = 183
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _instance_mutex = kernel32.CreateMutexW(None, False, "Global\\VRAutoLauncherSingleInstance")
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        sys.exit(0)


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8")],
    )


def log(msg, level="info"):
    getattr(logging, level)(msg)


def make_icon(color, letter="VR"):
    size = 64
    img  = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([2, 2, size - 2, size - 2], fill=color)
    font = None
    for fp in [r"C:\Windows\Fonts\arialbd.ttf", r"C:\Windows\Fonts\arial.ttf"]:
        if os.path.exists(fp):
            try:
                font = ImageFont.truetype(fp, 22)
                break
            except Exception:
                pass
    if font is None:
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), letter, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    draw.text(
        ((size - tw) / 2 - bbox[0], (size - th) / 2 - bbox[1]),
        letter, fill="white", font=font
    )
    return img


def get_status():
    if current_state == ST_DETECTED:
        return LABELS[ST_DETECTED].format(delay_remaining)
    return LABELS.get(current_state, "")


def update_tray():
    global tray_icon
    if tray_icon is None:
        return
    letter_map = {ST_WAITING: "VR", ST_DETECTED: "VR", ST_LAUNCHED: "OK", ST_CLOSED: "--"}
    color  = COLORS.get(current_state, "#4a90d9")
    letter = letter_map.get(current_state, "VR")
    tray_icon.icon  = make_icon(color, letter)
    tray_icon.title = "VR Auto Launcher\n" + get_status()
    tray_icon.update_menu()


def set_state(state, delay=0):
    global current_state, delay_remaining
    current_state   = state
    delay_remaining = delay
    log(get_status())
    update_tray()


def get_procs():
    try:
        out = subprocess.check_output(
            ["tasklist", "/fo", "csv", "/nh"],
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        ).decode("cp866", errors="ignore")
        return {
            line.split(",")[0].strip('"').lower().replace(".exe", "")
            for line in out.splitlines() if line
        }
    except Exception:
        return set()


def all_vr_up():
    procs = get_procs()
    return all(p.lower() in procs for p in WATCH_PROCESSES)


def any_vr_up():
    procs = get_procs()
    return any(p.lower() in procs for p in WATCH_PROCESSES)


def launch_target():
    try:
        # Launch via Steam URL so settings are loaded correctly
        subprocess.Popen(["cmd", "/c", "start", "", TARGET_APP],
                         creationflags=subprocess.CREATE_NO_WINDOW)
        log("Launched via Steam: " + TARGET_APP)
    except Exception as e:
        log("Launch error: " + str(e), "error")


def set_wallpaper_engine(paused):
    if not os.path.exists(WALLPAPER_ENGINE_EXE):
        return
    try:
        subprocess.Popen(
            [WALLPAPER_ENGINE_EXE, "-control", "pause" if paused else "play"],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        log("Wallpaper Engine " + ("paused" if paused else "resumed"))
    except Exception as e:
        log("Wallpaper Engine control error: " + str(e), "error")


def monitor_loop():
    global delay_remaining
    iteration = 0
    while not stop_event.is_set():
        iteration += 1
        set_state(ST_WAITING)
        while not all_vr_up():
            if stop_event.is_set():
                return
            time.sleep(CHECK_INTERVAL)

        for remaining in range(DELAY_SECONDS, 0, -1):
            set_state(ST_DETECTED, remaining)
            if stop_event.is_set():
                return
            time.sleep(1)

        launch_target()
        set_state(ST_LAUNCHED)

        if not LOOP_MODE:
            return

        while any_vr_up():
            if stop_event.is_set():
                return
            time.sleep(CHECK_INTERVAL)

        set_state(ST_CLOSED)
        time.sleep(2)


def wallpaper_watch_loop():
    """Pause/resume Wallpaper Engine based on SteamVR alone, independent of
    the VRChat auto-launch flow above (should react to SteamVR itself, not
    wait for VRChat to be running too)."""
    steamvr_was_up = False
    while not stop_event.is_set():
        steamvr_up = "vrserver" in get_procs()
        if steamvr_up and not steamvr_was_up:
            set_wallpaper_engine(paused=True)
        elif not steamvr_up and steamvr_was_up:
            set_wallpaper_engine(paused=False)
        steamvr_was_up = steamvr_up
        if stop_event.wait(CHECK_INTERVAL):
            return


def on_open_log(icon, item):
    if os.path.exists(LOG_FILE):
        os.startfile(LOG_FILE)


def on_quit(icon, item):
    stop_event.set()
    icon.stop()


def build_menu():
    return pystray.Menu(
        item(lambda text: get_status(), lambda: None, enabled=False),
        pystray.Menu.SEPARATOR,
        item(MENU["open_log"], on_open_log),
        pystray.Menu.SEPARATOR,
        item(MENU["quit"], on_quit),
    )


def main():
    global tray_icon
    ensure_single_instance()
    setup_logging()
    log("VR Auto Launcher started")

    t = threading.Thread(target=monitor_loop, daemon=True)
    t.start()

    w = threading.Thread(target=wallpaper_watch_loop, daemon=True)
    w.start()

    tray_icon = pystray._win32.Icon(
        name="VRAutoLauncher",
        icon=make_icon(COLORS[ST_WAITING], "VR"),
        title="VR Auto Launcher\n" + LABELS[ST_WAITING],
        menu=build_menu(),
    )
    tray_icon.run()


if __name__ == "__main__":
    main()
