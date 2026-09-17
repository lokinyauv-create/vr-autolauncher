# -*- coding: utf-8 -*-
"""pystray-based tray: used on Windows, and on Linux if AppIndicator is missing."""

import pystray
from pystray import MenuItem as item

from vr_autolauncher import APP_NAME, icons, osdeps
from vr_autolauncher.engine import DONE, PENDING
from vr_autolauncher.i18n import t

PHASE_MARK = {PENDING: "  ⏳", DONE: "  ✓"}


class PystrayTray:
    def __init__(self, engine, log_file):
        self.engine = engine
        self.log_file = log_file
        self._images = {state: icons.make_icon(state) for state in icons.STYLE}
        self._shown = None

        state, text = engine.status()
        self.icon = pystray.Icon(
            name="VRAutoLauncher",
            icon=self._images[state],
            title=APP_NAME + "\n" + text,
            menu=pystray.Menu(self._menu_items),
        )
        engine.add_listener(self.refresh)

    def _menu_items(self):
        _, text = self.engine.status()
        apps = self.engine.app_list()
        yield item(text, None, enabled=False)
        yield pystray.Menu.SEPARATOR
        yield item(t("menu_apps"), None, enabled=False)
        for name, _, phase in apps:
            yield item(name + PHASE_MARK.get(phase, ""), self._toggler(name),
                       checked=self._checker(name))
        yield item(t("menu_pause"), lambda: self.engine.set_paused(not self.engine.paused),
                   checked=lambda _: self.engine.paused)
        yield item(t("menu_launch_now"), pystray.Menu(
            *[item(name, self._launcher(name)) for name, _, _ in apps]))
        yield pystray.Menu.SEPARATOR
        yield item(t("menu_edit_config"), lambda: osdeps.open_path(self.engine.config_path))
        yield item(t("menu_reload"), lambda: self.engine.reload())
        yield item(t("menu_open_log"), lambda: osdeps.open_path(self.log_file))
        yield item(t("menu_quit"), self.quit)

    def _checker(self, name):
        return lambda _: any(n == name and enabled for n, enabled, _ in self.engine.app_list())

    def _toggler(self, name):
        def toggle():
            enabled = any(n == name and e for n, e, _ in self.engine.app_list())
            self.engine.set_enabled(name, not enabled)
        return toggle

    def _launcher(self, name):
        return lambda: self.engine.launch_now(name)

    def refresh(self):
        state, text = self.engine.status()
        if (state, text) == self._shown:
            return
        self._shown = (state, text)
        self.icon.icon = self._images[state]
        self.icon.title = APP_NAME + "\n" + text
        self.icon.update_menu()

    def run(self):
        self.icon.run()

    def quit(self):
        self.engine.stop()
        self.icon.stop()
