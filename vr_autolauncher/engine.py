# -*- coding: utf-8 -*-
"""The watcher: polls processes, runs countdowns, launches apps, manages the VR session."""

import logging
import os
import re
import signal
import threading
import time

from vr_autolauncher import config, osdeps
from vr_autolauncher.i18n import t

log = logging.getLogger("engine")

# Tray states
ST_WAITING = "waiting"
ST_DETECTED = "detected"
ST_ACTIVE = "active"
ST_CLOSED = "closed"

IDLE, PENDING, DONE = "idle", "pending", "done"

KEEP_ALIVE_GRACE = 20   # seconds a fresh launch gets to show up as a process
KEEP_ALIVE_MAX = 5      # restarts per trigger window, so a broken app can't loop forever


def _matches(proc, patterns):
    return any(p in proc.names or p in proc.path for p in patterns)


class AppState:
    def __init__(self):
        self.phase = IDLE
        self.deadline = 0.0
        self.launched = False   # launched by us during the current "when" window
        self.launched_at = 0.0
        self.restarts = 0
        self.popen = None


class Engine:
    def __init__(self, config_path=config.CONFIG_FILE):
        self.config_path = config_path
        self.cfg = config.load(config_path)
        self._cfg_mtime = self._mtime()
        self.apps = {}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._listeners = []
        self._names_up = set()
        self._procs = []
        self._next_scan = 0.0
        self.session_up = False
        self._closed_until = 0.0
        self._paused_pids = set()
        self._inhibitor = osdeps.IdleInhibitor()
        self._children = []  # Popen objects to reap

    # --- public API (thread-safe) -------------------------------------------

    def add_listener(self, fn):
        self._listeners.append(fn)

    def run(self):
        log.info("Engine started, config: %s", self.config_path)
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:
                log.exception("Watcher error")
            self._emit()
            self._stop.wait(1.0)
        self._shutdown()

    def stop(self):
        self._stop.set()

    def status(self):
        """(state, text) for the tray."""
        with self._lock:
            now = time.monotonic()
            pending = [(st.deadline, name) for name, st in self.apps.items() if st.phase == PENDING]
            if pending and not self.cfg["paused"]:
                deadline, name = min(pending)
                return ST_DETECTED, t("countdown", app=name, n=max(1, int(round(deadline - now))))
            if self.cfg["paused"]:
                return (ST_ACTIVE if self.session_up else ST_WAITING), t("paused")
            if self.session_up:
                return ST_ACTIVE, t("active")
            if now < self._closed_until:
                return ST_CLOSED, t("closed")
            return ST_WAITING, t("waiting")

    def app_list(self):
        """[(name, enabled, phase)] in config order."""
        with self._lock:
            return [(a["name"], a["enabled"], self.apps.get(a["name"], AppState()).phase)
                    for a in self.cfg["apps"]]

    def snapshot(self):
        """Process names seen on the last scan and whether a VR session is up."""
        with self._lock:
            return set(self._names_up), self.session_up

    def last_processes(self):
        with self._lock:
            return list(self._procs)

    @property
    def paused(self):
        return self.cfg["paused"]

    def set_paused(self, paused):
        with self._lock:
            try:
                config.set_paused(paused, self.config_path)
            except (OSError, ValueError) as e:
                log.error("Could not save config: %s", e)
            self._reload(force=True)
            log.info("Auto-launch %s", "paused" if paused else "resumed")
        self._emit()

    def set_enabled(self, name, enabled):
        with self._lock:
            try:
                config.set_app_enabled(name, enabled, self.config_path)
            except (OSError, ValueError) as e:
                log.error("Could not save config: %s", e)
            self._reload(force=True)
        self._emit()

    def launch_now(self, name):
        with self._lock:
            app = self._app(name)
            if app:
                self._procs = osdeps.list_processes()
                self._launch(app, self.apps.setdefault(name, AppState()), manual=True)
        self._emit()

    def test_launch(self, raw_app):
        """Launch an app entry as currently edited (maybe unsaved) in the settings window.

        Returns "launched", "skipped", "missing" or "failed".
        """
        app = config.normalize_app(raw_app)
        with self._lock:
            self._procs = osdeps.list_processes()
            return self._launch(app, AppState(), manual=True)

    def reload(self):
        with self._lock:
            self._reload(force=True)
        self._emit()

    # --- internals ----------------------------------------------------------

    def _emit(self):
        for fn in self._listeners:
            try:
                fn()
            except Exception:
                log.exception("Listener error")

    def _mtime(self):
        try:
            return os.path.getmtime(self.config_path)
        except OSError:
            return 0.0

    def _reload(self, force=False):
        mtime = self._mtime()
        if not force and mtime == self._cfg_mtime:
            return
        self._cfg_mtime = mtime
        self.cfg = config.load(self.config_path)
        log.info("Config reloaded")

    def _app(self, name):
        return next((a for a in self.cfg["apps"] if a["name"] == name), None)

    def _tick(self):
        with self._lock:
            now = time.monotonic()
            self._children = [p for p in self._children if p.poll() is None]
            due = any(st.phase == PENDING and now >= st.deadline for st in self.apps.values())
            if now < self._next_scan and not due:
                return
            self._next_scan = now + self.cfg["check_interval"]
            self._reload()

            self._procs = osdeps.list_processes()
            self._names_up = set().union(*(p.names for p in self._procs)) if self._procs else set()

            self._update_session()
            for app in self.cfg["apps"]:
                self._update_app(app, now)
            # Forget apps removed from the config.
            names = {a["name"] for a in self.cfg["apps"]}
            for name in list(self.apps):
                if name not in names:
                    del self.apps[name]

    def _update_app(self, app, now):
        st = self.apps.setdefault(app["name"], AppState())
        if self.cfg["paused"]:
            # Stop countdowns, but don't treat it as "VR closed" (no close_on_exit).
            if st.phase == PENDING:
                st.phase = IDLE
            return
        check = any if app["trigger"] == "any" else all
        wanted = app["enabled"] and bool(app["when"]) and check(p in self._names_up for p in app["when"])

        if not wanted:
            if st.phase == DONE and st.launched and app["close_on_exit"]:
                self._close(app, st)
            if st.phase != IDLE:
                log.info("%s: conditions gone, reset", app["name"])
            st.phase, st.launched, st.popen, st.restarts = IDLE, False, None, 0
            return

        if st.phase == DONE and app["keep_alive"] and st.launched \
                and now - st.launched_at > KEEP_ALIVE_GRACE and not self._is_running(app, st):
            if st.restarts < KEEP_ALIVE_MAX:
                st.restarts += 1
                st.phase, st.deadline = PENDING, now + 3
                log.warning("%s exited, restarting (%d/%d)", app["name"], st.restarts, KEEP_ALIVE_MAX)
            else:
                st.launched = False
                log.error("%s keeps exiting, giving up until next session", app["name"])

        if st.phase == IDLE:
            st.phase = PENDING
            st.deadline = now + app["delay"]
            log.info("%s: %s detected, launching in %ss", app["name"],
                     (" + " if app["trigger"] == "all" else " / ").join(app["when"]), app["delay"])
        if st.phase == PENDING and now >= st.deadline:
            self._launch(app, st)

    def _is_running(self, app, st):
        if st.popen is not None and st.popen.poll() is None and not osdeps.is_url(app["command"]):
            return True
        return bool(app["running"]) and any(_matches(p, app["running"]) for p in self._procs)

    def _launch(self, app, st, manual=False):
        st.phase = DONE
        name = app["name"]
        if app["running"] and any(_matches(p, app["running"]) for p in self._procs):
            log.info("%s is already running, not launching", name)
            if manual:
                self._notify(t("app_skipped", app=name))
            return "skipped"

        command = app["command"].strip()
        app_log = os.path.join(osdeps.state_dir(), "apps", re.sub(r"[^\w.-]+", "_", name) + ".log")
        try:
            if osdeps.is_url(command):
                popen = osdeps.open_url(command, app_log, app["env"])
            else:
                argv = osdeps.expand_command(command)
                if argv is None:
                    log.warning("%s: program not found: %s", name, command)
                    self._notify(t("app_missing", app=name))
                    return "missing"
                popen = osdeps.spawn(argv, app_log, app["env"])
        except Exception as e:
            log.error("%s: launch failed: %s", name, e)
            self._notify(t("app_failed", app=name))
            return "failed"

        st.launched, st.popen, st.launched_at = True, popen, time.monotonic()
        if popen is not None:
            self._children.append(popen)
        log.info("Launched %s: %s", name, command)
        self._notify(t("app_launched", app=name))
        return "launched"

    def _close(self, app, st):
        closed = False
        if st.popen is not None and st.popen.poll() is None and not osdeps.IS_WINDOWS:
            try:
                os.killpg(st.popen.pid, signal.SIGTERM)
                closed = True
            except OSError:
                pass
        if app["running"]:
            for proc in osdeps.list_processes():
                if _matches(proc, app["running"]):
                    closed = osdeps.terminate(proc.pid) or closed
        if closed:
            log.info("Closed %s", app["name"])
            self._notify(t("app_closed", app=app["name"]))

    def _update_session(self):
        session = self.cfg["session"]
        up = bool(session["processes"]) and all(p in self._names_up for p in session["processes"])

        if up and not self.session_up:
            self.session_up = True
            log.info("VR session started")
            if session["inhibit_idle"] and self._inhibitor.start("VR session in progress"):
                log.info("Screen blanking/suspend inhibited")
            for cmd in session["on_start"]:
                self._hook(cmd)
        elif not up and self.session_up:
            self._end_session()
            self._closed_until = time.monotonic() + 3

        if self.session_up and session["pause_processes"]:
            for proc in self._procs:
                if proc.pid not in self._paused_pids and _matches(proc, session["pause_processes"]):
                    if osdeps.suspend(proc.pid, True):
                        self._paused_pids.add(proc.pid)
                        log.info("Paused pid %d (%s)", proc.pid, ", ".join(sorted(proc.names)))

    def _end_session(self):
        self.session_up = False
        log.info("VR session ended")
        self._inhibitor.stop()
        for pid in self._paused_pids:
            osdeps.suspend(pid, False)
        if self._paused_pids:
            log.info("Resumed %d paused process(es)", len(self._paused_pids))
        self._paused_pids.clear()
        for cmd in self.cfg["session"]["on_stop"]:
            self._hook(cmd)

    def _hook(self, cmd):
        try:
            self._children.append(osdeps.run_hook(cmd))
            log.info("Ran hook: %s", cmd)
        except Exception as e:
            log.error("Hook failed (%s): %s", cmd, e)

    def _notify(self, body):
        if self.cfg["notifications"]:
            osdeps.notify("VR Auto Launcher", body, icon="vr-autolauncher")

    def _shutdown(self):
        # Never leave paused processes frozen or the inhibitor running behind us.
        with self._lock:
            if self.session_up:
                self._end_session()
        log.info("Engine stopped")
