# -*- coding: utf-8 -*-
"""Settings window (GTK 3). Edits config.json; the engine picks the file up on save."""

import copy
import os
import shlex
import stat

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gio, GLib, Gtk, Pango  # noqa: E402

from vr_autolauncher import APP_NAME, config, discovery, osdeps  # noqa: E402
from vr_autolauncher.engine import DONE, PENDING  # noqa: E402
from vr_autolauncher.i18n import t  # noqa: E402

PRESET_IDS = [("steamvr", "trig_steamvr"), ("vrchat", "trig_vrchat"),
              ("steamvr+vrchat", "trig_both"), ("custom", "trig_custom")]
PHASE_MARK = {PENDING: "⏳ ", DONE: "✓ "}

CSS = b"""
.dim-label { opacity: 0.6; }
.section-title { font-weight: bold; margin-top: 12px; }
.app-row-title { font-weight: bold; }
.status-ok { color: #27ae60; }
.status-bad { color: #e74c3c; }
"""


def _split_list(text):
    return [p.strip() for p in text.split(",") if p.strip()]


def _preset_for(processes):
    normalized = [osdeps.normalize_name(p) for p in processes]
    for preset, names in config.TRIGGER_PRESETS.items():
        if sorted(normalized) == sorted(names):
            return preset
    return "custom"


def _env_to_text(env):
    return " ".join("%s=%s" % (k, shlex.quote(v)) for k, v in (env or {}).items())


def _text_to_env(text):
    env = {}
    try:
        parts = shlex.split(text)
    except ValueError:
        parts = text.split()
    for part in parts:
        if "=" in part:
            key, value = part.split("=", 1)
            if key.strip():
                env[key.strip()] = value
    return env


def _label(text, dim=False, xalign=0.0, wrap=False):
    lbl = Gtk.Label(label=text, xalign=xalign)
    if dim:
        lbl.get_style_context().add_class("dim-label")
    if wrap:
        lbl.set_line_wrap(True)
    return lbl


# --- Generic "pick from a list" dialog --------------------------------------------

def pick_from_list(parent, title, rows, multi=False):
    """rows: [(title, subtitle, value, Gio.Icon|icon-name|None)] -> [value, ...] or None."""
    dialog = Gtk.Dialog(title=title, transient_for=parent, modal=True, use_header_bar=True)
    dialog.add_button(t("cancel"), Gtk.ResponseType.CANCEL)
    ok = dialog.add_button(t("pick").rstrip("…."), Gtk.ResponseType.OK)
    ok.get_style_context().add_class("suggested-action")
    dialog.set_default_size(480, 560)

    box = dialog.get_content_area()
    box.set_spacing(6)
    box.set_border_width(8)
    search = Gtk.SearchEntry(placeholder_text=t("search"))
    box.pack_start(search, False, False, 0)

    listbox = Gtk.ListBox()
    listbox.set_selection_mode(Gtk.SelectionMode.MULTIPLE if multi else Gtk.SelectionMode.SINGLE)
    if multi:
        listbox.set_activate_on_single_click(False)
    for title_text, subtitle, value, icon in rows:
        row = Gtk.ListBoxRow()
        row.value = value
        row.haystack = ("%s %s" % (title_text, subtitle or "")).lower()
        hbox = Gtk.Box(spacing=10, margin=6)
        if icon is not None:
            if isinstance(icon, str):
                img = Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.DND)
            else:
                img = Gtk.Image.new_from_gicon(icon, Gtk.IconSize.DND)
            img.set_pixel_size(32)
            hbox.pack_start(img, False, False, 0)
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        name = _label(title_text)
        name.set_ellipsize(Pango.EllipsizeMode.END)
        vbox.pack_start(name, False, False, 0)
        if subtitle:
            sub = _label(subtitle, dim=True)
            sub.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            vbox.pack_start(sub, False, False, 0)
        hbox.pack_start(vbox, True, True, 0)
        row.add(hbox)
        listbox.add(row)

    listbox.set_filter_func(lambda row: search.get_text().lower() in row.haystack)
    search.connect("search-changed", lambda _: listbox.invalidate_filter())
    listbox.connect("row-activated", lambda *_: dialog.response(Gtk.ResponseType.OK) if not multi else None)

    scroller = Gtk.ScrolledWindow(vexpand=True)
    scroller.add(listbox)
    box.pack_start(scroller, True, True, 0)
    dialog.show_all()
    search.grab_focus()

    result = None
    if dialog.run() == Gtk.ResponseType.OK:
        result = [row.value for row in listbox.get_selected_rows()] or None
    dialog.destroy()
    return result


