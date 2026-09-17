# -*- coding: utf-8 -*-
"""Tray icon for Linux desktops via AppIndicator (StatusNotifierItem).

GNOME needs the "AppIndicator and KStatusNotifierItem Support" extension;
KDE, Cinnamon, XFCE etc. show it out of the box. Only distro packages are
needed (Fedora: python3-gobject libappindicator-gtk3 python3-pillow).
"""

import signal

import gi

gi.require_version("Gtk", "3.0")
try:
    gi.require_version("AyatanaAppIndicator3", "0.1")
    from gi.repository import AyatanaAppIndicator3 as AppIndicator
except (ValueError, ImportError):
    gi.require_version("AppIndicator3", "0.1")
    from gi.repository import AppIndicator3 as AppIndicator
from gi.repository import GLib, Gtk  # noqa: E402

if not Gtk.init_check()[0]:
    # Without this GTK aborts the whole process (no Wayland/X11 display, e.g. over SSH).
    raise ImportError("no graphical display")

from vr_autolauncher import APP_ID, APP_NAME, icons, osdeps  # noqa: E402
from vr_autolauncher.engine import DONE, PENDING, ST_DETECTED  # noqa: E402
from vr_autolauncher.i18n import t  # noqa: E402

PHASE_MARK = {PENDING: "  ⏳", DONE: "  ✓"}


class LinuxTray:
    def __init__(self, engine, log_file):
        self.engine = engine
        self.log_file = log_file
        icon_dir = osdeps.runtime_dir()
        self.icon_names = icons.write_icons(icon_dir)
        self._shown = None
        self._apps_shown = None
        self._settings = None

        self.indicator = AppIndicator.Indicator.new(
            APP_ID, self.icon_names["waiting"], AppIndicator.IndicatorCategory.APPLICATION_STATUS)
        self.indicator.set_icon_theme_path(icon_dir)
        self.indicator.set_title(APP_NAME)
        self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)

        self.menu = Gtk.Menu()
        self.status_item = Gtk.MenuItem(label="")
        self.status_item.set_sensitive(False)
        self.menu.append(self.status_item)
        settings = Gtk.MenuItem(label=t("menu_settings"))
        settings.connect("activate", lambda _: self.open_settings())
        self.menu.append(settings)
        self.pause_item = Gtk.CheckMenuItem(label=t("menu_pause"))
        self.pause_item.connect("toggled", self._on_pause_toggled)
        self.menu.append(self.pause_item)
        self.menu.append(Gtk.SeparatorMenuItem())
        self.apps_anchor = len(self.menu.get_children())
        self._app_items = []

        self.menu.append(Gtk.SeparatorMenuItem())
        for label, handler in (
            (t("menu_open_log"), lambda _: osdeps.open_path(self.log_file)),
            (t("menu_quit"), lambda _: self.quit()),
        ):
            mi = Gtk.MenuItem(label=label)
            mi.connect("activate", handler)
            self.menu.append(mi)
        self.menu.show_all()
        self.indicator.set_menu(self.menu)

        engine.add_listener(lambda: GLib.idle_add(self.refresh))
        for sig in (signal.SIGINT, signal.SIGTERM):
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, self.quit)
        # A second launch of the program (e.g. from the app menu) asks us to show settings.
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR1, self._on_show_request)
        self.refresh()

    def open_settings(self):
        from vr_autolauncher.settings_gtk import SettingsWindow
        if self._settings is None:
            self._settings = SettingsWindow(self.engine, self.log_file)
            self._settings.connect("destroy", self._on_settings_closed)
        self._settings.present()
        return False

    def _on_settings_closed(self, _window):
        self._settings = None

    def _on_show_request(self):
        self.open_settings()
        return GLib.SOURCE_CONTINUE

    def _on_pause_toggled(self, item):
        if item.get_active() != self.engine.paused:
            self.engine.set_paused(item.get_active())

    def refresh(self):
        if self.pause_item.get_active() != self.engine.paused:
            self.pause_item.set_active(self.engine.paused)
        state, text = self.engine.status()
        if (state, text) != self._shown:
            self._shown = (state, text)
            self.status_item.set_label(text)
            self.indicator.set_icon_full(self.icon_names[state], text)
            # The countdown next to the icon, like the old Windows tooltip.
            label = text.rsplit(" ", 1)[-1].rstrip(".") if state == ST_DETECTED else ""
            self.indicator.set_label(label, "00s")

        apps = self.engine.app_list()
        if apps != self._apps_shown:
            self._apps_shown = apps
            self._rebuild_apps(apps)
        return False  # one-shot idle callback

    def _rebuild_apps(self, apps):
        for mi in self._app_items:
            self.menu.remove(mi)
        self._app_items = []

        header = Gtk.MenuItem(label=t("menu_apps"))
        header.set_sensitive(False)
        items = [header]
        for name, enabled, phase in apps:
            mi = Gtk.CheckMenuItem(label=name + PHASE_MARK.get(phase, ""))
            mi.set_active(enabled)
            mi.connect("toggled", lambda w, n=name: self.engine.set_enabled(n, w.get_active()))
            items.append(mi)

        launch = Gtk.MenuItem(label=t("menu_launch_now"))
        sub = Gtk.Menu()
        for name, _, _ in apps:
            mi = Gtk.MenuItem(label=name)
            mi.connect("activate", lambda _, n=name: self.engine.launch_now(n))
            sub.append(mi)
        launch.set_submenu(sub)
        items.append(launch)

        for offset, mi in enumerate(items):
            self.menu.insert(mi, self.apps_anchor + offset)
        self._app_items = items
        self.menu.show_all()

    def run(self):
        Gtk.main()

    def quit(self, *_):
        self.engine.stop()
        Gtk.main_quit()
        return GLib.SOURCE_REMOVE
