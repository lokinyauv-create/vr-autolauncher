# -*- coding: utf-8 -*-
"""Everything that differs between Windows and Linux lives here."""

import glob
import os
import shlex
import shutil
import signal
import subprocess
import sys

from vr_autolauncher import APP_ID

IS_WINDOWS = sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# --- Paths -------------------------------------------------------------------

def config_dir():
    if IS_WINDOWS:
        return os.path.join(os.environ["LOCALAPPDATA"], "VRAutoLauncher")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, APP_ID)


def state_dir():
    """Logs go here (XDG_STATE_HOME on Linux, same folder as config on Windows)."""
    if IS_WINDOWS:
        return config_dir()
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return os.path.join(base, APP_ID)


def runtime_dir():
    """Per-session scratch space: lock file, generated tray icons."""
    if IS_WINDOWS:
        path = os.path.join(config_dir(), "runtime")
    else:
        base = os.environ.get("XDG_RUNTIME_DIR") or os.path.join("/tmp", "%s-%d" % (APP_ID, os.getuid()))
        path = os.path.join(base, APP_ID)
    os.makedirs(path, exist_ok=True)
    return path


# --- Language ----------------------------------------------------------------

def detect_language():
    """English by default; Ukrainian only if the OS locale is Ukrainian."""
    if IS_WINDOWS:
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(85)
            ctypes.windll.kernel32.GetUserDefaultLocaleName(buf, 85)
            return "uk" if buf.value.lower().startswith("uk") else "en"
        except Exception:
            return "en"
    for var in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        value = os.environ.get(var)
        if value:
            return "uk" if value.lower().startswith("uk") else "en"
    return "en"


# --- Single instance ---------------------------------------------------------

_instance_handle = None


def acquire_single_instance():
    """Return False if another copy is already running.

    Windows can replay the Startup folder on explorer.exe restarts, and GNOME
    can start autostart entries twice after a shell crash, so both need this.
    """
    global _instance_handle
    if IS_WINDOWS:
        import ctypes
        ERROR_ALREADY_EXISTS = 183
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _instance_handle = kernel32.CreateMutexW(None, False, "Global\\VRAutoLauncherSingleInstance")
        return ctypes.get_last_error() != ERROR_ALREADY_EXISTS

    import fcntl
    # "a" so a losing second copy doesn't wipe the pid the installer reads.
    _instance_handle = open(os.path.join(runtime_dir(), "instance.lock"), "a")
    try:
        fcntl.flock(_instance_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    _instance_handle.truncate(0)
    _instance_handle.write(str(os.getpid()))
    _instance_handle.flush()
    return True


def signal_running_instance():
    """Ask the already-running copy to open its settings window (Linux: SIGUSR1)."""
    if IS_WINDOWS:
        return False
    try:
        with open(os.path.join(runtime_dir(), "instance.lock")) as f:
            os.kill(int(f.read().strip()), signal.SIGUSR1)
        return True
    except (OSError, ValueError):
        return False


def hide_console():
    if not IS_WINDOWS:
        return
    import ctypes
    hwnd = ctypes.windll.kernel32.GetConsoleWindow()
    if hwnd:
        ctypes.windll.user32.ShowWindow(hwnd, 0)
        ctypes.windll.user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0080 | 0x0001 | 0x0002)


# --- Processes ---------------------------------------------------------------

class Proc:
    __slots__ = ("pid", "names", "path")

    def __init__(self, pid, names, path):
        self.pid = pid
        self.names = names  # normalized short names, e.g. {"vrchat"}
        # Lowercased argv[0] + executable path. Arguments are deliberately left
        # out: a shell script or editor mentioning "wayvr" must not count as WayVR.
        self.path = path


