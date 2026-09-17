# -*- coding: utf-8 -*-
import argparse
import logging
import logging.handlers
import os
import signal
import sys
import threading

from vr_autolauncher import APP_NAME, __version__, config, osdeps
from vr_autolauncher.engine import Engine
from vr_autolauncher.i18n import t

LOG_FILE = os.path.join(osdeps.state_dir(), "VR-AutoLauncher.log")


def setup_logging(verbose):
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    handlers = [logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=2,
                                                     encoding="utf-8")]
    if verbose or (sys.stderr is not None and sys.stderr.isatty()):
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S", handlers=handlers)


def print_status(config_path):
    """--status: what the watcher would see right now."""
    cfg = config.load(config_path)
    procs = osdeps.list_processes()
    names = set().union(*(p.names for p in procs)) if procs else set()

    def mark(ok):
        return "✓" if ok else "·"

    print("%s %s\nconfig: %s\nlog:    %s\n" % (APP_NAME, __version__, config_path, LOG_FILE))
    session = cfg["session"]["processes"]
    print("session %s: %s" % (mark(all(p in names for p in session)), ", ".join(session)))
    for app in cfg["apps"]:
        when = ", ".join("%s %s" % (mark(p in names), p) for p in app["when"])
        running = any(any(s in p.names or s in p.path for s in app["running"]) for p in procs) \
            if app["running"] else False
        cmd = app["command"]
        found = osdeps.is_url(cmd) or osdeps.expand_command(cmd) is not None
        print("\n[%s] %s" % ("on " if app["enabled"] else "off", app["name"]))
        print("  when:    %s (delay %ss)" % (when, app["delay"]))
        print("  command: %s%s" % (cmd, "" if found else "   <-- NOT FOUND"))
        print("  running: %s" % ("yes" if running else "no"))


def pick_tray(engine, headless):
    if headless:
        return None
    if osdeps.IS_LINUX:
        try:
            from vr_autolauncher.tray_linux import LinuxTray
            return LinuxTray(engine, LOG_FILE)
        except (ImportError, ValueError) as e:
            logging.warning("AppIndicator tray unavailable (%s), trying pystray", e)
    try:
        from vr_autolauncher.tray_pystray import PystrayTray
        return PystrayTray(engine, LOG_FILE)
    except Exception as e:
        logging.warning("No tray available (%s), running headless", e)
        return None


def main(argv=None):
    parser = argparse.ArgumentParser(prog="vr-autolauncher", description=APP_NAME)
    parser.add_argument("--config", default=config.CONFIG_FILE, help="config file (default: %(default)s)")
    parser.add_argument("--background", action="store_true",
                        help="start in the tray without opening the settings window (for autostart)")
    parser.add_argument("--headless", action="store_true", help="no tray icon, just watch")
    parser.add_argument("--status", action="store_true", help="show detected processes and apps, then exit")
    parser.add_argument("-v", "--verbose", action="store_true", help="also log to the terminal")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)

    if args.status:
        print_status(args.config)
        return

    osdeps.hide_console()
    if not osdeps.acquire_single_instance():
        print(t("already_running"), file=sys.stderr)
        if not args.background:
            osdeps.signal_running_instance()
        sys.exit(0)
    if hasattr(signal, "SIGUSR1"):
        # Until the tray installs its handler, a "show settings" ping must not kill us.
        signal.signal(signal.SIGUSR1, signal.SIG_IGN)
    setup_logging(args.verbose)
    logging.info("%s %s started (pid %d)", APP_NAME, __version__, os.getpid())

    engine = Engine(args.config)
    tray = pick_tray(engine, args.headless)
    if tray is None:
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: engine.stop())
        engine.run()
        return

    worker = threading.Thread(target=engine.run, name="engine")
    worker.start()
    if not args.background and hasattr(tray, "open_settings"):
        tray.open_settings()
    try:
        tray.run()
    finally:
        engine.stop()
        worker.join(10)
    logging.info("%s stopped", APP_NAME)