def ask(parent, text, secondary=None, buttons=Gtk.ButtonsType.YES_NO):
    dialog = Gtk.MessageDialog(transient_for=parent, modal=True, message_type=Gtk.MessageType.QUESTION,
                               buttons=buttons, text=text)
    if secondary:
        dialog.format_secondary_text(secondary)
    response = dialog.run()
    dialog.destroy()
    return response


# --- The window --------------------------------------------------------------------

class SettingsWindow(Gtk.Window):
    def __init__(self, engine, log_file):
        super().__init__(title=APP_NAME)
        self.engine = engine
        self.log_file = log_file
        self.set_default_size(920, 640)
        self.set_icon_name("vr-autolauncher")

        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(self.get_screen(), provider,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self._loading = False
        self._dirty = False
        self._index = -1
        self._file_mtime = 0.0
        self.data = {}

        # Header: tabs in the middle, Save on the right.
        header = Gtk.HeaderBar(show_close_button=True)
        self.set_titlebar(header)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        header.set_custom_title(Gtk.StackSwitcher(stack=self.stack))
        self.save_button = Gtk.Button(label=t("save"))
        self.save_button.get_style_context().add_class("suggested-action")
        self.save_button.connect("clicked", lambda _: self.save())
        header.pack_end(self.save_button)

        self.stack.add_titled(self._build_apps_page(), "apps", t("tab_apps"))
        self.stack.add_titled(self._build_general_page(), "general", t("tab_general"))

        # Status bar: live SteamVR / VRChat state.
        self.status_label = _label("", dim=True)
        status_bar = Gtk.Box(spacing=8, margin=6, margin_start=12, margin_end=12)
        status_bar.pack_start(self.status_label, True, True, 0)
        self.toast = _label("", xalign=1.0)
        status_bar.pack_end(self.toast, False, False, 0)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.pack_start(self.stack, True, True, 0)
        root.pack_start(Gtk.Separator(), False, False, 0)
        root.pack_start(status_bar, False, False, 0)
        self.add(root)

        self.connect("delete-event", self._on_delete)
        self.add_accelerator_save()
        self.load()
        self._timer = GLib.timeout_add(1000, self._tick)
        self.connect("destroy", lambda _: GLib.source_remove(self._timer))
        self.show_all()
        self._update_form_visibility()

    def add_accelerator_save(self):
        accel = Gtk.AccelGroup()
        key, mod = Gtk.accelerator_parse("<Control>s")
        accel.connect(key, mod, 0, lambda *_: self.save() or True)
        self.add_accel_group(accel)

    # --- data ------------------------------------------------------------------

    def load(self):
        try:
            raw = config.load_raw(self.engine.config_path)
        except (OSError, ValueError):
            raw = config.defaults()
        base = config.defaults()
        self.data = {
            "check_interval": raw.get("check_interval", base["check_interval"]),
            "notifications": raw.get("notifications", base["notifications"]),
            "paused": raw.get("paused", False),
            "apps": [dict(copy.deepcopy(config.APP_DEFAULTS), **a) for a in raw.get("apps", [])],
            "session": dict(base["session"], **raw.get("session", {})),
        }
        # Keep any keys we don't know about.
        for key, value in raw.items():
            self.data.setdefault(key, value)
        self._file_mtime = self._mtime()
        self._rebuild_list(select=max(0, min(self._index, len(self.data["apps"]) - 1)))
        self._fill_general()
        self._set_dirty(False)

    def save(self):
        renamed = self._dedupe_names()
        try:
            config.save(self.data, self.engine.config_path)
        except OSError as e:
            self._show_toast(str(e), bad=True)
            return False
        self._file_mtime = self._mtime()
        self.engine.reload()
        self._set_dirty(False)
        if renamed:
            self._rebuild_list(select=self._index)
            self._show_toast(t("dup_names", names=", ".join(renamed)), bad=True)
        else:
            self._show_toast(t("saved"))
        return True

    def _dedupe_names(self):
        seen, renamed = set(), []
        for app in self.data["apps"]:
            name = app.get("name", "").strip() or t("new_app")
            base, n = name, 2
            while name in seen:
                name = "%s (%d)" % (base, n)
                n += 1
            if name != app.get("name"):
                renamed.append(name)
            app["name"] = name
            seen.add(name)
        return renamed

    def _mtime(self):
        try:
            return os.path.getmtime(self.engine.config_path)
        except OSError:
            return 0.0

    def _set_dirty(self, dirty=True):
        if self._loading:
            return
        self._dirty = dirty
        self.save_button.set_sensitive(dirty)
        self.set_title(("• " if dirty else "") + APP_NAME)

    def _show_toast(self, text, bad=False):
        ctx = self.toast.get_style_context()
        ctx.remove_class("status-ok")
        ctx.remove_class("status-bad")
        ctx.add_class("status-bad" if bad else "status-ok")
        self.toast.set_text(text)
        GLib.timeout_add_seconds(4, lambda: self.toast.set_text("") and False)

    @property
    def app(self):
        apps = self.data.get("apps", [])
        return apps[self._index] if 0 <= self._index < len(apps) else None

    # --- Programs page ---------------------------------------------------------

    def _build_apps_page(self):
        paned = Gtk.Paned(position=300)

        # Left: list + toolbar
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.listbox = Gtk.ListBox()
        self.listbox.connect("row-selected", self._on_row_selected)
        scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.add(self.listbox)
        left.pack_start(scroller, True, True, 0)

        toolbar = Gtk.Box(spacing=4, margin=6)
        add_menu = Gtk.Menu()
        for key, handler in (("add_steam", self._add_steam), ("add_desktop", self._add_desktop),
                             ("add_file", self._add_file), ("add_empty", self._add_empty)):
            mi = Gtk.MenuItem(label=t(key))
            mi.connect("activate", lambda _, h=handler: h())
            add_menu.append(mi)
        add_menu.show_all()
        add_button = Gtk.MenuButton(popup=add_menu, tooltip_text=t("add"))
        add_button.add(Gtk.Image.new_from_icon_name("list-add-symbolic", Gtk.IconSize.BUTTON))
        toolbar.pack_start(add_button, False, False, 0)
        for icon, tip, handler in (("list-remove-symbolic", "remove", self._remove),
                                   ("edit-copy-symbolic", "duplicate", self._duplicate),
                                   ("go-up-symbolic", "move_up", lambda: self._move(-1)),
                                   ("go-down-symbolic", "move_down", lambda: self._move(1))):
            button = Gtk.Button.new_from_icon_name(icon, Gtk.IconSize.BUTTON)
            button.set_tooltip_text(t(tip))
            button.connect("clicked", lambda _, h=handler: h())
            toolbar.pack_start(button, False, False, 0)
        left.pack_start(Gtk.Separator(), False, False, 0)
        left.pack_start(toolbar, False, False, 0)
        paned.pack1(left, False, False)

        # Right: the form
        self.form_stack = Gtk.Stack()
        empty = _label(t("no_apps"), dim=True, xalign=0.5)
        empty.set_justify(Gtk.Justification.CENTER)
        self.form_stack.add_named(empty, "empty")
        form_scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        form_scroller.add(self._build_form())
        self.form_stack.add_named(form_scroller, "form")
        paned.pack2(self.form_stack, True, False)
        return paned

    def _build_form(self):
        grid = Gtk.Grid(column_spacing=12, row_spacing=10, margin=18)
        row = 0

        def add_row(label_key, widget, hint=None):
            nonlocal row
            lbl = _label(t(label_key), xalign=1.0)
            lbl.set_valign(Gtk.Align.START if hint else Gtk.Align.CENTER)
            lbl.set_margin_top(6 if hint else 0)
            grid.attach(lbl, 0, row, 1, 1)
            if hint:
                box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
                box.pack_start(widget, False, False, 0)
                box.pack_start(_label(hint, dim=True, wrap=True), False, False, 0)
                widget = box
            widget.set_hexpand(True)
            grid.attach(widget, 1, row, 1, 1)
            row += 1
            return lbl, widget

        # Name + enabled
        name_box = Gtk.Box(spacing=12)
        self.f_name = Gtk.Entry()
        self.f_name.connect("changed", self._on_field, "name")
        name_box.pack_start(self.f_name, True, True, 0)
        name_box.pack_start(_label(t("f_enabled")), False, False, 0)
        self.f_enabled = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.f_enabled.connect("notify::active", self._on_field, "enabled")
        name_box.pack_start(self.f_enabled, False, False, 0)
        add_row("f_name", name_box)

        # Trigger
        self.f_trigger = Gtk.ComboBoxText()
        for preset, key in PRESET_IDS:
            self.f_trigger.append(preset, t(key))
        self.f_trigger.connect("changed", self._on_trigger_preset)
        add_row("f_trigger", self.f_trigger)

        proc_box = Gtk.Box(spacing=6)
        self.f_processes = Gtk.Entry()
        self.f_processes.connect("changed", self._on_field, "when")
        proc_box.pack_start(self.f_processes, True, True, 0)
        pick = Gtk.Button(label=t("pick"))
        pick.connect("clicked", lambda _: self._pick_processes(self.f_processes))
        proc_box.pack_start(pick, False, False, 0)
        mode_box = Gtk.Box(spacing=12)
        self.f_mode_all = Gtk.RadioButton(label=t("trig_all"))
        self.f_mode_any = Gtk.RadioButton(label=t("trig_any"), group=self.f_mode_all)
        self.f_mode_all.connect("toggled", self._on_field, "trigger")
        mode_box.pack_start(self.f_mode_all, False, False, 0)
        mode_box.pack_start(self.f_mode_any, False, False, 0)
        custom = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        custom.pack_start(proc_box, False, False, 0)
        custom.pack_start(mode_box, False, False, 0)
        self.custom_rows = add_row("f_processes", custom, t("processes_hint"))

        # Delay
        delay_box = Gtk.Box(spacing=8)
        self.f_delay = Gtk.SpinButton.new_with_range(0, 600, 1)
        self.f_delay.connect("value-changed", self._on_field, "delay")
        delay_box.pack_start(self.f_delay, False, False, 0)
        delay_box.pack_start(_label(t("delay_suffix"), dim=True), False, False, 0)
        add_row("f_delay", delay_box)

        # Command
        cmd_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.f_command = Gtk.Entry(placeholder_text=t("command_hint"))
        self.f_command.connect("changed", self._on_field, "command")
        cmd_box.pack_start(self.f_command, False, False, 0)
        buttons = Gtk.Box(spacing=6)
        for key, handler in (("browse_file", self._browse_file), ("browse_steam", self._browse_steam),
                             ("browse_desktop", self._browse_desktop)):
            b = Gtk.Button(label=t(key))
            b.connect("clicked", lambda _, h=handler: h(self.app))
            buttons.pack_start(b, False, False, 0)
        self.cmd_status = _label("")
        buttons.pack_end(self.cmd_status, False, False, 0)
        cmd_box.pack_start(buttons, False, False, 0)
        add_row("f_command", cmd_box)

        # Already running
        run_box = Gtk.Box(spacing=6)
        self.f_running = Gtk.Entry()
        self.f_running.connect("changed", self._on_field, "running")
        run_box.pack_start(self.f_running, True, True, 0)
        pick = Gtk.Button(label=t("pick"))
        pick.connect("clicked", lambda _: self._pick_processes(self.f_running))
        run_box.pack_start(pick, False, False, 0)
        guess = Gtk.Button(label=t("guess"))
        guess.connect("clicked", lambda _: self.f_running.set_text(
            discovery.suggest_running(self.f_command.get_text())))
        run_box.pack_start(guess, False, False, 0)
        self.running_status = _label("", dim=True)
        run_outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        run_outer.pack_start(run_box, False, False, 0)
        add_row("f_running", run_outer, t("running_hint"))
        run_outer.pack_start(self.running_status, False, False, 0)

        # Env
        self.f_env = Gtk.Entry(placeholder_text=t("env_hint"))
        self.f_env.connect("changed", self._on_field, "env")
        add_row("f_env", self.f_env)

        # Options
        self.f_close = Gtk.CheckButton(label=t("f_close_on_exit"))
        self.f_close.connect("toggled", self._on_field, "close_on_exit")
        self.f_keep = Gtk.CheckButton(label=t("f_keep_alive"))
        self.f_keep.connect("toggled", self._on_field, "keep_alive")
        opts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        opts.pack_start(self.f_close, False, False, 0)
        opts.pack_start(self.f_keep, False, False, 0)
        grid.attach(opts, 1, row, 1, 1)
        row += 1

        # Test
        test_box = Gtk.Box(spacing=10, margin_top=6)
        test = Gtk.Button(label=t("test_launch"))
        test.connect("clicked", lambda _: self._test_launch())
        test_box.pack_start(test, False, False, 0)
        self.test_result = _label("", dim=True)
        test_box.pack_start(self.test_result, False, False, 0)
        grid.attach(test_box, 1, row, 1, 1)
        return grid

    def _rebuild_list(self, select=0):
        self._loading = True
        for child in self.listbox.get_children():
            self.listbox.remove(child)
        self._row_widgets = []
        for i, app in enumerate(self.data["apps"]):
            row = Gtk.ListBoxRow()
            box = Gtk.Box(spacing=8, margin=8)
            texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            title = _label("")
            title.get_style_context().add_class("app-row-title")
            title.set_ellipsize(Pango.EllipsizeMode.END)
            subtitle = _label("", dim=True)
            subtitle.set_ellipsize(Pango.EllipsizeMode.END)
            texts.pack_start(title, False, False, 0)
            texts.pack_start(subtitle, False, False, 0)
            box.pack_start(texts, True, True, 0)
            switch = Gtk.Switch(valign=Gtk.Align.CENTER, active=bool(app.get("enabled", True)))
            switch.connect("notify::active", self._on_row_switch, i)
            box.pack_end(switch, False, False, 0)
            row.add(box)
            self.listbox.add(row)
            self._row_widgets.append((title, subtitle, switch))
            self._refresh_row(i)
        self.listbox.show_all()
        self._loading = False
        rows = self.listbox.get_children()
        if rows:
            self.listbox.select_row(rows[max(0, min(select, len(rows) - 1))])
        else:
            self._index = -1
        self._update_form_visibility()

    def _refresh_row(self, i, phases=None):
        if i >= len(self._row_widgets):
            return
        app = self.data["apps"][i]
        title, subtitle, switch = self._row_widgets[i]
        mark = PHASE_MARK.get((phases or {}).get(app.get("name")), "")
        title.set_text(mark + (app.get("name") or t("new_app")))
        preset = _preset_for(app.get("when", []))
        trigger = dict(PRESET_IDS).get(preset)
        if preset == "custom":
            joiner = " + " if app.get("trigger", "all") == "all" else " / "
            trigger_text = joiner.join(app.get("when", [])) or "—"
        else:
            trigger_text = t(trigger)
        subtitle.set_text("%s · %s" % (trigger_text, t("summary_delay", n=int(float(app.get("delay", 0))))))
        if switch.get_active() != bool(app.get("enabled", True)):
            self._loading = True
            switch.set_active(bool(app.get("enabled", True)))
            self._loading = False

    def _update_form_visibility(self):
        has = self.app is not None
        self.form_stack.set_visible_child_name("form" if has else "empty")
        if has:
            custom = self.f_trigger.get_active_id() == "custom"
            for widget in self.custom_rows:
                widget.set_visible(custom)

    def _on_row_selected(self, _listbox, row):
        self._index = row.get_index() if row else -1
        self._fill_form()

    def _on_row_switch(self, switch, _pspec, i):
        if self._loading:
            return
        self.data["apps"][i]["enabled"] = switch.get_active()
        if i == self._index:
            self._loading = True
            self.f_enabled.set_active(switch.get_active())
            self._loading = False
        self._set_dirty()

    def _fill_form(self):
        app = self.app
        self._update_form_visibility()
        if app is None:
            return
        self._loading = True
        self.f_name.set_text(app.get("name", ""))
        self.f_enabled.set_active(bool(app.get("enabled", True)))
        self.f_trigger.set_active_id(_preset_for(app.get("when", [])))
        self.f_processes.set_text(", ".join(app.get("when", [])))
        (self.f_mode_any if app.get("trigger") == "any" else self.f_mode_all).set_active(True)
        self.f_delay.set_value(float(app.get("delay", 0)))
        self.f_command.set_text(app.get("command", ""))
        self.f_running.set_text(", ".join(app.get("running", [])))
        self.f_env.set_text(_env_to_text(app.get("env")))
        self.f_close.set_active(bool(app.get("close_on_exit")))
        self.f_keep.set_active(bool(app.get("keep_alive")))
        self.test_result.set_text("")
        self._loading = False
        self._update_form_visibility()
        self._update_command_status()

    def _on_field(self, widget, *args):
        key = args[-1]
        app = self.app
        if self._loading or app is None:
            return
        if key == "name":
            app["name"] = widget.get_text()
        elif key == "enabled":
            app["enabled"] = widget.get_active()
        elif key == "when":
            app["when"] = _split_list(widget.get_text())
        elif key == "trigger":
            app["trigger"] = "all" if self.f_mode_all.get_active() else "any"
        elif key == "delay":
            app["delay"] = int(widget.get_value())
        elif key == "command":
            app["command"] = widget.get_text()
            self._update_command_status()
        elif key == "running":
            app["running"] = _split_list(widget.get_text())
            self._update_command_status()
        elif key == "env":
            app["env"] = _text_to_env(widget.get_text())
        elif key in ("close_on_exit", "keep_alive"):
            app[key] = widget.get_active()
        self._refresh_row(self._index)
        self._set_dirty()

    def _on_trigger_preset(self, combo):
        app = self.app
        if self._loading or app is None:
            return
        preset = combo.get_active_id()
        if preset in config.TRIGGER_PRESETS:
            app["when"] = list(config.TRIGGER_PRESETS[preset])
            app["trigger"] = "all"
            self._loading = True
            self.f_processes.set_text(", ".join(app["when"]))
            self.f_mode_all.set_active(True)
            self._loading = False
        self._update_form_visibility()
        if preset == "custom":
            self.f_processes.grab_focus()
        self._refresh_row(self._index)
        self._set_dirty()

    def _update_command_status(self):
        command = self.f_command.get_text().strip()
        ctx = self.cmd_status.get_style_context()
        ctx.remove_class("status-ok")
        ctx.remove_class("status-bad")
        if not command:
            self.cmd_status.set_text("")
            return
        if osdeps.is_url(command):
            text, ok = t("cmd_url"), True
        else:
            try:
                ok = osdeps.expand_command(command) is not None
            except ValueError:
                ok = False
            text = t("cmd_ok") if ok else t("cmd_missing")
        self.cmd_status.set_text(text)
        ctx.add_class("status-ok" if ok else "status-bad")
        self._update_running_status()

    def _update_running_status(self):
        patterns = [p.lower() for p in _split_list(self.f_running.get_text())]
        if not patterns:
            self.running_status.hide()
            return
        running = any(any(p in proc.names or p in proc.path for p in patterns)
                      for proc in self.engine.last_processes())
        self.running_status.set_text("● " + (t("st_running") if running else t("st_not_running")))
        self.running_status.show()

    def _test_launch(self):
        if self.app is None:
            return
        result = self.engine.test_launch(self.app)
        self.test_result.set_text(t("result_" + result))

    # --- adding / pickers ------------------------------------------------------

    def _new_app(self, **fields):
        app = copy.deepcopy(config.APP_DEFAULTS)
        app["name"] = t("new_app")
        app.update(fields)
        self.data["apps"].append(app)
        self._rebuild_list(select=len(self.data["apps"]) - 1)
        self._set_dirty()
        return app

    def _add_empty(self):
        self._new_app()
        self.f_name.grab_focus()

    def _add_steam(self):
        game = self._pick_steam()
        if game:
            self._new_app(name=game.name, command=game.command, running=[game.installdir.lower()])

    def _add_desktop(self):
        picked = self._pick_desktop()
        if picked:
            name, command = picked
            self._new_app(name=name, command=command,
                          running=[r for r in [discovery.suggest_running(command)] if r])

    def _add_file(self):
        path = self._pick_file()
        if path:
            command = shlex.quote(path)
            self._new_app(name=os.path.basename(path).split(".AppImage")[0], command=command,
                          running=[r for r in [discovery.suggest_running(command)] if r])

    def _apply_picked(self, app, name, command, running):
        if app is None:
            return
        if not app.get("name") or app.get("name") == t("new_app"):
            app["name"] = name
        app["command"] = command
        if running:
            app["running"] = [running]
        self._fill_form()
        self._refresh_row(self._index)
        self._set_dirty()

    def _browse_file(self, app):
        path = self._pick_file()
        if path:
            command = shlex.quote(path)
            self._apply_picked(app, os.path.basename(path).split(".AppImage")[0], command,
                               discovery.suggest_running(command))

    def _browse_steam(self, app):
        game = self._pick_steam()
        if game:
            self._apply_picked(app, game.name, game.command, game.installdir.lower())

    def _browse_desktop(self, app):
        picked = self._pick_desktop()
        if picked:
            name, command = picked
            self._apply_picked(app, name, command, discovery.suggest_running(command))

    def _pick_steam(self):
        theme = Gtk.IconTheme.get_default()
        rows = []
        for game in discovery.steam_games():
            icon = "steam_icon_" + game.appid
            rows.append((game.name, "steam://rungameid/%s" % game.appid, game,
                         icon if theme.has_icon(icon) else "applications-games"))
        picked = pick_from_list(self, t("pick_steam"), rows)
        return picked[0] if picked else None

    def _pick_desktop(self):
        rows = []
        seen = set()
        for info in sorted(Gio.AppInfo.get_all(), key=lambda i: i.get_display_name().lower()):
            if not info.should_show() or not info.get_commandline():
                continue
            command = discovery.clean_exec(info.get_commandline())
            if (info.get_display_name(), command) in seen:
                continue
            seen.add((info.get_display_name(), command))
            rows.append((info.get_display_name(), command, (info.get_display_name(), command), info.get_icon()))
        picked = pick_from_list(self, t("pick_desktop"), rows)
        return picked[0] if picked else None

    def _pick_file(self):
        chooser = Gtk.FileChooserNative(title=t("pick_file"), transient_for=self,
                                        action=Gtk.FileChooserAction.OPEN)
        applications = os.path.expanduser("~/Applications")
        if os.path.isdir(applications):
            chooser.set_current_folder(applications)
        path = chooser.get_filename() if chooser.run() == Gtk.ResponseType.ACCEPT else None
        chooser.destroy()
        if path and not os.access(path, os.X_OK):
            if ask(self, t("not_executable"), path) == Gtk.ResponseType.YES:
                try:
                    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                except OSError as e:
                    self._show_toast(str(e), bad=True)
        return path

    def _pick_processes(self, entry):
        current = _split_list(entry.get_text())
        rows = [(name, None, name, None) for name in discovery.running_process_names()]
        picked = pick_from_list(self, t("pick_process"), rows, multi=True)
        if picked:
            entry.set_text(", ".join(dict.fromkeys(current + picked)))

    def _remove(self):
        if self.app is None:
            return
        del self.data["apps"][self._index]
        self._rebuild_list(select=self._index)
        self._set_dirty()

    def _duplicate(self):
        if self.app is None:
            return
        clone = copy.deepcopy(self.app)
        clone["name"] = clone.get("name", "") + " (2)"
        self.data["apps"].insert(self._index + 1, clone)
        self._rebuild_list(select=self._index + 1)
        self._set_dirty()

    def _move(self, delta):
        i, j = self._index, self._index + delta
        apps = self.data["apps"]
        if self.app is None or not 0 <= j < len(apps):
            return
        apps[i], apps[j] = apps[j], apps[i]
        self._rebuild_list(select=j)
        self._set_dirty()

    # --- General page ----------------------------------------------------------

    def _build_general_page(self):
        grid = Gtk.Grid(column_spacing=12, row_spacing=10, margin=18)
        row = 0

        def section(key):
            nonlocal row
            lbl = _label(t(key))
            lbl.get_style_context().add_class("section-title")
            grid.attach(lbl, 0, row, 2, 1)
            row += 1

        def add(label_key, widget, hint=None, expand=True):
            nonlocal row
            lbl = _label(t(label_key), xalign=1.0)
            lbl.set_valign(Gtk.Align.START if hint else Gtk.Align.CENTER)
            lbl.set_margin_top(6 if hint else 0)
            grid.attach(lbl, 0, row, 1, 1)
            holder = widget
            if hint:
                holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
                holder.pack_start(widget, False, False, 0)
                holder.pack_start(_label(hint, dim=True, wrap=True), False, False, 0)
            if not expand:
                box = Gtk.Box()
                box.pack_start(holder, False, False, 0)
                holder = box
            holder.set_hexpand(True)
            grid.attach(holder, 1, row, 1, 1)
            row += 1

        section("g_behaviour")
        self.g_autostart = Gtk.Switch()
        self.g_autostart.connect("notify::active", self._on_autostart)
        add("g_autostart", self.g_autostart, expand=False)
        self.g_paused = Gtk.Switch()
        self.g_paused.connect("notify::active", self._on_general, "paused")
        add("g_paused", self.g_paused, expand=False)
        self.g_notifications = Gtk.Switch()
        self.g_notifications.connect("notify::active", self._on_general, "notifications")
        add("g_notifications", self.g_notifications, expand=False)
        interval = Gtk.Box(spacing=8)
        self.g_interval = Gtk.SpinButton.new_with_range(1, 60, 1)
        self.g_interval.connect("value-changed", self._on_general, "check_interval")
        interval.pack_start(self.g_interval, False, False, 0)
        interval.pack_start(_label(t("seconds"), dim=True), False, False, 0)
        add("g_interval", interval, expand=False)

        section("g_session")
        session_box = Gtk.Box(spacing=6)
        self.g_session_procs = Gtk.Entry()
        self.g_session_procs.connect("changed", self._on_general, "session.processes")
        session_box.pack_start(self.g_session_procs, True, True, 0)
        pick = Gtk.Button(label=t("pick"))
        pick.connect("clicked", lambda _: self._pick_processes(self.g_session_procs))
        session_box.pack_start(pick, False, False, 0)
        add("g_session_trigger", session_box, t("processes_hint"))
        self.g_inhibit = Gtk.Switch()
        self.g_inhibit.connect("notify::active", self._on_general, "session.inhibit_idle")
        add("g_inhibit", self.g_inhibit, expand=False)
        pause_box = Gtk.Box(spacing=6)
        self.g_pause = Gtk.Entry()
        self.g_pause.connect("changed", self._on_general, "session.pause_processes")
        pause_box.pack_start(self.g_pause, True, True, 0)
        pick = Gtk.Button(label=t("pick"))
        pick.connect("clicked", lambda _: self._pick_processes(self.g_pause))
        pause_box.pack_start(pick, False, False, 0)
        add("g_pause_procs", pause_box, t("g_pause_hint"))
        self.g_on_start = self._text_view("session.on_start")
        add("g_on_start", self.g_on_start.get_parent(), t("commands_hint"))
        self.g_on_stop = self._text_view("session.on_stop")
        add("g_on_stop", self.g_on_stop.get_parent(), t("commands_hint"))

        section("g_files")
        files = Gtk.Box(spacing=6)
        for key, handler in (("open_config", lambda: osdeps.open_path(self.engine.config_path)),
                             ("open_log", lambda: osdeps.open_path(self.log_file)),
                             ("reset_defaults", self._reset_defaults)):
            b = Gtk.Button(label=t(key))
            b.connect("clicked", lambda _, h=handler: h())
            files.pack_start(b, False, False, 0)
        grid.attach(files, 0, row, 2, 1)

        if osdeps.IS_WINDOWS:
            self.g_autostart.set_sensitive(False)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.add(grid)
        return scroller

    def _text_view(self, key):
        view = Gtk.TextView(monospace=True, wrap_mode=Gtk.WrapMode.NONE, top_margin=4, bottom_margin=4,
                            left_margin=6, right_margin=6)
        view.get_buffer().connect("changed", lambda buf: self._on_general(buf, key))
        frame = Gtk.Frame()
        scroller = Gtk.ScrolledWindow(min_content_height=70)
        scroller.add(view)
        frame.add(scroller)
        return _ViewHandle(view, frame)

    def _fill_general(self):
        self._loading = True
        d, s = self.data, self.data["session"]
        self.g_autostart.set_active(discovery.autostart_enabled())
        self.g_paused.set_active(bool(d.get("paused")))
        self.g_notifications.set_active(bool(d.get("notifications")))
        self.g_interval.set_value(float(d.get("check_interval", 2)))
        self.g_session_procs.set_text(", ".join(s.get("processes", [])))
        self.g_inhibit.set_active(bool(s.get("inhibit_idle")))
        self.g_pause.set_text(", ".join(s.get("pause_processes", [])))
        self.g_on_start.set_text("\n".join(s.get("on_start", [])))
        self.g_on_stop.set_text("\n".join(s.get("on_stop", [])))
        self._loading = False

    def _on_general(self, widget, *args):
        if self._loading:
            return
        key = args[-1]
        target, name = (self.data["session"], key.split(".", 1)[1]) if key.startswith("session.") \
            else (self.data, key)
        if isinstance(widget, Gtk.Switch):
            target[name] = widget.get_active()
        elif isinstance(widget, Gtk.SpinButton):
            target[name] = int(widget.get_value())
        elif isinstance(widget, Gtk.TextBuffer):
            text = widget.get_text(widget.get_start_iter(), widget.get_end_iter(), False)
            target[name] = [line.strip() for line in text.splitlines() if line.strip()]
        elif isinstance(widget, Gtk.Entry):
            target[name] = _split_list(widget.get_text())
        self._set_dirty()

    def _on_autostart(self, switch, _pspec):
        if self._loading:
            return
        try:
            discovery.set_autostart(switch.get_active())
        except OSError as e:
            self._show_toast(str(e), bad=True)

    def _reset_defaults(self):
        if ask(self, t("reset_confirm")) != Gtk.ResponseType.YES:
            return
        defaults = config.defaults()
        self.data.update(defaults)
        self.data["apps"] = [dict(copy.deepcopy(config.APP_DEFAULTS), **a) for a in defaults["apps"]]
        self._rebuild_list()
        self._fill_general()
        self._set_dirty()

    # --- live status / closing ---------------------------------------------------

    def _tick(self):
        names, session_up = self.engine.snapshot()
        parts = []
        for label, proc in (("SteamVR", "vrserver"), ("VRChat", "vrchat")):
            parts.append("%s %s %s" % ("●" if proc in names else "○", label,
                                       t("st_running") if proc in names else t("st_not_running")))
        _, status = self.engine.status()
        self.status_label.set_text("    ".join(parts) + "    —    " + status)

        phases = {name: phase for name, _, phase in self.engine.app_list()}
        for i in range(len(self.data.get("apps", []))):
            self._refresh_row(i, phases)
        if self.app is not None:
            self._update_running_status()

        # Changed from the tray (enable/disable, pause) while we have nothing unsaved: follow it.
        if not self._dirty and self._mtime() != self._file_mtime:
            self.load()
        return True

    def _on_delete(self, *_):
        if not self._dirty:
            return False
        dialog = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.QUESTION,
                                   text=t("unsaved_title"))
        dialog.format_secondary_text(t("unsaved_body"))
        dialog.add_buttons(t("discard"), Gtk.ResponseType.NO, t("cancel"), Gtk.ResponseType.CANCEL,
                           t("save"), Gtk.ResponseType.YES)
        response = dialog.run()
        dialog.destroy()
        if response == Gtk.ResponseType.YES:
            return not self.save()
        return response != Gtk.ResponseType.NO


class _ViewHandle:
    """A TextView plus its framed scroller, with plain get/set text."""

    def __init__(self, view, frame):
        self.view = view
        self.frame = frame

    def get_parent(self):
        return self.frame

    def set_text(self, text):
        self.view.get_buffer().set_text(text)
