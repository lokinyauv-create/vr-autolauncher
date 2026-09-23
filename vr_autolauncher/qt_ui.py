# -*- coding: utf-8 -*-
"""The whole user interface: tray icon + settings window, in Qt (PySide6).

One implementation for Linux and Windows, so both look and behave the same.
"""

import copy
import html
import logging
import os
import shlex
import stat

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QColor, QFont, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog,
                               QFileDialog, QFileIconProvider, QFormLayout, QFrame, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox,
                               QPlainTextEdit, QPushButton, QRadioButton, QSpinBox, QSplitter,
                               QStackedWidget, QSystemTrayIcon, QTabWidget, QToolButton,
                               QVBoxLayout, QWidget)

from vr_autolauncher import APP_ID, APP_NAME, config, discovery, osdeps
from vr_autolauncher.engine import (DONE, PENDING, ST_ACTIVE, ST_CLOSED, ST_DETECTED, ST_WAITING)
from vr_autolauncher.i18n import t

ICON_STYLE = {
    ST_WAITING: ("#4a90d9", "VR"),
    ST_DETECTED: ("#f5a623", "VR"),
    ST_ACTIVE: ("#27ae60", "OK"),
    ST_CLOSED: ("#e74c3c", "--"),
}
PHASE_MARK = {PENDING: "⏳ ", DONE: "✓ "}
PRESETS = [("steamvr", "trig_steamvr"), ("vrchat", "trig_vrchat"),
           ("steamvr+vrchat", "trig_both"), ("custom", "trig_custom")]

_icon_cache = {}


def state_icon(state):
    if state not in _icon_cache:
        color, text = ICON_STYLE[state]
        size = 128
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(color))
        painter.drawEllipse(4, 4, size - 8, size - 8)
        font = QFont()
        font.setBold(True)
        font.setPixelSize(int(size * 0.42))
        painter.setFont(font)
        painter.setPen(QColor("white"))
        painter.drawText(pixmap.rect(), Qt.AlignCenter, text)
        painter.end()
        _icon_cache[state] = QIcon(pixmap)
    return _icon_cache[state]


def save_icon_file(path, state=ST_WAITING, size=128):
    """Write the tray icon to a file (.ico/.png) - used by the installers."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return state_icon(state).pixmap(size, size).save(path)


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


def _dim(label):
    label.setStyleSheet("color: palette(mid);")
    return label


def _hint(text):
    label = _dim(QLabel(text))
    label.setWordWrap(True)
    return label


def _row(*widgets):
    """Lay widgets out side by side; a stretch factor comes from `(widget, factor)`."""
    box = QWidget()
    layout = QHBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    for widget in widgets:
        if isinstance(widget, tuple):
            layout.addWidget(widget[0], widget[1])
        elif widget is None:
            layout.addStretch(1)
        else:
            layout.addWidget(widget)
    return box


def _column(*widgets):
    box = QWidget()
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)
    for widget in widgets:
        layout.addWidget(widget)
    return box


# --- Pick-from-a-list dialog ------------------------------------------------------

class PickerDialog(QDialog):
    """rows: [(title, subtitle, value, QIcon|None)]"""

    def __init__(self, parent, title, rows, multi=False):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(520, 600)
        self.rows = rows

        self.search = QLineEdit(placeholderText=t("search"), clearButtonEnabled=True)
        self.search.textChanged.connect(self._filter)
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection if multi
                                   else QAbstractItemView.SingleSelection)
        self.list.setIconSize(self.list.iconSize().scaled(32, 32, Qt.KeepAspectRatio))
        self.list.itemDoubleClicked.connect(lambda _: self.accept())
        for row_title, subtitle, value, icon in rows:
            item = QListWidgetItem(row_title if not subtitle else "%s\n%s" % (row_title, subtitle))
            item.setData(Qt.UserRole, value)
            item.setData(Qt.UserRole + 1, ("%s %s" % (row_title, subtitle or "")).lower())
            if icon is not None:
                item.setIcon(icon)
            self.list.addItem(item)

        ok = QPushButton(t("pick").rstrip("…."), default=True)
        ok.clicked.connect(self.accept)
        cancel = QPushButton(t("cancel"))
        cancel.clicked.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self.search)
        layout.addWidget(self.list, 1)
        layout.addWidget(_row(None, cancel, ok))
        self.search.setFocus()

    def _filter(self, text):
        text = text.lower()
        for i in range(self.list.count()):
            item = self.list.item(i)
            item.setHidden(text not in item.data(Qt.UserRole + 1))

    def values(self):
        return [item.data(Qt.UserRole) for item in self.list.selectedItems()]


def pick_from_list(parent, title, rows, multi=False):
    dialog = PickerDialog(parent, title, rows, multi)
    if dialog.exec() == QDialog.Accepted:
        return dialog.values() or None
    return None


# --- Settings window ---------------------------------------------------------------

class AppRow(QWidget):
    """One line of the programs list: [x] name / trigger · delay          [✕ | ▶]

    The checkbox is the auto-start switch; the button force-closes a running program
    (✕) or starts a stopped one (▶) right now.
    """

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(48)
        self.check = QCheckBox()
        self.label = QLabel()
        self.label.setTextFormat(Qt.RichText)
        # Let clicks on the text fall through so they select the row.
        self.label.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.button = QToolButton()
        self.button.setAutoRaise(True)
        self.button.setFixedSize(34, 34)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 3, 6, 3)
        layout.addWidget(self.check)
        layout.addWidget(self.label, 1)
        layout.addWidget(self.button)

    def set_text(self, mark, name, detail):
        self.label.setText("<b>%s%s</b><br><span style='color: gray;'>%s</span>"
                           % (html.escape(mark), html.escape(name), html.escape(detail)))

    def set_running(self, running, known=True):
        """`known` is False for a program the engine doesn't have yet (unsaved)."""
        if running:
            self.button.setText("✖")
            self.button.setStyleSheet("QToolButton { color: #e74c3c; font-size: 18px; }")
            self.button.setToolTip(t("tip_close_now"))
        else:
            self.button.setText("▶")
            self.button.setStyleSheet("QToolButton { color: #27ae60; font-size: 16px; }")
            self.button.setToolTip(t("tip_start_now"))
        if not known:
            self.button.setToolTip(t("tip_save_first"))
        self.button.setEnabled(known)