def normalize_name(name):
    """'Z:\\...\\VRChat.exe' / '/opt/x/vrserver' / 'VRCX.AppImage' -> 'vrchat' / 'vrserver' / 'vrcx'."""
    name = name.replace("\\", "/").rsplit("/", 1)[-1].strip().lower()
    for suffix in (".exe", ".appimage"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name


def list_processes():
    return _list_processes_windows() if IS_WINDOWS else _list_processes_linux()


def _list_processes_linux():
    own = os.getpid()
    procs = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit() or int(entry) == own:
            continue
        try:
            with open("/proc/%s/comm" % entry, "rb") as f:
                comm = f.read().decode("utf-8", "replace").strip()
            with open("/proc/%s/cmdline" % entry, "rb") as f:
                argv0 = f.read().split(b"\0", 1)[0].decode("utf-8", "replace")
        except OSError:
            continue  # exited meanwhile, or not ours to read
        # Chromium/Electron rewrite argv into one space-separated string.
        argv0 = argv0.split(" --", 1)[0]
        try:
            exe = os.readlink("/proc/%s/exe" % entry)
        except OSError:
            exe = ""
        names = {normalize_name(comm)}
        # Wine/Proton sets argv[0] to the Windows path (e.g. "S:\common\VRChat\VRChat.exe"),
        # and comm is truncated to 15 chars, so look at argv[0] and the exe as well.
        for path in (argv0, exe):
            if path:
                names.add(normalize_name(path))
        procs.append(Proc(int(entry), names, (argv0 + " " + exe).lower()))
    return procs


def _list_processes_windows():
    try:
        out = subprocess.check_output(
            ["tasklist", "/fo", "csv", "/nh"],
            stderr=subprocess.DEVNULL,
            creationflags=_NO_WINDOW,
        ).decode("cp866", errors="ignore")
    except Exception:
        return []
    procs = []
    for line in out.splitlines():
        parts = line.split('","')
        if len(parts) < 2:
            continue
        image = parts[0].strip('"')
        try:
            pid = int(parts[1].strip('"'))
        except ValueError:
            pid = 0
        procs.append(Proc(pid, {normalize_name(image)}, image.lower()))
    return procs


def terminate(pid):
    try:
        if IS_WINDOWS:
            subprocess.call(["taskkill", "/pid", str(pid)], creationflags=_NO_WINDOW,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            os.kill(pid, signal.SIGTERM)
        return True
    except Exception:
        return False


def suspend(pid, paused):
    """Freeze / thaw a process (Linux only: SIGSTOP / SIGCONT)."""
    if IS_WINDOWS:
        return False
    try:
        os.kill(pid, signal.SIGSTOP if paused else signal.SIGCONT)
        return True
    except OSError:
        return False


# --- Launching ---------------------------------------------------------------

def expand_command(command):
    """Turn a config command string into argv.

    ~ and $VARS are expanded; if the program path contains a glob pattern
    (e.g. "~/Applications/WayVR-*.AppImage") the newest match wins, so the
    config survives AppImage version bumps.
    Returns None if the program can't be found.
    """
    if IS_WINDOWS:
        argv = [os.path.expandvars(a) for a in shlex.split(command, posix=False)]
        argv = [a[1:-1] if len(a) > 1 and a[0] == a[-1] == '"' else a for a in argv]
    else:
        argv = shlex.split(command)
        argv = [os.path.expandvars(os.path.expanduser(a)) for a in argv]
    if not argv:
        return None
    program = argv[0]
    if any(ch in program for ch in "*?["):
        matches = sorted(glob.glob(program), key=os.path.getmtime, reverse=True)
        if not matches:
            return None
        program = matches[0]
    if os.sep in program or (IS_WINDOWS and "/" in program):
        if not os.path.exists(program):
            return None
    elif shutil.which(program) is None:
        return None
    argv[0] = program
    return argv


def is_url(command):
    return "://" in command.split(None, 1)[0] if command.strip() else False


def open_url(url, log_file=None, env=None):
    """steam://... and friends."""
    if IS_WINDOWS:
        subprocess.Popen(["cmd", "/c", "start", "", url], creationflags=_NO_WINDOW)
        return None
    # Steam hands the URL to its already-running client, so env vars can't reach the game;
    # set those in the game's Steam launch options instead.
    if url.startswith("steam://"):
        if shutil.which("steam"):
            argv = ["steam", url]
        elif _flatpak_has("com.valvesoftware.Steam"):
            argv = ["flatpak", "run", "com.valvesoftware.Steam", url]
        else:
            argv = ["xdg-open", url]
    else:
        argv = ["xdg-open", url]
    return spawn(argv, log_file, env)


def _flatpak_has(app_id):
    if not shutil.which("flatpak"):
        return False
    return subprocess.call(["flatpak", "info", app_id],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0


def spawn(argv, log_file=None, env=None):
    """Start a program fully detached from us; returns the Popen."""
    full_env = dict(os.environ, **env) if env else None
    out = subprocess.DEVNULL
    if log_file:
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        out = open(log_file, "ab")
    try:
        if IS_WINDOWS:
            return subprocess.Popen(argv, stdout=out, stderr=out, stdin=subprocess.DEVNULL,
                                    env=full_env, creationflags=_NO_WINDOW)
        return subprocess.Popen(argv, stdout=out, stderr=out, stdin=subprocess.DEVNULL,
                                env=full_env, start_new_session=True)
    finally:
        if out is not subprocess.DEVNULL:
            out.close()


def run_hook(command):
    """Fire-and-forget shell command from the config (on_start / on_stop)."""
    if IS_WINDOWS:
        return subprocess.Popen(command, shell=True, creationflags=_NO_WINDOW)
    return subprocess.Popen(["/bin/sh", "-c", command], start_new_session=True,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def open_path(path):
    if IS_WINDOWS:
        os.startfile(path)
    else:
        subprocess.Popen(["xdg-open", path], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def notify(title, body, icon=None):
    if IS_WINDOWS or not shutil.which("notify-send"):
        return
    argv = ["notify-send", "--app-name", "VR Auto Launcher", "--expire-time", "4000"]
    if icon:
        argv += ["--icon", icon]
    subprocess.Popen(argv + [title, body], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# --- Keeping the desktop awake -----------------------------------------------

class IdleInhibitor:
    """Stops the screen from blanking/locking while you're in the headset.

    On GNOME a locked screen also means WayVR/desktop overlays show the lock
    screen instead of your desktop, and the mouse is idle the whole session.
    """

    def __init__(self):
        self._proc = None

    def start(self, reason):
        if IS_WINDOWS or self._proc is not None:
            return False
        if shutil.which("gnome-session-inhibit") and os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
            argv = ["gnome-session-inhibit", "--app-id", APP_ID, "--reason", reason,
                    "--inhibit", "idle:suspend", "--inhibit-only"]
        elif shutil.which("systemd-inhibit"):
            argv = ["systemd-inhibit", "--what=idle:sleep", "--who=VR Auto Launcher",
                    "--why=" + reason, "--mode=block", "sleep", "infinity"]
        else:
            return False
        self._proc = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True

    def stop(self):
        if self._proc is None:
            return
        self._proc.terminate()
        try:
            self._proc.wait(3)
        except subprocess.TimeoutExpired:
            self._proc.kill()
        self._proc = None