class SettingsWindow(QWidget):
    def __init__(self, engine, log_file):
        super().__init__()
        self.engine = engine
        self.log_file = log_file
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(state_icon(ST_WAITING))
        self.resize(960, 660)

        self._loading = False
        self._dirty = False
        self._index = -1
        self._file_mtime = 0.0
        self._states = {}  # engine.app_states() by name: running / phase for each row
        self.data = {}

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_apps_tab(), t("tab_apps"))
        self.tabs.addTab(self._build_general_tab(), t("tab_general"))

        self.status_label = _dim(QLabel(""))
        self.toast = QLabel("")
        self.save_button = QPushButton(t("save"))
        self.save_button.setShortcut("Ctrl+S")
        self.save_button.clicked.connect(self.save)

        layout = QVBoxLayout(self)
        layout.addWidget(self.tabs, 1)
        layout.addWidget(_row(self.status_label, None, self.toast, self.save_button))

        self.load()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)
        self._tick()

    # --- data -------------------------------------------------------------------

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
        for key, value in raw.items():  # keep keys we don't know about
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
        self.save_button.setEnabled(dirty)
        self.setWindowTitle(("• " if dirty else "") + APP_NAME)

    def _show_toast(self, text, bad=False):
        self.toast.setStyleSheet("color: %s;" % ("#e74c3c" if bad else "#27ae60"))
        self.toast.setText(text)
        QTimer.singleShot(4000, lambda: self.toast.setText(""))

    @property
    def app(self):
        apps = self.data.get("apps", [])
        return apps[self._index] if 0 <= self._index < len(apps) else None

    # --- Programs tab -------------------------------------------------------------

    def _build_apps_tab(self):
        self.list = QListWidget()
        # Rows carry their own widgets (checkbox, text, ✕/▶): a light tint keeps their
        # colours readable, where the default solid highlight would swallow the grey text.
        self.list.setStyleSheet("QListWidget::item:selected { background: rgba(74, 144, 217, 55); }"
                                "QListWidget::item:hover { background: rgba(127, 127, 127, 30); }")
        self.list.currentRowChanged.connect(self._on_row_changed)

        add_button = QToolButton(text="+", toolTip=t("add"))
        add_menu = QMenu(add_button)
        for key, handler in (("add_steam", self._add_steam), ("add_desktop", self._add_desktop),
                             ("add_file", self._add_file), ("add_empty", self._add_empty)):
            add_menu.addAction(QAction(t(key), add_menu, triggered=handler))
        add_button.setMenu(add_menu)
        add_button.setPopupMode(QToolButton.InstantPopup)
        toolbar = [add_button]
        for text, tip, handler in (("−", "remove", self._remove), ("⧉", "duplicate", self._duplicate),
                                   ("↑", "move_up", lambda: self._move(-1)),
                                   ("↓", "move_down", lambda: self._move(1))):
            button = QToolButton(text=text, toolTip=t(tip))
            button.clicked.connect(handler)
            toolbar.append(button)

        left = _column(self.list, _row(*toolbar, None))
        left.layout().setStretch(0, 1)

        placeholder = QLabel(t("no_apps"), alignment=Qt.AlignCenter)
        self.form_stack = QStackedWidget()
        self.form_stack.addWidget(_dim(placeholder))
        self.form_stack.addWidget(self._build_form())

        splitter = QSplitter()
        splitter.addWidget(left)
        splitter.addWidget(self.form_stack)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([300, 660])
        return splitter

    def _build_form(self):
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        self.f_name = QLineEdit()
        self.f_name.textEdited.connect(lambda _: self._on_field("name"))
        self.f_enabled = QCheckBox(t("f_enabled"))
        self.f_enabled.toggled.connect(lambda _: self._on_field("enabled"))
        form.addRow(t("f_name"), _row((self.f_name, 1), self.f_enabled))

        self.f_trigger = QComboBox()
        for preset, key in PRESETS:
            self.f_trigger.addItem(t(key), preset)
        self.f_trigger.activated.connect(self._on_trigger_preset)
        form.addRow(t("f_trigger"), self.f_trigger)

        self.f_processes = QLineEdit()
        self.f_processes.textEdited.connect(lambda _: self._on_field("when"))
        pick = QPushButton(t("pick"))
        pick.clicked.connect(lambda: self._pick_processes(self.f_processes,
                                                          lambda: self._on_field("when")))
        self.f_mode_all = QRadioButton(t("trig_all"))
        self.f_mode_any = QRadioButton(t("trig_any"))
        self.f_mode_all.toggled.connect(lambda _: self._on_field("trigger"))
        self.custom_widget = _column(_row((self.f_processes, 1), pick),
                                     _row(self.f_mode_all, self.f_mode_any, None),
                                     _hint(t("processes_hint")))
        self.custom_label = QLabel(t("f_processes"))
        form.addRow(self.custom_label, self.custom_widget)

        self.f_delay = QSpinBox(maximum=600, suffix=" " + t("seconds_short"))
        self.f_delay.valueChanged.connect(lambda _: self._on_field("delay"))
        form.addRow(t("f_delay"), _row(self.f_delay, _dim(QLabel(t("delay_suffix"))), None))

        self.f_command = QLineEdit(placeholderText=t("command_hint"))
        self.f_command.textEdited.connect(lambda _: self._on_field("command"))
        self.cmd_status = QLabel("")
        buttons = []
        for key, handler in (("browse_file", self._browse_file), ("browse_steam", self._browse_steam),
                             ("browse_desktop", self._browse_desktop)):
            button = QPushButton(t(key))
            button.clicked.connect(handler)
            buttons.append(button)
        form.addRow(t("f_command"),
                    _column(self.f_command, _row(*buttons, None, self.cmd_status)))

        self.f_running = QLineEdit()
        self.f_running.textEdited.connect(lambda _: self._on_field("running"))
        pick_running = QPushButton(t("pick"))
        pick_running.clicked.connect(lambda: self._pick_processes(self.f_running,
                                                                  lambda: self._on_field("running")))
        guess = QPushButton(t("guess"))
        guess.clicked.connect(self._guess_running)
        self.running_status = _dim(QLabel(""))
        form.addRow(t("f_running"),
                    _column(_row((self.f_running, 1), pick_running, guess),
                            _row(self.running_status, None), _hint(t("running_hint"))))

        self.f_env = QLineEdit(placeholderText=t("env_hint"))
        self.f_env.textEdited.connect(lambda _: self._on_field("env"))
        form.addRow(t("f_env"), self.f_env)

        self.f_close = QCheckBox(t("f_close_on_exit"))
        self.f_close.toggled.connect(self._on_close_toggled)
        self.f_close_delay = QSpinBox(maximum=3600, suffix=" " + t("seconds_short"))
        self.f_close_delay.valueChanged.connect(lambda _: self._on_field("close_delay"))
        self.close_delay_label = _dim(QLabel(t("close_delay_label")))
        self.f_keep = QCheckBox(t("f_keep_alive"))
        self.f_keep.toggled.connect(lambda _: self._on_field("keep_alive"))
        form.addRow("", _column(self.f_close,
                                _row(self.close_delay_label, self.f_close_delay, None),
                                self.f_keep))

        test = QPushButton(t("test_launch"))
        test.clicked.connect(self._test_launch)
        self.test_result = _dim(QLabel(""))
        form.addRow("", _row(test, self.test_result, None))

        page = QWidget()
        page.setLayout(form)
        return page

    def _rebuild_list(self, select=0):
        self._loading = True
        self.list.clear()
        for app in self.data["apps"]:
            item = QListWidgetItem("")
            row = AppRow()
            row.check.toggled.connect(lambda on, it=item: self._on_row_enabled(it, on))
            row.button.clicked.connect(lambda _=False, it=item: self._on_row_button(it))
            self.list.addItem(item)
            self.list.setItemWidget(item, row)
            item.setSizeHint(row.sizeHint())
        self._loading = False
        for i in range(len(self.data["apps"])):
            self._refresh_row(i)
        if self.list.count():
            self.list.setCurrentRow(max(0, min(select, self.list.count() - 1)))
        else:
            self._index = -1
            self._update_form_visibility()

    def _refresh_row(self, i, states=None):
        if i >= self.list.count():
            return
        states = self._states if states is None else states
        app = self.data["apps"][i]
        row = self.list.itemWidget(self.list.item(i))
        state = states.get(app.get("name"))
        mark = PHASE_MARK.get(state["phase"], "") if state else ""
        preset = _preset_for(app.get("when", []))
        if preset == "custom":
            joiner = " + " if app.get("trigger", "all") == "all" else " / "
            trigger = joiner.join(app.get("when", [])) or "—"
        else:
            trigger = t(dict(PRESETS)[preset])
        row.set_text(mark, app.get("name") or t("new_app"),
                     "%s · %s" % (trigger, t("summary_delay", n=int(float(app.get("delay", 0))))))
        row.check.blockSignals(True)
        row.check.setChecked(bool(app.get("enabled", True)))
        row.check.blockSignals(False)
        row.set_running(bool(state and state["running"]), known=state is not None)

    def _update_form_visibility(self):
        has = self.app is not None
        self.form_stack.setCurrentIndex(1 if has else 0)
        if has:
            custom = self.f_trigger.currentData() == "custom"
            self.custom_widget.setVisible(custom)
            self.custom_label.setVisible(custom)

    def _on_row_changed(self, row):
        self._index = row
        self._fill_form()

    def _on_row_enabled(self, item, enabled):
        if self._loading:
            return
        i = self.list.row(item)
        self.data["apps"][i]["enabled"] = enabled
        if i == self._index:
            self._loading = True
            self.f_enabled.setChecked(enabled)
            self._loading = False
        self._set_dirty()

    def _on_row_button(self, item):
        """✕ / ▶ on a row: force-close the program, or start it."""
        name = self.data["apps"][self.list.row(item)].get("name", "")
        result = self.engine.toggle_running(name)
        if result is None:
            self._show_toast(t("tip_save_first"), bad=True)
        elif result == "closed":
            self._show_toast(t("app_force_closed", app=name))
        else:
            self._show_toast(t("app_" + result, app=name), bad=result in ("missing", "failed"))
        self._tick()

    def _fill_form(self):
        app = self.app
        self._update_form_visibility()
        if app is None:
            return
        self._loading = True
        self.f_name.setText(app.get("name", ""))
        self.f_enabled.setChecked(bool(app.get("enabled", True)))
        self.f_trigger.setCurrentIndex(self.f_trigger.findData(_preset_for(app.get("when", []))))
        self.f_processes.setText(", ".join(app.get("when", [])))
        (self.f_mode_any if app.get("trigger") == "any" else self.f_mode_all).setChecked(True)
        self.f_delay.setValue(int(float(app.get("delay", 0))))
        self.f_command.setText(app.get("command", ""))
        self.f_running.setText(", ".join(app.get("running", [])))
        self.f_env.setText(_env_to_text(app.get("env")))
        self.f_close.setChecked(bool(app.get("close_on_exit")))
        self.f_close_delay.setValue(int(float(app.get("close_delay", 0))))
        self._update_close_delay_enabled()
        self.f_keep.setChecked(bool(app.get("keep_alive")))
        self.test_result.setText("")
        self._loading = False
        self._update_form_visibility()
        self._update_command_status()

    def _on_field(self, key):
        app = self.app
        if self._loading or app is None:
            return
        if key == "name":
            app["name"] = self.f_name.text()
        elif key == "enabled":
            app["enabled"] = self.f_enabled.isChecked()
        elif key == "when":
            app["when"] = _split_list(self.f_processes.text())
        elif key == "trigger":
            app["trigger"] = "all" if self.f_mode_all.isChecked() else "any"
        elif key == "delay":
            app["delay"] = self.f_delay.value()
        elif key == "command":
            app["command"] = self.f_command.text()
            self._update_command_status()
        elif key == "running":
            app["running"] = _split_list(self.f_running.text())
            self._update_running_status()
        elif key == "env":
            app["env"] = _text_to_env(self.f_env.text())
        elif key in ("close_on_exit", "keep_alive"):
            app[key] = (self.f_close if key == "close_on_exit" else self.f_keep).isChecked()
        elif key == "close_delay":
            app["close_delay"] = self.f_close_delay.value()
        self._refresh_row(self._index)
        self._set_dirty()

    def _on_close_toggled(self, _checked):
        self._update_close_delay_enabled()
        self._on_field("close_on_exit")

    def _update_close_delay_enabled(self):
        """The exit delay only means something when we close the program at all."""
        on = self.f_close.isChecked()
        self.f_close_delay.setEnabled(on)
        self.close_delay_label.setEnabled(on)

    def _on_trigger_preset(self, _index):
        app = self.app
        if self._loading or app is None:
            return
        preset = self.f_trigger.currentData()
        if preset in config.TRIGGER_PRESETS:
            app["when"] = list(config.TRIGGER_PRESETS[preset])
            app["trigger"] = "all"
            self._loading = True
            self.f_processes.setText(", ".join(app["when"]))
            self.f_mode_all.setChecked(True)
            self._loading = False
        self._update_form_visibility()
        if preset == "custom":
            self.f_processes.setFocus()
        self._refresh_row(self._index)
        self._set_dirty()

    def _update_command_status(self):
        command = self.f_command.text().strip()
        if not command:
            self.cmd_status.setText("")
            return
        if osdeps.is_url(command):
            text, ok = t("cmd_url"), True
        else:
            try:
                ok = osdeps.expand_command(command) is not None
            except ValueError:
                ok = False
            text = t("cmd_ok") if ok else t("cmd_missing")
        self.cmd_status.setText(text)
        self.cmd_status.setStyleSheet("color: %s;" % ("#27ae60" if ok else "#e74c3c"))
        self._update_running_status()

    def _update_running_status(self):
        patterns = [p.lower() for p in _split_list(self.f_running.text())]
        if not patterns:
            self.running_status.setText("")
            return
        running = any(any(p in proc.names or p in proc.path for p in patterns)
                      for proc in self.engine.last_processes())
        self.running_status.setText("● " + (t("st_running") if running else t("st_not_running")))

    def _guess_running(self):
        self.f_running.setText(discovery.suggest_running(self.f_command.text()))
        self._on_field("running")

    def _test_launch(self):
        if self.app is not None:
            self.test_result.setText(t("result_" + self.engine.test_launch(self.app)))

    # --- adding / pickers ---------------------------------------------------------

    def _new_app(self, **fields):
        app = copy.deepcopy(config.APP_DEFAULTS)
        app["name"] = t("new_app")
        app.update(fields)
        self.data["apps"].append(app)
        self._rebuild_list(select=len(self.data["apps"]) - 1)
        self._set_dirty()

    def _add_empty(self):
        self._new_app()
        self.f_name.setFocus()

    def _add_steam(self):
        game = self._pick_steam()
        if game:
            self._new_app(name=game.name, command=game.command, running=[game.installdir.lower()])

    def _add_desktop(self):
        app = self._pick_installed()
        if app:
            self._new_app(name=app.name, command=app.command,
                          running=[r for r in [discovery.suggest_running(app.command)] if r])

    def _add_file(self):
        path = self._pick_file()
        if path:
            command = shlex.quote(path) if not osdeps.IS_WINDOWS else '"%s"' % path
            self._new_app(name=os.path.basename(path).split(".AppImage")[0], command=command,
                          running=[r for r in [discovery.suggest_running(command)] if r])

    def _apply_picked(self, name, command, running):
        app = self.app
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

    def _browse_file(self):
        path = self._pick_file()
        if path:
            command = shlex.quote(path) if not osdeps.IS_WINDOWS else '"%s"' % path
            self._apply_picked(os.path.basename(path).split(".AppImage")[0], command,
                               discovery.suggest_running(command))

    def _browse_steam(self):
        game = self._pick_steam()
        if game:
            self._apply_picked(game.name, game.command, game.installdir.lower())

    def _browse_desktop(self):
        app = self._pick_installed()
        if app:
            self._apply_picked(app.name, app.command, discovery.suggest_running(app.command))

    def _pick_steam(self):
        rows = []
        for game in discovery.steam_games():
            icon = QIcon.fromTheme("steam_icon_" + game.appid)
            if icon.isNull():
                icon = QIcon.fromTheme("applications-games")
            rows.append((game.name, game.command, game, icon))
        picked = pick_from_list(self, t("pick_steam"), rows)
        return picked[0] if picked else None

    def _pick_installed(self):
        provider = QFileIconProvider()
        rows = []
        for app in discovery.installed_apps():
            icon = None
            if app.icon and os.path.isabs(app.icon) and os.path.exists(app.icon):
                icon = provider.icon(app.icon) if osdeps.IS_WINDOWS else QIcon(app.icon)
            elif app.icon:
                icon = QIcon.fromTheme(app.icon)
            rows.append((app.name, app.command, app, icon))
        picked = pick_from_list(self, t("pick_desktop"), rows)
        return picked[0] if picked else None

    def _pick_file(self):
        start = os.path.expanduser("~/Applications")
        path, _filter = QFileDialog.getOpenFileName(
            self, t("pick_file"), start if os.path.isdir(start) else os.path.expanduser("~"))
        if path and not osdeps.IS_WINDOWS and not os.access(path, os.X_OK):
            if QMessageBox.question(self, APP_NAME, "%s\n\n%s" % (t("not_executable"), path)) \
                    == QMessageBox.Yes:
                try:
                    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                except OSError as e:
                    self._show_toast(str(e), bad=True)
        return path

    def _pick_processes(self, entry, on_change=None):
        """Add processes chosen from the running ones to `entry`, then save them."""
        rows = [(name, None, name, None) for name in discovery.running_process_names()]
        picked = pick_from_list(self, t("pick_process"), rows, multi=True)
        if picked:
            entry.setText(", ".join(dict.fromkeys(_split_list(entry.text()) + picked)))
            if on_change:
                on_change()

    def _remove(self):
        if self.app is not None:
            del self.data["apps"][self._index]
            self._rebuild_list(select=self._index)
            self._set_dirty()

    def _duplicate(self):
        if self.app is not None:
            clone = copy.deepcopy(self.app)
            clone["name"] = clone.get("name", "") + " (2)"
            self.data["apps"].insert(self._index + 1, clone)
            self._rebuild_list(select=self._index + 1)
            self._set_dirty()

    def _move(self, delta):
        i, j = self._index, self._index + delta
        apps = self.data["apps"]
        if self.app is not None and 0 <= j < len(apps):
            apps[i], apps[j] = apps[j], apps[i]
            self._rebuild_list(select=j)
            self._set_dirty()

    # --- General tab ---------------------------------------------------------------

    def _build_general_tab(self):
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        def section(key):
            label = QLabel("<b>%s</b>" % t(key))
            form.addRow(label)

        section("g_behaviour")
        self.g_autostart = QCheckBox(t("g_autostart"))
        self.g_autostart.toggled.connect(self._on_autostart)
        form.addRow("", self.g_autostart)
        self.g_paused = QCheckBox(t("g_paused"))
        self.g_paused.toggled.connect(lambda _: self._on_general("paused"))
        form.addRow("", self.g_paused)
        self.g_notifications = QCheckBox(t("g_notifications"))
        self.g_notifications.toggled.connect(lambda _: self._on_general("notifications"))
        form.addRow("", self.g_notifications)
        self.g_interval = QSpinBox(minimum=1, maximum=60, suffix=" " + t("seconds_short"))
        self.g_interval.valueChanged.connect(lambda _: self._on_general("check_interval"))
        form.addRow(t("g_interval"), _row(self.g_interval, None))

        section("g_session")
        self.g_session_procs = QLineEdit()
        self.g_session_procs.textEdited.connect(lambda _: self._on_general("session.processes"))
        pick = QPushButton(t("pick"))
        pick.clicked.connect(lambda: self._pick_processes(
            self.g_session_procs, lambda: self._on_general("session.processes")))
        form.addRow(t("g_session_trigger"),
                    _column(_row((self.g_session_procs, 1), pick), _hint(t("processes_hint"))))
        self.g_inhibit = QCheckBox(t("g_inhibit"))
        self.g_inhibit.toggled.connect(lambda _: self._on_general("session.inhibit_idle"))
        form.addRow("", self.g_inhibit)
        self.g_pause = QLineEdit()
        self.g_pause.textEdited.connect(lambda _: self._on_general("session.pause_processes"))
        pick_pause = QPushButton(t("pick"))
        pick_pause.clicked.connect(lambda: self._pick_processes(
            self.g_pause, lambda: self._on_general("session.pause_processes")))
        form.addRow(t("g_pause_procs"),
                    _column(_row((self.g_pause, 1), pick_pause), _hint(t("g_pause_hint"))))
        self.g_on_start = QPlainTextEdit(maximumHeight=80)
        self.g_on_start.textChanged.connect(lambda: self._on_general("session.on_start"))
        form.addRow(t("g_on_start"), _column(self.g_on_start, _hint(t("commands_hint"))))
        self.g_on_stop = QPlainTextEdit(maximumHeight=80)
        self.g_on_stop.textChanged.connect(lambda: self._on_general("session.on_stop"))
        form.addRow(t("g_on_stop"), _column(self.g_on_stop, _hint(t("commands_hint"))))

        section("g_files")
        buttons = []
        for key, handler in (("open_config", lambda: osdeps.open_path(self.engine.config_path)),
                             ("open_log", lambda: osdeps.open_path(self.log_file)),
                             ("reset_defaults", self._reset_defaults)):
            button = QPushButton(t(key))
            button.clicked.connect(handler)
            buttons.append(button)
        form.addRow("", _row(*buttons, None))

        if osdeps.IS_WINDOWS:
            self.g_inhibit.setEnabled(False)
            self.g_pause.setEnabled(False)

        page = QWidget()
        page.setLayout(form)
        return page

    def _fill_general(self):
        self._loading = True
        data, session = self.data, self.data["session"]
        self.g_autostart.setChecked(discovery.autostart_enabled())
        self.g_paused.setChecked(bool(data.get("paused")))
        self.g_notifications.setChecked(bool(data.get("notifications")))
        self.g_interval.setValue(int(data.get("check_interval", 2)))
        self.g_session_procs.setText(", ".join(session.get("processes", [])))
        self.g_inhibit.setChecked(bool(session.get("inhibit_idle")))
        self.g_pause.setText(", ".join(session.get("pause_processes", [])))
        self.g_on_start.setPlainText("\n".join(session.get("on_start", [])))
        self.g_on_stop.setPlainText("\n".join(session.get("on_stop", [])))
        self._loading = False

    def _on_general(self, key):
        if self._loading:
            return
        target, name = (self.data["session"], key.split(".", 1)[1]) if key.startswith("session.") \
            else (self.data, key)
        widgets = {
            "paused": self.g_paused, "notifications": self.g_notifications,
            "check_interval": self.g_interval, "processes": self.g_session_procs,
            "inhibit_idle": self.g_inhibit, "pause_processes": self.g_pause,
            "on_start": self.g_on_start, "on_stop": self.g_on_stop,
        }
        widget = widgets[name]
        if isinstance(widget, QCheckBox):
            target[name] = widget.isChecked()
        elif isinstance(widget, QSpinBox):
            target[name] = widget.value()
        elif isinstance(widget, QPlainTextEdit):
            target[name] = [line.strip() for line in widget.toPlainText().splitlines() if line.strip()]
        else:
            target[name] = _split_list(widget.text())
        self._set_dirty()

    def _on_autostart(self, enabled):
        if self._loading:
            return
        try:
            discovery.set_autostart(enabled)
        except OSError as e:
            self._show_toast(str(e), bad=True)

    def _reset_defaults(self):
        if QMessageBox.question(self, APP_NAME, t("reset_confirm")) != QMessageBox.Yes:
            return
        defaults = config.defaults()
        self.data.update(copy.deepcopy(defaults))
        self.data["apps"] = [dict(copy.deepcopy(config.APP_DEFAULTS), **a) for a in defaults["apps"]]
        self._rebuild_list()
        self._fill_general()
        self._set_dirty()

    # --- live status / closing -------------------------------------------------------

    def _tick(self):
        names, _session_up = self.engine.snapshot()
        parts = ["%s %s %s" % ("●" if proc in names else "○", label,
                               t("st_running") if proc in names else t("st_not_running"))
                 for label, proc in (("SteamVR", "vrserver"), ("VRChat", "vrchat"))]
        self.status_label.setText("    ".join(parts) + "    —    " + self.engine.status()[1])

        self._states = {s["name"]: s for s in self.engine.app_states()}
        for i in range(len(self.data.get("apps", []))):
            self._refresh_row(i)
        if self.app is not None:
            self._update_running_status()
        # Changed from the tray while we have nothing unsaved: follow it.
        if not self._dirty and self._mtime() != self._file_mtime:
            self.load()

    def closeEvent(self, event):
        if not self._dirty:
            event.accept()
            return
        box = QMessageBox(QMessageBox.Question, t("unsaved_title"), t("unsaved_body"), parent=self)
        save = box.addButton(t("save"), QMessageBox.AcceptRole)
        discard = box.addButton(t("discard"), QMessageBox.DestructiveRole)
        box.addButton(t("cancel"), QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is save:
            event.setAccepted(self.save())
        elif box.clickedButton() is discard:
            event.accept()
        else:
            event.ignore()


# --- Tray + application -------------------------------------------------------------

class QtUI:
    def __init__(self, engine, log_file):
        self.engine = engine
        self.log_file = log_file
        self.app = QApplication.instance() or QApplication([])
        self.app.setApplicationName(APP_NAME)
        self.app.setQuitOnLastWindowClosed(False)
        QGuiApplication.setDesktopFileName(APP_ID)

        self.window = None
        self._shown = None
        self._apps_shown = None

        self.tray = QSystemTrayIcon(state_icon(ST_WAITING))
        self.menu = QMenu()
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()
        self._build_menu()
        # Right after login the panel (on GNOME: the AppIndicator extension) may not be
        # up yet. Don't give up then - _tick() shows the icon as soon as it appears, and
        # the settings window works without it in the meantime.
        self._tray_ready = QSystemTrayIcon.isSystemTrayAvailable()
        if not self._tray_ready:
            logging.warning("System tray not available yet; will show the icon once it is")

        self.timer = QTimer()
        self.timer.timeout.connect(self._tick)
        self.timer.start(1000)

    # --- menu ---------------------------------------------------------------------

    def _build_menu(self):
        self.menu.clear()
        self.status_action = self.menu.addAction("")
        self.status_action.setEnabled(False)
        self.menu.addAction(QAction(t("menu_settings"), self.menu, triggered=self.open_settings))
        self.pause_action = QAction(t("menu_pause"), self.menu, checkable=True,
                                    checked=self.engine.paused)
        self.pause_action.toggled.connect(self.engine.set_paused)
        self.menu.addAction(self.pause_action)
        self.menu.addSeparator()

        apps = self.engine.app_states()
        self._apps_shown = apps
        header = self.menu.addAction(t("menu_apps"))
        header.setEnabled(False)
        self.app_actions = {}
        for app in apps:
            action = QAction(PHASE_MARK.get(app["phase"], "") + app["name"], self.menu,
                             checkable=True, checked=app["enabled"])
            action.toggled.connect(lambda on, n=app["name"]: self.engine.set_enabled(n, on))
            self.menu.addAction(action)
            self.app_actions[app["name"]] = action
        if apps:
            # Plain actions only: on GNOME the tray menu goes through dbusmenu, which
            # can't show buttons inside a row.
            self.menu.addSeparator()
            now = self.menu.addAction(t("menu_now"))
            now.setEnabled(False)
            for app in apps:
                key = "menu_close_app" if app["running"] else "menu_start_app"
                self.menu.addAction(QAction(t(key, app=app["name"]), self.menu,
                                            triggered=lambda _=False, n=app["name"]:
                                            self.engine.toggle_running(n)))
        self.menu.addSeparator()
        self.menu.addAction(QAction(t("menu_open_log"), self.menu,
                                    triggered=lambda: osdeps.open_path(self.log_file)))
        self.menu.addAction(QAction(t("menu_quit"), self.menu, triggered=self.quit))

    def _on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.open_settings()

    # --- window -------------------------------------------------------------------

    def open_settings(self):
        if self.window is None:
            self.window = SettingsWindow(self.engine, self.log_file)
            self.window.destroyed.connect(self._on_window_closed)
            self.window.setAttribute(Qt.WA_DeleteOnClose)
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def _on_window_closed(self):
        self.window = None

    # --- polling ------------------------------------------------------------------

    def _tick(self):
        if not self._tray_ready and QSystemTrayIcon.isSystemTrayAvailable():
            self._tray_ready = True
            self.tray.hide()
            self.tray.show()  # register again, now that there is someone to register with
            logging.info("System tray appeared, icon shown")
        if osdeps.take_show_request():
            self.open_settings()
        state, text = self.engine.status()
        if (state, text) != self._shown:
            self._shown = (state, text)
            self.tray.setIcon(state_icon(state))
            self.tray.setToolTip("%s\n%s" % (APP_NAME, text))
            self.status_action.setText(text)
        apps = self.engine.app_states()
        if apps != self._apps_shown:
            self._build_menu()
            self.status_action.setText(self._shown[1] if self._shown else "")
        if self.pause_action.isChecked() != self.engine.paused:
            self.pause_action.setChecked(self.engine.paused)

    def run(self):
        self.app.exec()

    def quit(self):
        self.engine.stop()
        if self.window is not None:
            self.window.close()
        self.tray.hide()
        self.app.quit()
