import os
import sys
import functools
import json
import time
import socket
import platform
from pathlib import Path
from datetime import datetime

import psutil
from PySide6.QtCore import (Qt, QTimer, QThread, Signal, QUrl, QSettings, QEvent, QFileSystemWatcher, QProcess, QObject, QSize)
from PySide6.QtGui import (QFont, QDesktopServices, QColor, QKeySequence, QShortcut, QGuiApplication, QIcon,
                           QImageReader, QPixmap, QFontDatabase)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QStackedWidget, QLineEdit, QFileDialog, QTableWidget, QMessageBox, QTextEdit, QHeaderView,
    QSpinBox, QAbstractItemView, QComboBox, QCheckBox, QDialog, QListWidget, QGridLayout, QMenu,
    QSystemTrayIcon, QScrollArea, QButtonGroup
)

import core
import crash
import db
import i18n
import themes
from core import fmt_bytes
from i18n import tr
from widgets import (Ring, BarList, Card, StatCard, TwoLineDelegate, BandDelegate, IntField, SUB_ROLE, swatch_icon, Toast, DropLineEdit,
                     TLabel, TButton, TCheck, SortItem, SORT_ROLE, build_arrows, make_app_pixmap)

APP_NAME = "Smart File Manager Pro"
VERSION = "4.2"
LOG_DIR = Path.home() / ".smart_file_manager_pro"
LOG_FILE = LOG_DIR / "activity.log"                # legacy (imported into SQLite once)
JOURNAL_FILE = LOG_DIR / "undo_journal.json"       # legacy (imported into SQLite once)
DB_FILE = LOG_DIR / "sfm.db"
MAX_LOG_LINES = 500

# Sidebar: the seven main tools are always on the rail; every other page opens from the "..." tile (and Ctrl+K).
RAIL_KEYS = ("dashboard", "organizer", "disk", "dups", "cleanup", "rename", "backup")
MORE_KEYS = ("similar", "search", "procs", "network", "system", "log")


def load_bundled_fonts():
    """Register fonts/*.ttf so the UI looks the same on every PC (works from source and PyInstaller)."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    for f in sorted((base / "fonts").glob("*.ttf")):
        QFontDatabase.addApplicationFont(str(f))


class Worker(QThread):
    """Runs fn(*args, cancel=..., progress=...) off the UI thread."""
    progress = Signal(str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, *args):
        super().__init__()
        self.fn, self.args, self._cancel = fn, args, False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            self.done.emit(self.fn(*self.args, cancel=lambda: self._cancel, progress=self.progress.emit))
        except Exception as exc:
            self.failed.emit(str(exc))


class Palette(QDialog):
    """Ctrl+K quick switcher."""

    def __init__(self, parent, items, search_cb=None):
        super().__init__(parent, Qt.Dialog | Qt.FramelessWindowHint)
        self.items, self.search_cb, self.extra = items, search_cb, {}
        self.setFixedSize(460, 360)
        lay = QVBoxLayout(self)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(tr("Go to page…"))
        self.list = QListWidget()
        lay.addWidget(self.edit)
        lay.addWidget(self.list)
        self.edit.textChanged.connect(self.refill)
        self.edit.installEventFilter(self)
        self.list.itemActivated.connect(lambda _i: self.accept())
        self.refill("")

    def refill(self, text):
        self.list.clear()
        self.extra = {}
        for title, _ in self.items:
            if text.lower() in title.lower():
                self.list.addItem(title)
        q = text.strip()
        if q and self.search_cb:
            label = tr("Search files for “{q}”", q=q)
            self.extra[label] = lambda: self.search_cb(q)
            self.list.addItem(label)
        if self.list.count():
            self.list.setCurrentRow(0)

    def eventFilter(self, obj, ev):
        if obj is self.edit and ev.type() == QEvent.KeyPress:
            k = ev.key()
            if k in (Qt.Key_Down, Qt.Key_Up):
                row = self.list.currentRow() + (1 if k == Qt.Key_Down else -1)
                self.list.setCurrentRow(max(0, min(self.list.count() - 1, row)))
                return True
            if k in (Qt.Key_Return, Qt.Key_Enter):
                if self.list.currentItem():
                    self.accept()
                return True
        return super().eventFilter(obj, ev)

    def chosen(self):
        it = self.list.currentItem()
        if it and it.text() in self.extra:
            return self.extra[it.text()]
        return next((cb for t, cb in self.items if it and t == it.text()), None)


class RulesDialog(QDialog):
    """Edit custom organizer rules: pattern -> folder."""

    def __init__(self, parent, rules):
        super().__init__(parent)
        self.setWindowTitle(tr("Custom rules"))
        self.resize(560, 420)
        lay = QVBoxLayout(self)
        d = TLabel("Files whose name matches a pattern go to that folder. Use wildcards (*.psd, report_*) or part of a name. "
                   "The first matching rule wins; other files use the built-in categories.")
        d.setObjectName("muted")
        d.setWordWrap(True)
        lay.addWidget(d)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels([tr("Pattern"), tr("Folder")])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        lay.addWidget(self.table, 1)
        for pat, folder in rules:
            self.add_row(pat, folder)
        row = QHBoxLayout()
        add = TButton("Add rule")
        add.clicked.connect(lambda: self.add_row("", ""))
        rm = TButton("Remove selected")
        rm.clicked.connect(self.remove_rows)
        ok = TButton("Save")
        ok.setObjectName("primary")
        ok.clicked.connect(self.accept)
        cancel = TButton("Cancel")
        cancel.clicked.connect(self.reject)
        for b in (add, rm):
            row.addWidget(b)
        row.addStretch()
        row.addWidget(cancel)
        row.addWidget(ok)
        lay.addLayout(row)

    def add_row(self, pat, folder):
        from PySide6.QtWidgets import QTableWidgetItem
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, 0, QTableWidgetItem(pat))
        self.table.setItem(r, 1, QTableWidgetItem(folder))
        self.table.editItem(self.table.item(r, 0))

    def remove_rows(self):
        for r in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(r)

    def rules(self):
        out = []
        for r in range(self.table.rowCount()):
            a, b = self.table.item(r, 0), self.table.item(r, 1)
            out.append({"pattern": a.text() if a else "", "folder": b.text() if b else ""})
        return out


def make_table(headers, stretch_cols=(0,), sortable=True, bare=False, row_h=0, header=True):
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels([tr(h) for h in headers])
    for c in range(len(headers)):
        mode = QHeaderView.Stretch if c in stretch_cols else QHeaderView.ResizeToContents
        t.horizontalHeader().setSectionResizeMode(c, mode)
    t.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.setAlternatingRowColors(False)
    t.verticalHeader().setVisible(False)
    t.horizontalHeader().setVisible(header)
    t.horizontalHeader().setHighlightSections(False)
    t.setShowGrid(False)
    t.setFrameShape(QFrame.NoFrame)
    t.setFocusPolicy(Qt.NoFocus)
    if bare:
        t.setProperty("bare", True)
    if row_h:
        t.verticalHeader().setDefaultSectionSize(row_h)
    t.setSortingEnabled(sortable)
    if sortable:
        t.horizontalHeader().setSortIndicator(-1, Qt.AscendingOrder)     # no arrow until the user clicks a header
    return t


def fill_table(table, rows, checkable=False, checked=True, userdata=None, checks=None):
    """rows: list of lists. A cell may be (text, sort_key). userdata[r] is stored on column 0."""
    sorting = table.isSortingEnabled()
    table.setSortingEnabled(False)
    table.setUpdatesEnabled(False)
    table.setRowCount(len(rows))
    for r, row in enumerate(rows):
        for c, val in enumerate(row):
            text, key = val if isinstance(val, tuple) else (val, None)
            item = SortItem(str(text))
            item.setToolTip(str(text))
            if key is not None:
                item.setData(SORT_ROLE, key)
            if c == 0:
                if checkable:
                    item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                    on = checks[r] if checks is not None else checked
                    item.setCheckState(Qt.Checked if on else Qt.Unchecked)
                if userdata is not None:
                    item.setData(Qt.UserRole, userdata[r])
            table.setItem(r, c, item)
    table.setUpdatesEnabled(True)
    table.setSortingEnabled(sorting)


def set_all_checked(table, state):
    for r in range(table.rowCount()):
        it = table.item(r, 0)
        if it is not None:
            it.setCheckState(Qt.Checked if state else Qt.Unchecked)


def add_items(combo, items):
    """Show translated text but keep the English value as item data (logic reads currentData())."""
    for it in items:
        combo.addItem(tr(it), it)


def open_location(path):
    p = Path(path)
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(p.parent if p.exists() else p)))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = QSettings("SmartFileManagerPro", "SmartFileManagerPro")
        i18n.set_lang(self.settings.value("lang", "en", type=str))
        themes.set_theme(self.settings.value("theme", "Light", type=str))
        is_th = i18n.lang == "th"
        themes.defaults.update(family=themes.BUNDLED, size=11 if is_th else 10)
        themes.set_font(self.settings.value("font_family", "", type=str),
                        self.settings.value("font_size", themes.defaults["size"], type=int))
        self.setWindowTitle(APP_NAME)
        self.resize(1280, 800)
        self.setMinimumSize(1020, 680)
        self.workers, self.logs = {}, []
        self.db = db.Database(DB_FILE)
        self.db.migrate_legacy(JOURNAL_FILE, LOG_FILE, self.legacy_rules())
        self.undo_stack = self.db.load_undo_stack()
        self.rn_files, self.backup_plan, self.disk_root, self.dup_root = [], None, None, None
        self.watch_dir, self._quitting, self.tray = None, False, None
        self.rules = self.load_rules()
        self.local_ip = core.local_ip()
        self.health = None
        self.load_logs()

        root = QWidget()
        root.setObjectName("root")
        body = QHBoxLayout(root)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        # ---- slim icon rail: logo on top, the main tools, "more" tile, settings at the bottom
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(76)
        self.sidebar = sidebar
        sl = QVBoxLayout(sidebar)
        sl.setContentsMargins(16, 16, 16, 16)
        sl.setSpacing(6)
        logo = QLabel("SF")
        logo.setObjectName("logo")
        logo.setFixedSize(44, 44)
        logo.setAlignment(Qt.AlignCenter)
        logo.setToolTip(f"{APP_NAME}  v{VERSION}")
        sl.addWidget(logo)
        sl.addSpacing(10)

        self.stack = QStackedWidget()
        self.nav_buttons, self.page_keys, self.page_titles, self.nav_by_key = [], [], {}, {}
        pages = [
            ("dashboard", "⌂", "Dashboard", self.make_dashboard),
            ("organizer", "▤", "File Organizer", self.make_organizer),
            ("disk", "◉", "Disk Analyzer", self.make_disk),
            ("dups", "⧉", "Duplicate Finder", self.make_dups),
            ("similar", "▣", "Similar Photos", self.make_similar),
            ("cleanup", "✧", "Cleanup", self.make_cleanup),
            ("search", "⌕", "File Search", self.make_search),
            ("rename", "✎", "Bulk Renamer", self.make_renamer),
            ("backup", "⟳", "Backup", self.make_backup),
            ("procs", "▦", "Processes", self.make_procs),
            ("network", "⌁", "Network Tools", self.make_network),
            ("system", "⚙", "System Info", self.make_system),
            ("log", "☷", "Activity Log", self.make_log),
            ("settings", "✦", "Settings", self.make_settings),
        ]
        self.page_icons = {}
        for idx, (key, icon, title, builder) in enumerate(pages):
            self.page_keys.append(key)
            self.page_titles[key] = title
            self.page_icons[key] = icon
            self.stack.addWidget(builder())
            QShortcut(QKeySequence(f"Ctrl+{idx + 1}" if idx < 9 else f"Ctrl+Shift+{idx - 8}"),
                      self, activated=lambda i=idx: self.change_page(i))

        def rail_button(icon, tip):
            btn = QPushButton(icon)
            btn.setObjectName("navbtn")
            btn.setCheckable(True)
            btn.setFixedSize(44, 44)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setToolTip(tip)
            return btn

        for key in RAIL_KEYS:
            btn = rail_button(self.page_icons[key], tr(self.page_titles[key]))
            btn.clicked.connect(lambda checked=False, k=key: self.goto(k))
            sl.addWidget(btn)
            self.nav_buttons.append(btn)
            self.nav_by_key[key] = btn
        self.more_btn = rail_button("⋯", tr("More tools"))
        self.more_btn.clicked.connect(self.show_more_menu)
        sl.addWidget(self.more_btn)
        sl.addStretch()
        self.settings_btn = rail_button(self.page_icons["settings"], tr("Settings"))
        self.settings_btn.clicked.connect(lambda: self.goto("settings"))
        sl.addWidget(self.settings_btn)
        self.nav_by_key["settings"] = self.settings_btn

        content = QWidget()
        cl = QVBoxLayout(content)
        cl.setContentsMargins(36, 0, 36, 0)
        cl.setSpacing(0)
        cl.addWidget(self.stack)
        body.addWidget(sidebar)
        body.addWidget(content, 1)

        self.setCentralWidget(root)
        self.toast = Toast(root)
        QShortcut(QKeySequence("Ctrl+K"), self, activated=self.open_palette)

        self.watcher = QFileSystemWatcher(self)
        self.watch_debounce = QTimer(self)
        self.watch_debounce.setSingleShot(True)
        self.watch_debounce.setInterval(5000)
        self.watch_debounce.timeout.connect(self.auto_organize)
        self.watcher.directoryChanged.connect(lambda _p: self.watch_debounce.start())
        self.watch_poll = QTimer(self)
        self.watch_poll.setInterval(60000)
        self.watch_poll.timeout.connect(self.auto_organize)
        self.setup_tray()

        self.apply_theme(themes.name, save=False)
        geo = self.settings.value("geometry")
        if geo:
            self.restoreGeometry(geo)
        self.change_page(0)
        self.refresh_dashboard()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_dashboard)
        self.timer.start(self.settings.value("refresh_sec", 2, type=int) * 1000)
        self.proc_timer = QTimer(self)
        self.proc_timer.timeout.connect(self.refresh_procs_if_visible)
        self.proc_timer.start(3000)
        if self.settings.value("watch_enabled", False, type=bool):
            self.start_watch(self.settings.value("watch_dir", "", type=str), ask=False)
        QTimer.singleShot(400, self.refresh_health)

    # ------------------------------------------------------------ infrastructure
    def apply_theme(self, name, save=True):
        themes.set_theme(name)
        up, down, check = build_arrows(themes.color("muted"))
        QApplication.setFont(QFont(themes.font["family"] or themes.defaults["family"], themes.font["size"]))
        self.setStyleSheet(themes.qss(up, down, check))
        for cls in (Ring, BarList):
            for w in self.findChildren(cls):
                w.update()
        if hasattr(self, "rn_table") and self.rn_files:
            self.refresh_rename_preview()
        if save:
            self.settings.setValue("theme", name)

    def notify(self, text, kind="info"):
        self.toast.show_message(text, kind)

    def confirm(self, title, text):
        return QMessageBox.question(self, tr(title), text, QMessageBox.Yes | QMessageBox.No,
                                    QMessageBox.No) == QMessageBox.Yes

    def warn(self, title, text):
        QMessageBox.warning(self, tr(title), text)

    def change_page(self, index):
        self.stack.setCurrentIndex(index)
        key = self.page_keys[index]
        for k, btn in self.nav_by_key.items():
            btn.setChecked(k == key)
        self.more_btn.setChecked(key in MORE_KEYS)
        self.more_btn.setText(self.page_icons[key] if key in MORE_KEYS else "⋯")
        if key == "procs":
            self.refresh_procs()
        elif key == "organizer":
            self.auto_scan_organizer()
        elif key == "dashboard":
            self.refresh_health(only_if_stale=True)
        elif key == "cleanup" and not self.cl_scanned:
            self.scan_cleanup()

    def show_more_menu(self):
        menu = QMenu(self)
        for key in MORE_KEYS:
            act = menu.addAction(f"{self.page_icons[key]}   {tr(self.page_titles[key])}")
            act.triggered.connect(lambda checked=False, k=key: self.goto(k))
        menu.exec(self.more_btn.mapToGlobal(self.more_btn.rect().topRight()))
        self.more_btn.setChecked(self.page_keys[self.stack.currentIndex()] in MORE_KEYS)

    def goto(self, key):
        self.change_page(self.page_keys.index(key))

    def open_palette(self, initial=""):
        items = [(tr(self.page_titles[k]), (lambda kk=k: self.goto(kk))) for k in self.page_keys]
        dlg = Palette(self, items, self.search_files_for)
        dlg.move(self.geometry().center().x() - 230, self.geometry().top() + 120)
        if initial:
            dlg.edit.setText(initial)
        if dlg.exec() == QDialog.Accepted:
            cb = dlg.chosen()
            if cb:
                cb()

    def search_files_for(self, text):
        """Palette entry "search files for ..." -> File Search page with the query filled in and started."""
        self.goto("search")
        self.search_query.setText(text)
        self.run_search()

    def page_shell(self, title, desc, top=28, action=None):
        """Plain page: big title, muted one-line description, then the page content (spacing 14-18 px)."""
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(0, top, 0, 28)
        l.setSpacing(16)
        head = QVBoxLayout()
        head.setSpacing(2)
        if title:
            row = QHBoxLayout()
            h = TLabel(title)
            h.setObjectName("pageTitle")
            row.addWidget(h)
            row.addStretch()
            if action is not None:
                row.addWidget(action, 0, Qt.AlignBottom)
            head.addLayout(row)
        if desc:
            d = TLabel(desc)
            d.setObjectName("pageDesc")
            d.setWordWrap(True)
            head.addWidget(d)
        l.addLayout(head)
        return w, l

    def panel(self, title=None):
        f = QFrame()
        f.setObjectName("panel")
        lay = QVBoxLayout(f)
        lay.setContentsMargins(18, 16, 18, 16)
        if title:
            t = TLabel(title)
            t.setObjectName("cardTitle")
            lay.addWidget(t)
        return f, lay

    def footer(self, note_text=""):
        """Bottom row of a page: muted note on the left, buttons on the right. -> (layout, note label)"""
        row = QHBoxLayout()
        row.setSpacing(12)
        note = TLabel(note_text)
        note.setObjectName("note")
        note.setWordWrap(True)
        row.addWidget(note, 1)
        return row, note

    def path_row(self, key, default, button="Choose folder…"):
        edit = DropLineEdit(self.settings.value("path/" + key, default, type=str))
        edit.textChanged.connect(lambda t, k=key: self.settings.setValue("path/" + k, t))
        browse = TButton(button)
        browse.clicked.connect(lambda: self.pick_folder(edit))
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(edit, 1)
        row.addWidget(browse)
        return row, edit

    def pick_folder(self, edit):
        path = QFileDialog.getExistingDirectory(self, tr("Choose folder"), edit.text())
        if path:
            edit.setText(path)

    def valid_dir(self, edit, silent=False):
        p = Path(edit.text()).expanduser()
        if not p.is_dir():
            if not silent:
                self.warn("Invalid folder", tr("Choose an existing folder first."))
            return None
        return p

    def block_if_protected(self, folder):
        reason = core.protected_reason(folder)
        if reason:
            self.warn("Protected folder", tr("This action is blocked for {reason}.\nChoose a normal user folder instead.", reason=reason))
            return True
        return False

    def duration(self, seconds):
        return core.fmt_duration(seconds, ("วัน", "ชม.", "นาที") if i18n.lang == "th" else ("d", "h", "m"))

    def unr(self, res):
        n = res.get("unreadable", 0)
        return tr(" · {n} unreadable skipped", n=n) if n else ""

    def run_task(self, key, fn, args, on_done, status=None, start_btn=None, cancel_btn=None):
        old = self.workers.get(key)
        if old and old.isRunning():
            old.wait(300)
            if old.isRunning():
                return
        w = Worker(fn, *args)
        self.workers[key] = w
        if start_btn:
            start_btn.setEnabled(False)
        if cancel_btn:
            cancel_btn.setVisible(True)
            cancel_btn.setEnabled(True)
        if status:
            w.progress.connect(status.setText)

        def finish():
            if start_btn:
                start_btn.setEnabled(True)
            if cancel_btn:
                cancel_btn.setEnabled(False)
                cancel_btn.setVisible(False)

        def ok(result):
            finish()
            on_done(result)

        def fail(msg):
            finish()
            if status:
                status.setText(tr("Error: {msg}", msg=msg))
            self.notify(msg, "error")
            self.log(f"{key} failed: {msg}")

        w.done.connect(ok)
        w.failed.connect(fail)
        w.start()

    def is_busy(self, key):
        w = self.workers.get(key)
        return bool(w and w.isRunning())

    def busy(self, key):
        if self.is_busy(key):
            self.notify(tr("Another file operation is still running"), "warn")
            return True
        return False

    def cancel_task(self, key):
        w = self.workers.get(key)
        if w and w.isRunning():
            w.cancel()

    def cancel_button(self, key):
        b = TButton("Cancel")
        b.setEnabled(False)
        b.setVisible(False)                           # only shown while the task runs
        b.clicked.connect(lambda: self.cancel_task(key))
        return b

    def finish_move(self, label, res, after):
        moves, skipped, errors = res
        for e in errors:
            self.log(f"{label} failed: {e}")
        self.push_undo(label, moves)
        self.log(f"{label}: moved {len(moves)}, skipped {skipped}, errors {len(errors)}")
        msg = tr("{label}: {n} files", label=tr(label), n=len(moves))
        if errors:
            msg += tr(" · {n} errors", n=len(errors))
        self.notify(msg, "error" if errors else "ok")
        after()

    # --------------------------------------------------- tables: export / menus
    def export_table(self, table, default_name):
        if table.rowCount() == 0:
            self.notify(tr("Nothing to export yet"), "warn")
            return
        path, _ = QFileDialog.getSaveFileName(self, tr("Export CSV"), default_name, "CSV (*.csv)")
        if not path:
            return
        headers = [table.horizontalHeaderItem(c).text() for c in range(table.columnCount())]
        rows = [[table.item(r, c).text() if table.item(r, c) else "" for c in range(table.columnCount())]
                for r in range(table.rowCount())]
        try:
            core.export_csv(path, headers, rows)
            self.notify(tr("Exported {n} rows", n=len(rows)), "ok")
            self.log(f"Exported CSV: {path}")
        except OSError as e:
            self.warn("Export failed", str(e))

    def row_path(self, table, row):
        it = table.item(row, 0) if row >= 0 else None
        if not it:
            return None
        d = it.data(Qt.UserRole)
        if isinstance(d, (list, tuple)) and d:
            return str(d[0])
        if it.data(SUB_ROLE) == "__group__":
            return None
        return d if isinstance(d, str) else it.text()

    def attach_menu(self, table):
        table.setContextMenuPolicy(Qt.CustomContextMenu)
        table.customContextMenuRequested.connect(lambda pos, t=table: self.table_menu(t, pos))

    def table_menu(self, table, pos):
        path = self.row_path(table, table.rowAt(pos.y()))
        menu = QMenu(self)
        a_open = a_show = a_copy = None
        if path:
            a_open = menu.addAction(tr("Open file"))
            a_show = menu.addAction(tr("Show in folder"))
            a_copy = menu.addAction(tr("Copy path"))
        a_all = a_none = None
        if any(table.item(r, 0) is not None and table.item(r, 0).flags() & Qt.ItemIsUserCheckable
               for r in range(min(table.rowCount(), 3))):
            if path:
                menu.addSeparator()
            a_all = menu.addAction(tr("Select all"))
            a_none = menu.addAction(tr("Select none"))
        if menu.isEmpty():
            return
        act = menu.exec(table.viewport().mapToGlobal(pos))
        if act is None:
            return
        if act == a_all:
            set_all_checked(table, True)
        elif act == a_none:
            set_all_checked(table, False)
        elif act == a_open:
            self.open_file(path)
        elif act == a_show:
            open_location(path)
        elif act == a_copy:
            QGuiApplication.clipboard().setText(path)
            self.notify(tr("Path copied"), "ok")

    def open_file(self, path):
        p = Path(path)
        if not p.exists():
            self.notify(tr("File not found"), "warn")
        elif p.suffix.lower().lstrip(".") in core.EXECUTABLE_EXTS:
            self.notify(tr("Not opening programs/scripts from here. Use “Show in folder”."), "warn")
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))

    def open_row(self, table, row):
        path = self.row_path(table, row)
        if path:
            open_location(path)

    # ------------------------------------------------------------------- undo
    def push_undo(self, label, moves):
        if moves:
            self.undo_stack.append((label, moves))
            del self.undo_stack[:-20]
            self.db.push_undo(label, moves)

    def undo_last(self):
        if not self.undo_stack:
            self.notify(tr("Nothing to undo"), "warn")
            return
        label, moves = self.undo_stack[-1]
        if not self.confirm("Undo", tr("Undo “{label}”?\n{n} files will be moved back.", label=tr(label), n=len(moves))):
            return
        if self.busy("move"):
            return
        self.undo_stack.pop()
        self.db.pop_undo()

        def done(res):
            restored, skipped, errors = res
            for e in errors:
                self.log(f"Undo failed: {e}")
            self.log(f"Undo “{label}”: restored {restored}, skipped {skipped}, errors {len(errors)}")
            msg = tr("Restored {n} files", n=restored)
            if errors:
                msg += tr(" · {n} errors", n=len(errors))
            self.notify(msg, "error" if errors else "ok")
            self.after_files_changed()
        self.run_task("move", core.undo_moves, (moves,), done)

    def after_files_changed(self):
        if self.org_table.rowCount():
            self.scan_organizer()
        if self.rn_files:
            self.rn_files = [f for f in self.rn_files if Path(f).exists()]
            self.refresh_rename_preview()

    def undo_button(self, arrow=False):
        b = TButton("↩ Undo last" if arrow else "Undo last")
        b.clicked.connect(self.undo_last)
        return b

    # -------------------------------------------------------------------- log
    def load_logs(self):
        self.logs = self.db.recent_logs(MAX_LOG_LINES)

    def log(self, message):
        line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}"
        self.logs.insert(0, line)
        del self.logs[MAX_LOG_LINES:]
        try:
            self.db.add_log(message)
        except Exception:
            pass
        if hasattr(self, "log_text") and not self.log_search.text().strip():
            self.log_text.setPlainText("\n".join(self.logs))
        self.refresh_db_stats()
        if hasattr(self, "recent_label"):
            self.recent_label.setText("\n".join(self.logs[:5]) or tr("No activity yet."))

    # -------------------------------------------------------------- dashboard
    def make_dashboard(self):
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(0, 24, 0, 28)
        l.setSpacing(20)
        top = QHBoxLayout()
        self.dash_search = QLineEdit()
        self.dash_search.setObjectName("searchbar")
        self.dash_search.setPlaceholderText(tr("Search tools or files… (Ctrl+K)"))
        self.dash_search.setFixedSize(520, 44)
        self.dash_search.installEventFilter(self)
        self.dash_search.textEdited.connect(self.on_dash_search)
        top.addWidget(self.dash_search)
        top.addStretch()
        local = TLabel("All your data stays on this PC")
        local.setObjectName("pageDesc")
        top.addWidget(local)
        l.addLayout(top)
        self.greeting = QLabel("")
        self.greeting.setObjectName("greeting")
        l.addWidget(self.greeting)

        mid = QHBoxLayout()
        mid.setSpacing(20)
        score = Card()
        score.setFixedWidth(340)
        sc = score.body
        sc.addStretch(1)
        self.score_ring = Ring(140, 16)
        sc.addWidget(self.score_ring, 0, Qt.AlignHCenter)
        sc.addSpacing(22)
        cap = TLabel("File tidiness")
        cap.setObjectName("scoreCaption")
        cap.setAlignment(Qt.AlignCenter)
        sc.addWidget(cap)
        sc.addSpacing(8)
        self.score_note = TLabel("")
        self.score_note.setObjectName("note")
        self.score_note.setAlignment(Qt.AlignCenter)
        self.score_note.setWordWrap(True)
        sc.addWidget(self.score_note)
        sc.addStretch(1)
        mid.addWidget(score)
        recs = Card("Suggested for you now")
        self.rec_body = recs.body
        mid.addWidget(recs, 1)
        score.setFixedHeight(270)
        recs.setFixedHeight(270)
        l.addLayout(mid)

        stats = QHBoxLayout()
        stats.setSpacing(20)
        self.free_card = StatCard("Free space")
        self.cpu_card = StatCard("CPU")
        self.undo_card = StatCard("Latest undoable")
        for c in (self.free_card, self.cpu_card, self.undo_card):
            c.setFixedHeight(94)
            stats.addWidget(c, 1)
        l.addLayout(stats)
        l.addStretch(1)
        self.show_recommendations(None)
        return w

    def eventFilter(self, obj, ev):
        if obj is getattr(self, "dash_search", None) and ev.type() == QEvent.MouseButtonPress:
            QTimer.singleShot(0, self.open_palette)
            return True
        return super().eventFilter(obj, ev)

    def on_dash_search(self, text):
        self.dash_search.clear()
        self.open_palette(text)

    def drive_label(self):
        anchor = Path.home().anchor or os.path.abspath(os.sep)
        return anchor.rstrip("\\/") or anchor

    def refresh_dashboard(self):
        try:
            hour = datetime.now().hour
            self.greeting.setText(tr("Good morning — here's how your PC is doing") if hour < 12 else
                                  tr("Good afternoon — here's how your PC is doing") if hour < 17 else
                                  tr("Good evening — here's how your PC is doing"))
            usage = psutil.disk_usage(Path.home().anchor or os.path.abspath(os.sep))
            self.free_card.set_label(tr("Free space ({drive})", drive=self.drive_label()))
            self.free_card.set_value(fmt_bytes(usage.free))
            self.cpu_card.set_value(f"{psutil.cpu_percent():.0f}%")
            self.undo_card.set_value(tr("{n} items", n=len(self.undo_stack)))
        except Exception as e:
            if not getattr(self, "_dash_warned", False):
                self._dash_warned = True
                self.log(f"Dashboard refresh warning: {e}")

    # ---- tidiness score + suggestions (a quick read-only look at the organizer folder)
    def health_folder(self):
        p = Path(self.settings.value("path/organizer", str(Path.home() / "Downloads"), type=str)).expanduser()
        return p if p.is_dir() and not core.protected_reason(p) else None

    def refresh_health(self, only_if_stale=False):
        if only_if_stale and self.health is not None and time.time() - getattr(self, "_health_time", 0) < 120:
            return
        if self.is_busy("health"):
            return
        folder = self.health_folder()
        if folder is None:
            self.health = None
            self.show_recommendations(None, missing=True)
            return
        self.run_task("health", core.health_summary, (folder,), self.on_health_done)

    def on_health_done(self, res):
        self.health = res
        self._health_time = time.time()
        self.show_recommendations(res)

    def invalidate_health(self):
        self._health_time = 0

    def show_recommendations(self, res, missing=False):
        while self.rec_body.count():
            item = self.rec_body.takeAt(0)
            wd = item.widget()
            if wd is not None:
                wd.hide()
                wd.setParent(None)
                wd.deleteLater()
        rows = []
        if missing:
            self.score_ring.set(0, "—")
            self.score_note.setText(tr("Choose a folder in File Organizer to get tips."))
            rows.append((tr("No folder to check yet"), tr("Pick one in File Organizer and come back."), None, None))
        elif res is None:
            self.score_ring.set(0, "—")
            self.score_note.setText(tr("Checking your files…"))
            rows.append((tr("Checking your files…"), tr("This only reads names and sizes."), None, None))
        else:
            score = res["score"]
            self.score_ring.set(score, str(score))
            self.score_note.setText(tr("Excellent: your files are in great shape") if score >= 90 else
                                    tr("Good, but a little more to clear") if score >= 70 else
                                    tr("Fair: worth a quick tidy-up") if score >= 40 else
                                    tr("Cluttered: start with the tips on the right"))
            folder = res["folder"]
            name = Path(folder).name or folder
            if res["loose"] >= 5:
                title = tr("Downloads folder is cluttered") if name.lower() == "downloads" else tr("“{name}” folder is cluttered", name=name)
                rows.append((title, tr("{n} loose files of {k} kinds. They can be sorted automatically", n=res["loose"], k=res["kinds"]),
                             tr("Organize"), "organizer"))
            if res["dup_files"]:
                rows.append((tr("Duplicate files found"), tr("{n} extra copies ({size}). Copies move to _Duplicates, nothing is deleted",
                                                            n=res["dup_files"], size=fmt_bytes(res["dup_bytes"])), tr("View duplicates"), "dups"))
            junk = res["temp_bytes"] + res["old_large_bytes"]
            if junk or res["empty_dirs"]:
                rows.append((tr("Temporary files piling up"), tr("{size} can go to the Recycle Bin, recoverable", size=fmt_bytes(junk)),
                             tr("Clean up"), "cleanup"))
            if res["partial"]:
                self.score_note.setText(self.score_note.text() + "  " + tr("(quick check, big folder)"))
            if not rows:
                rows.append((tr("Nothing to do right now"), tr("Your files look tidy."), None, None))
        for i, (title, sub, btn_text, target) in enumerate(rows[:3]):
            if i:
                line = QFrame()
                line.setObjectName("hline")
                line.setFixedHeight(1)
                self.rec_body.addWidget(line)
            row = QWidget()
            row.setFixedHeight(66)
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0)
            col = QVBoxLayout()
            col.setSpacing(0)
            col.addStretch()
            t = QLabel(title)
            t.setObjectName("rowTitle")
            s = QLabel(sub)
            s.setObjectName("rowSub")
            col.addWidget(t)
            col.addWidget(s)
            col.addStretch()
            rl.addLayout(col, 1)
            if btn_text:
                b = QPushButton(btn_text)
                b.setObjectName("primary")
                b.setFixedHeight(36)
                b.setCursor(Qt.PointingHandCursor)
                b.clicked.connect(lambda checked=False, k=target, r=res: self.open_recommendation(k, r))
                rl.addWidget(b)
            self.rec_body.addWidget(row)
        self.rec_body.addStretch()

    def open_recommendation(self, key, res):
        if res:
            folder = res["folder"]
            if key == "dups":
                self.dup_path.setText(folder)
            elif key == "cleanup":
                self.cl_path.setText(folder)
        self.goto(key)

    # -------------------------------------------------------------- organizer
    def legacy_rules(self):
        """Rules stored by older versions in QSettings (imported into SQLite once)."""
        try:
            return core.parse_rules(json.loads(self.settings.value("org_rules", "[]", type=str)))
        except (ValueError, TypeError):
            return []

    def load_rules(self):
        rules = core.parse_rules([{"pattern": p, "folder": f} for p, f in self.db.get_rules()])
        core.set_extra_dirs(rules)
        return rules

    MODE_LABELS = {"By type": "By file type", "By type + month": "Type + month", "By month": "By month",
                   "Photos by date taken": "Photos by date taken"}

    def make_organizer(self):
        w, l = self.page_shell("File Organizer", "Preview before every change. Existing files are never overwritten.")
        self.org_page = w
        row, self.org_path = self.path_row("organizer", str(Path.home() / "Downloads"))
        l.addLayout(row)
        saved = self.settings.value("org_mode", core.ORGANIZE_MODES[0], type=str)
        self.org_mode_name = saved if saved in core.ORGANIZE_MODES else core.ORGANIZE_MODES[0]
        chips = QHBoxLayout()
        chips.setSpacing(10)
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.mode_buttons = {}
        for mode in core.ORGANIZE_MODES:
            b = TButton(self.MODE_LABELS[mode])
            b.setObjectName("chip")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setChecked(mode == self.org_mode_name)
            b.clicked.connect(lambda checked=False, m=mode: self.set_org_mode(m))
            self.mode_group.addButton(b)
            self.mode_buttons[mode] = b
            chips.addWidget(b)
        chips.addStretch()
        rules_btn = TButton("My rules…")
        rules_btn.clicked.connect(self.edit_rules)
        chips.addWidget(rules_btn)
        l.addLayout(chips)

        cards = QHBoxLayout()
        cards.setSpacing(20)
        left = Card("Folders that will be created")
        self.org_bars = BarList(tr("Nothing to organize here."), label_w=123, value_w=84)
        left.body.addWidget(self.org_bars)
        left.body.addStretch(1)
        right = Card("Sample of files to move")
        self.org_table = make_table(["File", "Destination"], (0,), sortable=False, bare=True, row_h=47, header=False)
        self.org_table.itemChanged.connect(lambda _i: self.update_org_button())
        self.attach_menu(self.org_table)
        right.body.addWidget(self.org_table)
        cards.addWidget(left, 1)
        cards.addWidget(right, 1)
        l.addLayout(cards, 1)

        bottom, self.org_note = self.footer("Partly downloaded files, hidden files and symlinks are always skipped.")
        self.watch_check = TCheck("Auto-organize (watch folder)")
        self.watch_check.setChecked(self.settings.value("watch_enabled", False, type=bool))
        self.watch_check.toggled.connect(self.on_watch_toggled)
        bottom.addWidget(self.watch_check)
        bottom.addWidget(self.undo_button())
        go = TButton("Organize")
        go.setObjectName("primary")
        go.clicked.connect(self.organize_files)
        self.org_go = go
        bottom.addWidget(go)
        l.addLayout(bottom)
        self.org_timer = QTimer(self)
        self.org_timer.setSingleShot(True)
        self.org_timer.setInterval(700)
        self.org_timer.timeout.connect(self.auto_scan_organizer)
        self.org_path.textChanged.connect(lambda _t: self.org_timer.start())
        return w

    def current_mode(self):
        return self.org_mode_name

    def set_org_mode(self, mode):
        self.org_mode_name = mode
        self.settings.setValue("org_mode", mode)
        self.auto_scan_organizer()

    def update_org_button(self):
        n = sum(1 for r in range(self.org_table.rowCount())
                if self.org_table.item(r, 0) is not None and self.org_table.item(r, 0).checkState() == Qt.Checked)
        self.org_go.setText(tr("Organize {n} files", n=n) if self.org_table.rowCount() else tr("Organize"))

    def edit_rules(self):
        dlg = RulesDialog(self, self.rules)
        if dlg.exec() == QDialog.Accepted:
            self.rules = core.parse_rules(dlg.rules())
            self.db.set_rules(self.rules)
            core.set_extra_dirs(self.rules)
            self.notify(tr("{n} rules saved", n=len(self.rules)), "ok")
            self.log(f"Custom rules saved ({len(self.rules)})")
            if self.org_table.rowCount():
                self.scan_organizer()

    def auto_scan_organizer(self):
        """Preview refreshes by itself (page opened, folder or mode changed). Never pops up dialogs."""
        if getattr(self, "org_page", None) is None or self.stack.currentWidget() is not self.org_page:
            return
        if self.is_busy("move"):
            return
        self.scan_organizer(silent=True)

    def clear_org_preview(self, note):
        fill_table(self.org_table, [])
        self.org_bars.set_items([])
        self.update_org_button()
        self.org_note.setText(note)

    def rel_dest_label(self, folder, dest):
        try:
            parts = list(Path(dest).relative_to(folder).parts)
        except ValueError:
            return str(dest)
        if parts and parts[0] in core.CATEGORY_MAP or parts and parts[0] == "Others":
            parts[0] = tr(parts[0])
        return "/".join(parts) or str(dest)

    def scan_organizer(self, silent=False):
        note = tr("Partly downloaded files, hidden files and symlinks are always skipped.")
        folder = self.valid_dir(self.org_path, silent)
        if not folder:
            self.clear_org_preview(tr("Choose an existing folder to preview."))
            return
        if core.protected_reason(folder):
            if not silent:
                self.block_if_protected(folder)
            self.clear_org_preview(tr("This folder is protected. Choose a normal user folder."))
            return
        try:
            plan = core.plan_organize(folder, self.current_mode(), self.rules)
        except OSError as e:
            if not silent:
                self.warn("Cannot read folder", str(e))
            self.clear_org_preview(str(e))
            return
        labels = [self.rel_dest_label(folder, dest) for _p, _c, _s, dest in plan]
        rows = [[p.name, "→ " + lab] for (p, _c, _s, _d), lab in zip(plan, labels)]
        fill_table(self.org_table, rows, checkable=True, userdata=[[str(p), str(dest)] for p, _c, _s, dest in plan])
        for r in range(self.org_table.rowCount()):
            it = self.org_table.item(r, 1)
            it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            it.setForeground(QColor(themes.color("muted")))
        counts = {}
        for lab in labels:
            counts[lab] = counts.get(lab, 0) + 1
        top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        items = [(k, v, tr("{n} files", n=v)) for k, v in top[:9]]
        if len(top) > 9:
            rest = sum(v for _k, v in top[9:])
            items.append((tr("Others"), rest, tr("{n} files", n=rest)))
        self.org_bars.set_items(items)
        self.update_org_button()
        self.org_note.setText(note)
        if not silent:
            self.log(f"Scanned {folder} ({len(plan)} files)")

    def organize_files(self):
        todo = []
        for r in range(self.org_table.rowCount()):
            it = self.org_table.item(r, 0)
            if it.checkState() == Qt.Checked:
                src, dest_dir = it.data(Qt.UserRole)
                todo.append((src, dest_dir))
        if not todo:
            QMessageBox.information(self, tr("Nothing to do"), tr("Pick a folder with files in it and keep at least one file ticked."))
            return
        if self.block_if_protected(self.org_path.text()):
            return
        if not self.confirm("Confirm organization", tr("Move {n} files?\nExisting files are never overwritten (a number is added to the name).\nYou can undo afterwards.", n=len(todo))):
            return
        if self.busy("move"):
            return
        label = f"Organize {Path(self.org_path.text()).name}"
        self.run_task("move", core.move_files, (todo,), lambda res: self.finish_move(label, res, self.scan_organizer),
                      self.org_note, self.org_go)

    # ---- watch folder ----
    def on_watch_toggled(self, on):
        if on:
            folder = self.valid_dir(self.org_path)
            if not folder or self.block_if_protected(folder) or not self.confirm(
                    "Auto-organize",
                    tr("New files that finish downloading in\n{folder}\nwill be moved automatically using your current mode and rules.\nEvery batch can be undone. Enable?", folder=folder)):
                self.watch_check.blockSignals(True)
                self.watch_check.setChecked(False)
                self.watch_check.blockSignals(False)
                return
            self.start_watch(str(folder), ask=False)
        else:
            self.stop_watch()

    def start_watch(self, folder, ask=False):
        p = Path(folder)
        if not p.is_dir() or core.protected_reason(p):
            self.stop_watch()
            return
        self.stop_watch(keep_setting=True)
        self.watch_dir = p
        self.watcher.addPath(str(p))
        self.watch_poll.start()
        self.settings.setValue("watch_enabled", True)
        self.settings.setValue("watch_dir", str(p))
        self.watch_check.setToolTip(tr("Watching: {folder}", folder=p))
        self.watch_check.blockSignals(True)
        self.watch_check.setChecked(True)
        self.watch_check.blockSignals(False)
        self.log(f"Auto-organize enabled for {p}")
        QTimer.singleShot(1500, self.auto_organize)

    def stop_watch(self, keep_setting=False):
        if self.watcher.directories():
            self.watcher.removePaths(self.watcher.directories())
        self.watch_poll.stop()
        self.watch_debounce.stop()
        if self.watch_dir and not keep_setting:
            self.log("Auto-organize disabled")
        self.watch_dir = None
        if not keep_setting:
            self.settings.setValue("watch_enabled", False)
            self.watch_check.setToolTip("")

    def auto_organize(self):
        if not self.watch_dir or self.is_busy("move"):
            return
        try:
            plan = core.plan_organize(self.watch_dir, self.current_mode(), self.rules)
        except OSError:
            return
        now, todo = time.time(), []
        for p, _cat, _size, dest in plan:
            try:
                if now - p.stat().st_mtime > 5:          # file has stopped changing
                    todo.append((str(p), str(dest)))
            except OSError:
                continue
        if not todo:
            return

        def after():
            if self.tray:
                self.tray.showMessage(APP_NAME, tr("Auto-organized {n} files", n=len(self.undo_stack[-1][1]) if self.undo_stack else 0))
            if self.org_table.rowCount():
                self.scan_organizer()
        self.run_task("move", core.move_files, (todo,), lambda res: self.finish_move("Auto-organize", res, after))

    # ---- tray ----
    def setup_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(QIcon(make_app_pixmap(64)), self)
        self.tray.setToolTip(APP_NAME)
        self.tray_menu = QMenu()
        self.tray_menu.addAction(tr("Show window"), self.show_from_tray)
        self.tray_menu.addAction(tr("Organize watched folder now"), self.auto_organize)
        self.tray_menu.addSeparator()
        self.tray_menu.addAction(tr("Quit"), self.quit_app)
        self.tray.setContextMenu(self.tray_menu)
        self.tray.activated.connect(lambda reason: self.show_from_tray() if reason == QSystemTrayIcon.Trigger else None)
        self.tray.show()

    def show_from_tray(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def quit_app(self):
        self._quitting = True
        self.close()

    # ------------------------------------------------------------------- disk
    def make_disk(self):
        w, l = self.page_shell("Disk Analyzer", "See what takes the most space. Read-only: nothing is touched.")
        row, self.disk_path = self.path_row("disk", str(Path.home()), "Choose…")
        self.disk_btn = TButton("Analyze")
        self.disk_btn.setObjectName("primary")
        self.disk_btn.clicked.connect(self.scan_disk)
        self.disk_cancel = self.cancel_button("disk")
        row.addWidget(self.disk_btn)
        row.addWidget(self.disk_cancel)
        l.addLayout(row)
        stats = QHBoxLayout()
        stats.setSpacing(20)
        self.disk_total = StatCard("Total scanned")
        self.disk_files = StatCard("Files")
        self.disk_skipped = StatCard("Skipped (unreadable)")
        for c in (self.disk_total, self.disk_files, self.disk_skipped):
            c.setFixedHeight(86)
            stats.addWidget(c, 1)
        l.addLayout(stats)
        cards = QHBoxLayout()
        cards.setSpacing(20)
        cp = Card("Space by type")
        self.cat_chart = BarList(tr("Run an analysis"), label_w=102, value_w=82)
        cp.body.addWidget(self.cat_chart)
        cp.body.addStretch(1)
        fp = Card("Biggest folders")
        self.folder_chart = BarList(tr("Run an analysis"), label_w=102, value_w=82)
        fp.body.addWidget(self.folder_chart)
        fp.body.addStretch(1)
        cards.addWidget(cp, 1)
        cards.addWidget(fp, 1)
        l.addLayout(cards, 1)
        # the 100 largest files live in a dialog (opened from the footer) so the page stays as designed
        self.disk_dialog = QDialog(self)
        self.disk_dialog.setWindowTitle(tr("Largest files"))
        self.disk_dialog.resize(860, 520)
        dl = QVBoxLayout(self.disk_dialog)
        self.disk_table = make_table(["Largest files", "Size", "Last modified"], (0,))
        self.disk_table.doubleClicked.connect(lambda idx: self.open_row(self.disk_table, idx.row()))
        self.attach_menu(self.disk_table)
        dl.addWidget(self.disk_table, 1)
        drow = QHBoxLayout()
        drow.addStretch()
        o = TButton("Open location")
        o.clicked.connect(lambda: self.open_row(self.disk_table, self.disk_table.currentRow()))
        close = TButton("Close")
        close.clicked.connect(self.disk_dialog.close)
        drow.addWidget(o)
        drow.addWidget(close)
        dl.addLayout(drow)
        bottom = QHBoxLayout()
        self.disk_status = TButton("Show 100 largest files")
        self.disk_status.setObjectName("flat")
        self.disk_status.setCursor(Qt.PointingHandCursor)
        self.disk_status.setStyleSheet("QPushButton { padding: 6px 0px; text-align: left; font-size: 9pt; }")
        self.disk_status.clicked.connect(self.disk_dialog.show)
        bottom.addWidget(self.disk_status)
        bottom.addStretch(1)
        e = TButton("Export CSV")
        e.clicked.connect(lambda: self.export_table(self.disk_table, "largest_files.csv"))
        bottom.addWidget(e)
        l.addLayout(bottom)
        return w

    def scan_disk(self):
        root = self.valid_dir(self.disk_path)
        if not root:
            return
        self.disk_root = root
        self.disk_status.setText(tr("Scanning…"))
        self.run_task("disk", core.analyze_disk, (root, 100), self.on_disk_done,
                      self.disk_status, self.disk_btn, self.disk_cancel)

    def on_disk_done(self, res):
        rows = [[p, (fmt_bytes(s), s), (datetime.fromtimestamp(m).strftime("%Y-%m-%d %H:%M"), m)] for s, p, m in res["top"]]
        fill_table(self.disk_table, rows)
        cats = sorted(res["categories"].items(), key=lambda kv: kv[1], reverse=True)[:8]
        self.cat_chart.set_items([(tr(k), v, fmt_bytes(v)) for k, v in cats])
        folders = sorted(res["folders"].items(), key=lambda kv: kv[1], reverse=True)[:8]
        self.folder_chart.set_items([(tr(k), v, fmt_bytes(v)) for k, v in folders])
        self.disk_total.set_value(fmt_bytes(res["total"]))
        self.disk_files.set_value(f"{res['scanned']:,}")
        self.disk_skipped.set_value(f"{res['unreadable']:,}")
        self.disk_status.setText(tr("Cancelled. Show 100 largest files") if res["cancelled"] else tr("Show 100 largest files"))
        self.log(f"Disk analysis{' (cancelled)' if res['cancelled'] else ''}: {self.disk_root} ({res['scanned']} files, {res['unreadable']} unreadable)")

    # ------------------------------------------------------------- duplicates
    def make_dups(self):
        opts_btn = TButton("Options…")
        opts_btn.setObjectName("flat")
        opts_btn.setCursor(Qt.PointingHandCursor)
        w, l = self.page_shell("Duplicate Finder",
                               "Compares size, then verifies with SHA-256. The oldest copy is kept; the rest move to a separate folder.",
                               action=opts_btn)
        row, self.dup_path = self.path_row("dups", str(Path.home() / "Downloads"))
        self.dup_btn = TButton("Scan")
        self.dup_btn.setObjectName("primary")
        self.dup_btn.clicked.connect(self.scan_dups)
        self.dup_cancel = self.cancel_button("dups")
        row.addWidget(self.dup_btn)
        row.addWidget(self.dup_cancel)
        l.addLayout(row)
        # scan options live in a small dialog (the design has no room for them on the page)
        self.dup_opts = QDialog(self)
        self.dup_opts.setWindowTitle(tr("Options…"))
        ol = QVBoxLayout(self.dup_opts)
        ol.addWidget(TLabel("Ignore files smaller than"))
        self.dup_min = QSpinBox()
        self.dup_min.setRange(0, 1_000_000)
        self.dup_min.setValue(self.settings.value("dup_min", 100, type=int))
        self.dup_min.setSuffix(tr(" KB min"))
        self.dup_min.valueChanged.connect(lambda v: self.settings.setValue("dup_min", v))
        ol.addWidget(self.dup_min)
        ok = TButton("Close")
        ok.clicked.connect(self.dup_opts.accept)
        ol.addWidget(ok, 0, Qt.AlignRight)
        opts_btn.clicked.connect(self.dup_opts.exec)

        stats = QHBoxLayout()
        stats.setSpacing(20)
        self.dup_groups_card = StatCard("Duplicate groups", "0")
        self.dup_move_card = StatCard("Files to move", "0")
        self.dup_space_card = StatCard("Space to reclaim", "0 B")
        for c in (self.dup_groups_card, self.dup_move_card, self.dup_space_card):
            c.setFixedHeight(86)
            stats.addWidget(c, 1)
        l.addLayout(stats)

        card = Card(margins=(0, 0, 0, 0))
        self.dup_table = make_table(["File", "Size", "Action"], (0,), sortable=False, bare=True, row_h=49)
        self.dup_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Fixed)
        self.dup_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Fixed)
        for c in range(3):
            self.dup_table.setItemDelegateForColumn(c, BandDelegate(self.dup_table))
        self.dup_table.setColumnWidth(1, 112)
        self.dup_table.setColumnWidth(2, 150)
        self.dup_table.doubleClicked.connect(lambda idx: self.open_row(self.dup_table, idx.row()))
        self.dup_table.itemChanged.connect(self.on_dup_item_changed)
        self.attach_menu(self.dup_table)
        card.body.addWidget(self.dup_table, 1)
        line = QFrame()
        line.setObjectName("footline")
        line.setFixedHeight(1)
        card.body.addWidget(line)
        foot = QWidget()
        bottom = QHBoxLayout(foot)
        bottom.setContentsMargins(18, 12, 18, 12)
        bottom.setSpacing(12)
        self.dup_status = TLabel("Moving to the _Duplicates folder can be undone.")
        self.dup_status.setObjectName("note")
        bottom.addWidget(self.dup_status, 1)
        undo = self.undo_button()
        undo.setObjectName("flat")
        bottom.addWidget(undo)
        rec = TButton("Send to Recycle Bin…")
        rec.setObjectName("danger")
        rec.clicked.connect(self.recycle_dups)
        self.dup_rec = rec
        bottom.addWidget(rec)
        go = TButton("Move to _Duplicates")
        go.setObjectName("primary")
        go.clicked.connect(self.move_dups)
        self.dup_go = go
        bottom.addWidget(go)
        card.body.addWidget(foot)
        l.addWidget(card, 1)
        return w

    def scan_dups(self):
        root = self.valid_dir(self.dup_path)
        if not root:
            return
        self.dup_root = root
        self.dup_status.setText(tr("Scanning…"))
        self.db.hash_cache.reset_counters()
        self.run_task("dups", functools.partial(core.find_duplicates, cache=self.db.hash_cache),
                      (root, self.dup_min.value() * 1024), self.on_dups_done,
                      self.dup_status, self.dup_btn, self.dup_cancel)

    KEEP_ROLE = Qt.UserRole + 3          # marks the oldest copy of a group (the one that is kept by default)

    def on_dups_done(self, res):
        try:
            gs = res["groups"]
            self.db.add_scan("duplicates", self.dup_root or "", res["scanned"], len(gs),
                             sum(len(g) - 1 for _s, g in gs), sum(s * (len(g) - 1) for s, g in gs),
                             self.db.hash_cache.hits, self.db.hash_cache.misses, res.get("seconds", 0.0))
        except Exception:
            pass
        rows, checks, userdata, group_rows = [], [], [], []
        for gi, (size, files) in enumerate(res["groups"], 1):
            group_rows.append(len(rows))
            rows.append([tr("Group {n} · {name}", n=gi, name=os.path.basename(files[0][0])), (fmt_bytes(size), size),
                         tr("{n} files", n=len(files))])
            checks.append(False)
            userdata.append(None)
            for i, (p, m) in enumerate(files):
                rows.append([p, (fmt_bytes(size), size), tr("Keep (oldest)") if i == 0 else tr("Will move")])
                checks.append(i != 0)
                userdata.append(p)
        self.dup_table.blockSignals(True)
        fill_table(self.dup_table, rows, checkable=True, checks=checks, userdata=userdata)
        bold = QFont(self.dup_table.font())
        bold.setBold(True)
        for r, row in enumerate(rows):
            if r in group_rows:
                self.dup_table.setRowHeight(r, 44)
                for c in range(3):
                    it = self.dup_table.item(r, c)
                    it.setFlags(Qt.ItemIsEnabled)
                    it.setData(Qt.CheckStateRole, None)
                    it.setBackground(QColor(themes.color("alt")))
                    it.setFont(bold)
                    if c == 0:
                        it.setData(SUB_ROLE, "__group__")
            else:
                self.dup_table.item(r, 0).setData(self.KEEP_ROLE, row[2] == tr("Keep (oldest)"))
        self.dup_table.blockSignals(False)
        for r in range(self.dup_table.rowCount()):
            self.style_dup_row(r)
        self.update_dup_stats(len(res["groups"]))
        state = tr("Cancelled. ") if res["cancelled"] else ""
        self.dup_status.setText(state + tr("Moving to the _Duplicates folder can be undone.") + self.unr(res))
        self.log(f"Duplicate scan{' (cancelled)' if res['cancelled'] else ''}: {self.dup_root} ({len(res['groups'])} groups)")

    def style_dup_row(self, r):
        it0, it1, it2 = (self.dup_table.item(r, c) for c in range(3))
        if it0 is None or it0.data(SUB_ROLE) == "__group__":
            if it1 is not None:
                it1.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            return
        moving = it0.checkState() == Qt.Checked
        it1.setForeground(QColor(themes.color("muted")))
        f = QFont(self.dup_table.font())
        f.setBold(moving)
        it2.setFont(f)
        it2.setForeground(QColor(themes.color("accent_text" if moving else "text")))
        it2.setText(tr("Will move") if moving else (tr("Keep (oldest)") if it0.data(self.KEEP_ROLE) else tr("Kept")))

    def on_dup_item_changed(self, item):
        if item.column() != 0:
            return
        self.dup_table.blockSignals(True)
        self.style_dup_row(item.row())
        self.dup_table.blockSignals(False)
        self.update_dup_stats()

    def update_dup_stats(self, groups=None):
        t = self.dup_table
        n = size = 0
        for r in range(t.rowCount()):
            it = t.item(r, 0)
            if it is not None and it.data(SUB_ROLE) != "__group__" and it.checkState() == Qt.Checked:
                n += 1
                size += t.item(r, 1).data(SORT_ROLE) or 0
        if groups is None:
            groups = sum(1 for r in range(t.rowCount()) if t.item(r, 0) is not None and t.item(r, 0).data(SUB_ROLE) == "__group__")
        self.dup_groups_card.set_value(str(groups))
        self.dup_move_card.set_value(str(n))
        self.dup_space_card.set_value(fmt_bytes(size))

    def checked_dups(self):
        return [self.dup_table.item(r, 0).text() for r in range(self.dup_table.rowCount())
                if self.dup_table.item(r, 0).checkState() == Qt.Checked]

    def move_dups(self):
        todo = self.checked_dups()
        if not todo or not self.dup_root:
            QMessageBox.information(self, tr("Nothing to do"), tr("Scan and tick at least one file first."))
            return
        if self.block_if_protected(self.dup_root):
            return
        dest = self.dup_root / "_Duplicates"
        if not self.confirm("Confirm move", tr("Move {n} duplicate files to:\n{dest}\n\nNothing is deleted.", n=len(todo), dest=dest)):
            return
        if self.busy("move"):
            return

        def after():
            self.dup_table.setRowCount(0)
            self.update_dup_stats(0)
            self.dup_status.setText(tr("Done. Scan again to refresh."))
        self.run_task("move", core.move_files, ([(p, dest) for p in todo],),
                      lambda res: self.finish_move("Move duplicates", res, after), self.dup_status, self.dup_go)

    def recycle_dups(self):
        todo = self.checked_dups()
        if not todo:
            QMessageBox.information(self, tr("Nothing to do"), tr("Scan and tick at least one file first."))
            return
        size = sum(os.path.getsize(p) for p in todo if os.path.exists(p))
        if not self.confirm("Send to Recycle Bin", tr("Send {n} files ({size}) to the Recycle Bin?\nYou can restore them from the Recycle Bin, but this app cannot undo it.", n=len(todo), size=fmt_bytes(size))):
            return
        if self.busy("move"):
            return

        def done(res):
            trashed, errors = res
            for e in errors:
                self.log(f"Recycle failed: {e}")
            self.log(f"Recycled {len(trashed)} duplicate files, errors {len(errors)}")
            msg = tr("Recycled {n} files", n=len(trashed))
            if errors:
                msg += tr(" · {n} errors", n=len(errors))
            self.notify(msg, "error" if errors else "ok")
            self.dup_table.setRowCount(0)
            self.update_dup_stats(0)
            self.dup_status.setText(tr("Done. Scan again to refresh."))
        self.run_task("move", core.trash_files, (todo,), done, self.dup_status, self.dup_rec)

    # ----------------------------------------------------------------- search
    def make_search(self):
        w, l = self.page_shell("File Search", "Search by name (substring) or wildcard such as *.pdf or report_*.xlsx.")
        row, self.search_path = self.path_row("search", str(Path.home()))
        l.addLayout(row)
        row2 = QHBoxLayout()
        self.search_query = QLineEdit(self.settings.value("search_query", "", type=str))
        self.search_query.setPlaceholderText(tr("e.g. invoice   or   *.pdf"))
        self.search_query.returnPressed.connect(self.run_search)
        self.search_min = QSpinBox()
        self.search_min.setRange(0, 1_000_000)
        self.search_min.setSuffix(tr(" MB min"))
        self.search_btn = TButton("Search")
        self.search_btn.setObjectName("primary")
        self.search_btn.clicked.connect(self.run_search)
        self.search_cancel = self.cancel_button("search")
        row2.addWidget(self.search_query, 1)
        row2.addWidget(self.search_min)
        row2.addWidget(self.search_btn)
        row2.addWidget(self.search_cancel)
        l.addLayout(row2)
        self.search_table = make_table(["File", "Size", "Modified"], (0,))
        self.search_table.doubleClicked.connect(lambda idx: self.open_row(self.search_table, idx.row()))
        self.attach_menu(self.search_table)
        l.addWidget(self.search_table, 1)
        bottom = QHBoxLayout()
        self.search_status = TLabel("Double-click a result to open its folder. Right-click for more.")
        self.search_status.setObjectName("muted")
        bottom.addWidget(self.search_status, 1)
        e = TButton("Export CSV…")
        e.clicked.connect(lambda: self.export_table(self.search_table, "search_results.csv"))
        bottom.addWidget(e)
        l.addLayout(bottom)
        return w

    def run_search(self):
        root = self.valid_dir(self.search_path)
        if not root:
            return
        self.settings.setValue("search_query", self.search_query.text())
        self.search_status.setText(tr("Searching…"))
        self.run_task("search", core.search_files,
                      (root, self.search_query.text(), self.search_min.value() * 1024 * 1024),
                      self.on_search_done, self.search_status, self.search_btn, self.search_cancel)

    def on_search_done(self, res):
        rows = [[p, (fmt_bytes(s), s), (datetime.fromtimestamp(m).strftime("%Y-%m-%d %H:%M"), m)] for p, s, m in res["results"]]
        fill_table(self.search_table, rows)
        extra = tr(" (showing the first 2000)") if res["truncated"] else ""
        state = tr("Cancelled. ") if res["cancelled"] else ""
        self.search_status.setText(state + tr("{n} results", n=len(rows)) + extra + tr(" · {n} files checked", n=res["scanned"]) + self.unr(res))
        self.log(f"Search “{self.search_query.text()}”: {len(rows)} results")

    # ---------------------------------------------------------------- renamer
    def make_renamer(self):
        w, l = self.page_shell("Bulk Renamer", "Rename many files at once. Live preview, duplicate-name check, and the whole batch can be undone.")
        top = QHBoxLayout()
        top.setSpacing(12)
        for text, fn, primary in (("Add files…", self.rn_add_files, True), ("Add folder…", self.rn_add_folder, False),
                                  ("Clear", self.rn_clear, False)):
            b = TButton(text)
            if primary:
                b.setObjectName("primary")
            b.clicked.connect(fn)
            top.addWidget(b)
        top.addStretch()
        l.addLayout(top)

        opts = Card()
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(12)
        for c in range(4):
            grid.setColumnStretch(c, 1)
        opts.body.addLayout(grid)

        def cell(label, widget, row, col):
            box = QWidget()
            bl = QVBoxLayout(box)
            bl.setContentsMargins(0, 0, 0, 0)
            bl.setSpacing(4)
            lab = TLabel(label) if label else QLabel(" ")
            lab.setObjectName("fieldLabel")
            bl.addWidget(lab)
            bl.addWidget(widget)
            grid.addWidget(box, row, col)

        self.rn_find, self.rn_repl = QLineEdit(), QLineEdit()
        self.rn_prefix, self.rn_suffix = QLineEdit(), QLineEdit()
        for edit in (self.rn_find, self.rn_repl, self.rn_prefix, self.rn_suffix):
            edit.setPlaceholderText(tr("Optional"))
            edit.textChanged.connect(self.refresh_rename_preview)
        self.rn_case, self.rn_spaces = QComboBox(), QComboBox()
        add_items(self.rn_case, ["Keep", "lowercase", "UPPERCASE", "Title Case"])
        add_items(self.rn_spaces, ["Keep", "Underscore", "Dash", "Remove"])
        self.rn_num = TCheck("Add number")
        self.rn_num.setMinimumHeight(40)
        self.rn_start = IntField(0, 999999, 1)
        self.rn_pad = IntField(1, 8, 3)
        self.rn_start.setFixedWidth(34)
        self.rn_pad.setFixedWidth(30)
        pair = QFrame()
        pair.setObjectName("pairfield")
        pl = QHBoxLayout(pair)
        pl.setContentsMargins(10, 0, 10, 0)
        pl.setSpacing(0)
        pl.addWidget(self.rn_start)
        pl.addWidget(QLabel("/"))
        pl.addWidget(self.rn_pad)
        pl.addStretch()
        for c in (self.rn_case, self.rn_spaces):
            c.currentIndexChanged.connect(self.refresh_rename_preview)
        self.rn_num.stateChanged.connect(self.refresh_rename_preview)
        self.rn_start.valueChanged.connect(self.refresh_rename_preview)
        self.rn_pad.valueChanged.connect(self.refresh_rename_preview)
        cell("Find", self.rn_find, 0, 0)
        cell("Replace with", self.rn_repl, 0, 1)
        cell("Prefix", self.rn_prefix, 0, 2)
        cell("Suffix", self.rn_suffix, 0, 3)
        cell("Case", self.rn_case, 1, 0)
        cell("Spaces", self.rn_spaces, 1, 1)
        cell("", self.rn_num, 1, 2)
        cell("Start at / digits", pair, 1, 3)
        l.addWidget(opts)

        card = Card(margins=(0, 0, 0, 0))
        self.rn_table = make_table(["Current name", "New name", "Status"], (0, 1), sortable=False, bare=True, row_h=44)
        self.rn_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Fixed)
        self.rn_table.setColumnWidth(2, 200)
        self.attach_menu(self.rn_table)
        card.body.addWidget(self.rn_table, 1)
        line = QFrame()
        line.setObjectName("footline")
        line.setFixedHeight(1)
        card.body.addWidget(line)
        foot = QWidget()
        bottom = QHBoxLayout(foot)
        bottom.setContentsMargins(18, 12, 18, 12)
        bottom.setSpacing(12)
        self.rn_status = TLabel("Add files or a folder to begin.")
        self.rn_status.setObjectName("note")
        bottom.addWidget(self.rn_status, 1)
        bottom.addWidget(self.undo_button(arrow=True))
        go = TButton("Apply rename")
        go.setObjectName("primary")
        go.clicked.connect(self.apply_rename)
        self.rn_go = go
        bottom.addWidget(go)
        card.body.addWidget(foot)
        l.addWidget(card, 1)
        return w

    def rn_add_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, tr("Choose files"), self.settings.value("path/rename", str(Path.home()), type=str))
        if files:
            self.settings.setValue("path/rename", str(Path(files[0]).parent))
            self.rn_files = list(dict.fromkeys(self.rn_files + files))
            self.refresh_rename_preview()

    def rn_add_folder(self):
        d = QFileDialog.getExistingDirectory(self, tr("Choose folder"), self.settings.value("path/rename", str(Path.home()), type=str))
        if d:
            self.settings.setValue("path/rename", d)
            files = sorted(str(p) for p in Path(d).iterdir() if p.is_file() and not p.is_symlink())
            self.rn_files = list(dict.fromkeys(self.rn_files + files))
            self.refresh_rename_preview()

    def rn_clear(self):
        self.rn_files = []
        self.refresh_rename_preview()

    def current_rename_plan(self):
        return core.plan_rename(self.rn_files, self.rn_find.text(), self.rn_repl.text(), self.rn_prefix.text(),
                                self.rn_suffix.text(), self.rn_case.currentData(), self.rn_spaces.currentData(),
                                self.rn_num.isChecked(), self.rn_start.value(), self.rn_pad.value())

    RN_STATUS = {"OK": "✓ Ready", "Unchanged": "Unchanged", "Conflict": "⚠ Name conflict",
                 "Invalid": "⚠ Invalid name", "Protected": "⚠ Protected"}

    def refresh_rename_preview(self, *_):
        if not hasattr(self, "rn_table"):
            return
        plan = self.current_rename_plan()
        fill_table(self.rn_table, [[p.name, new, tr(self.RN_STATUS[st])] for p, new, st in plan],
                   userdata=[str(p) for p, _n, _s in plan])
        colors = {"OK": "accent_text", "Unchanged": "muted", "Conflict": "warn_text", "Invalid": "danger_text",
                  "Protected": "danger_text"}
        for r, (_, _, st) in enumerate(plan):
            self.rn_table.item(r, 1).setForeground(QColor(themes.color(colors[st])))
            if st == "Unchanged":
                self.rn_table.item(r, 2).setForeground(QColor(themes.color("muted")))
        ok = sum(1 for x in plan if x[2] == "OK")
        bad = sum(1 for x in plan if x[2] in ("Conflict", "Invalid", "Protected"))
        if not plan:
            self.rn_status.setText(tr("Add files or a folder to begin."))
        else:
            self.rn_status.setText(tr("{n} files · nothing changes until you confirm", n=len(plan), ok=ok)
                                   + (tr(" · {bad} blocked", bad=bad) if bad else ""))

    def apply_rename(self):
        plan = self.current_rename_plan()
        ok = [x for x in plan if x[2] == "OK"]
        if not ok:
            QMessageBox.information(self, tr("Nothing to do"), tr("No files would change."))
            return
        if not self.confirm("Confirm rename", tr("Rename {n} files? Blocked files are skipped. You can undo afterwards.", n=len(ok))):
            return
        if self.busy("move"):
            return

        def done(res):
            moves, errors = res
            for e in errors:
                self.log(f"Rename failed: {e}")
            self.push_undo("Bulk rename", moves)
            new_paths = {str(s): str(d) for s, d in moves}
            self.rn_files = [new_paths.get(f, f) for f in self.rn_files]
            self.log(f"Renamed {len(moves)} files, errors {len(errors)}")
            self.notify(tr("Renamed {n} files", n=len(moves)), "error" if errors else "ok")
            self.refresh_rename_preview()
        self.run_task("move", core.apply_rename, (plan,), done, self.rn_status, self.rn_go)

    # ----------------------------------------------------------------- backup
    def make_backup(self):
        w, l = self.page_shell("Backup", "One-way copy of new and changed files. Never deletes anything in the source or the destination.")
        r1, self.bk_src = self.path_row("backup_src", str(Path.home() / "Documents"))
        r2, self.bk_dst = self.path_row("backup_dst", str(Path.home() / "Backup"))
        for text, row in (("Source", r1), ("Destination", r2)):
            lab = TLabel(text)
            lab.setFixedWidth(96)
            row.insertWidget(0, lab)
        l.addLayout(r1)
        l.addLayout(r2)
        act = QHBoxLayout()
        self.bk_prev = TButton("Preview changes")
        self.bk_prev.clicked.connect(self.bk_preview)
        self.bk_start = TButton("Start backup")
        self.bk_start.setObjectName("primary")
        self.bk_start.setEnabled(False)
        self.bk_start.clicked.connect(self.bk_run)
        self.bk_cancel = self.cancel_button("backup")
        for b in (self.bk_prev, self.bk_start, self.bk_cancel):
            act.addWidget(b)
        act.addStretch()
        l.addLayout(act)
        self.bk_table = make_table(["File to copy", "Size", "Reason"], (0,))
        self.attach_menu(self.bk_table)
        l.addWidget(self.bk_table, 1)
        self.bk_status = TLabel("Choose source and destination, then preview.")
        self.bk_status.setObjectName("muted")
        l.addWidget(self.bk_status)
        return w

    def bk_preview(self):
        src = self.valid_dir(self.bk_src)
        if not src:
            return
        dst = Path(self.bk_dst.text()).expanduser()
        self.backup_plan = None
        self.bk_start.setEnabled(False)
        self.bk_status.setText(tr("Comparing…"))
        self.run_task("backup", core.plan_backup, (src, dst), self.on_bk_preview, self.bk_status,
                      self.bk_prev, self.bk_cancel)

    def on_bk_preview(self, res):
        plan = res["plan"]
        fill_table(self.bk_table, [[s, (fmt_bytes(sz), sz), tr(why)] for s, _t, sz, why in plan[:3000]])
        if res["cancelled"]:
            self.bk_status.setText(tr("Cancelled."))
            return
        self.backup_plan = plan
        self.bk_start.setEnabled(bool(plan))
        if plan:
            more = tr(" (showing first 3000)") if len(plan) > 3000 else ""
            self.bk_status.setText(tr("{n} files to copy · {size}", n=len(plan), size=fmt_bytes(res["bytes"])) + more
                                   + tr(" · {n} files compared", n=res["scanned"]) + self.unr(res))
        else:
            self.bk_status.setText(tr("Destination is already up to date ({n} files compared).", n=res["scanned"]) + self.unr(res))

    def bk_run(self):
        if not self.backup_plan:
            return
        if not self.confirm("Start backup", tr("Copy {n} files to:\n{dst}\n\nNothing will be deleted. Older copies of changed files are replaced.", n=len(self.backup_plan), dst=self.bk_dst.text())):
            return
        self.bk_start.setEnabled(False)
        plan, self.backup_plan = self.backup_plan, None
        self.run_task("backup", core.run_backup, (plan,), self.on_bk_done, self.bk_status, self.bk_prev, self.bk_cancel)

    def on_bk_done(self, res):
        for e in res["errors"]:
            self.log(f"Backup error: {e}")
        state = tr("Cancelled. ") if res["cancelled"] else ""
        self.bk_status.setText(state + tr("Copied {n} files ({size}) · {e} errors", n=res["copied"], size=fmt_bytes(res["bytes"]), e=len(res["errors"])))
        self.log(f"Backup {self.bk_src.text()} → {self.bk_dst.text()}: copied {res['copied']}, errors {len(res['errors'])}")
        self.notify(tr("Backup finished: {n} files", n=res["copied"]), "error" if res["errors"] else "ok")
        self.bk_table.setRowCount(0)

    # ----------------------------------------------------------- similar photos
    def make_similar(self):
        w, l = self.page_shell("Similar Photos", "Finds photos that look alike (burst shots, resized or re-saved copies) using a perceptual hash. The copy with the most pixels is kept; thumbnails help you decide.")
        row, self.sim_path = self.path_row("similar", str(Path.home() / "Pictures"))
        self.sim_level = QComboBox()
        add_items(self.sim_level, ["Strict", "Normal", "Loose"])
        self.sim_level.setCurrentIndex(1)
        self.sim_btn = TButton("Find similar photos")
        self.sim_btn.setObjectName("primary")
        self.sim_btn.clicked.connect(self.scan_similar)
        self.sim_cancel = self.cancel_button("similar")
        for x in (self.sim_level, self.sim_btn, self.sim_cancel):
            row.addWidget(x)
        l.addLayout(row)
        self.sim_table = make_table(["Photo", "Resolution", "Size", "Modified", "Group"], (0,), sortable=False)
        self.sim_table.setIconSize(QSize(48, 48))
        self.sim_table.verticalHeader().setDefaultSectionSize(56)
        self.sim_table.doubleClicked.connect(lambda idx: self.open_row(self.sim_table, idx.row()))
        self.attach_menu(self.sim_table)
        l.addWidget(self.sim_table, 1)
        bottom = QHBoxLayout()
        self.sim_status = TLabel("Choose a folder of photos and scan.")
        self.sim_status.setObjectName("muted")
        bottom.addWidget(self.sim_status, 1)
        for text, fn in (("Select all", lambda: set_all_checked(self.sim_table, True)),
                         ("Select none", lambda: set_all_checked(self.sim_table, False))):
            b = TButton(text)
            b.clicked.connect(fn)
            bottom.addWidget(b)
        bottom.addWidget(self.undo_button())
        rec = TButton("Recycle checked…")
        rec.setObjectName("danger")
        rec.clicked.connect(lambda: self.recycle_checked(self.sim_table, self.sim_status, rec))
        go = TButton("Move checked to _Similar")
        go.setObjectName("primary")
        go.clicked.connect(self.move_similar)
        self.sim_go = go
        bottom.addWidget(rec)
        bottom.addWidget(go)
        l.addLayout(bottom)
        return w

    def thumb_icon(self, path):
        r = QImageReader(path)
        r.setAutoTransform(True)
        s = r.size()
        if s.isValid():
            s.scale(48, 48, Qt.KeepAspectRatio)
            r.setScaledSize(s)
        img = r.read()
        return None if img.isNull() else QIcon(QPixmap.fromImage(img))

    def scan_similar(self):
        root = self.valid_dir(self.sim_path)
        if not root:
            return
        self.sim_root = root
        dist = {"Strict": 2, "Normal": 6, "Loose": 10}[self.sim_level.currentData()]
        self.sim_status.setText(tr("Scanning…"))
        self.run_task("similar", core.find_similar_images, (root, dist), self.on_similar_done,
                      self.sim_status, self.sim_btn, self.sim_cancel)

    def on_similar_done(self, res):
        rows, checks, wasted = [], [], 0
        for gi, files in enumerate(res["groups"], 1):
            wasted += sum(f[1] for f in files[1:])
            for i, (p, size, m, (wd, ht)) in enumerate(files):
                rows.append([p, f"{wd}×{ht}", fmt_bytes(size), datetime.fromtimestamp(m).strftime("%Y-%m-%d %H:%M"),
                             f"#{gi}" + (tr("  ✓ keep") if i == 0 else "")])
                checks.append(i != 0)
        fill_table(self.sim_table, rows, checkable=True, checks=checks)
        for r in range(min(len(rows), 200)):                     # thumbnails for the first 200 rows
            ic = self.thumb_icon(rows[r][0])
            if ic:
                self.sim_table.item(r, 0).setIcon(ic)
        state = tr("Cancelled. ") if res["cancelled"] else ""
        self.sim_status.setText(state + tr("{g} groups · {w} reclaimable · {n} files scanned", g=len(res["groups"]), w=fmt_bytes(wasted), n=res["scanned"]) + self.unr(res))
        self.log(f"Similar photos: {self.sim_root} ({len(res['groups'])} groups)")

    def move_similar(self):
        todo = [self.sim_table.item(r, 0).text() for r in range(self.sim_table.rowCount())
                if self.sim_table.item(r, 0).checkState() == Qt.Checked]
        if not todo:
            QMessageBox.information(self, tr("Nothing to do"), tr("Scan and tick at least one file first."))
            return
        if self.block_if_protected(self.sim_root):
            return
        dest = self.sim_root / "_Similar"
        if not self.confirm("Confirm move", tr("Move {n} duplicate files to:\n{dest}\n\nNothing is deleted.", n=len(todo), dest=dest)):
            return
        if self.busy("move"):
            return

        def after():
            self.sim_table.setRowCount(0)
            self.sim_status.setText(tr("Done. Scan again to refresh."))
        self.run_task("move", core.move_files, ([(p, dest) for p in todo],),
                      lambda res: self.finish_move("Move similar photos", res, after), self.sim_status, self.sim_go)

    def recycle_checked(self, table, status, btn, after=None):
        """Shared by Similar Photos and Cleanup: send checked rows (of one table or a list of tables) to the Recycle Bin."""
        tables = table if isinstance(table, (list, tuple)) else [table]
        todo = []
        for t in tables:
            for r in range(t.rowCount()):
                it = t.item(r, 0)
                if it is not None and it.checkState() == Qt.Checked:
                    todo.append(self.row_path(t, r))
        if not todo:
            QMessageBox.information(self, tr("Nothing to do"), tr("Scan and tick at least one file first."))
            return
        size = sum(os.path.getsize(p) for p in todo if os.path.isfile(p))
        if not self.confirm("Send to Recycle Bin", tr("Send {n} files ({size}) to the Recycle Bin?\nYou can restore them from the Recycle Bin, but this app cannot undo it.", n=len(todo), size=fmt_bytes(size))):
            return
        if self.busy("move"):
            return

        def done(res):
            trashed, errors = res
            for e in errors:
                self.log(f"Recycle failed: {e}")
            self.log(f"Recycled {len(trashed)} items, errors {len(errors)}")
            msg = tr("Recycled {n} files", n=len(trashed))
            if errors:
                msg += tr(" · {n} errors", n=len(errors))
            self.notify(msg, "error" if errors else "ok")
            for t in tables:
                t.setRowCount(0)
            status.setText(tr("Done. Scan again to refresh."))
            self.invalidate_health()
            if after:
                after()
        self.run_task("move", core.trash_files, (todo,), done, status, btn)

    # ---------------------------------------------------------------- cleanup
    def make_cleanup(self):
        opts_btn = TButton("Options…")
        opts_btn.setObjectName("flat")
        opts_btn.setCursor(Qt.PointingHandCursor)
        w, l = self.page_shell("Cleanup", "Finds files you can remove. They go to the Recycle Bin, never deleted for good.",
                               action=opts_btn)
        self.cl_root, self.cl_scanned = None, False
        # scan options (folder, size, age) sit in a small dialog; the page itself stays as designed
        self.cl_opts = QDialog(self)
        self.cl_opts.setWindowTitle(tr("Options…"))
        ol = QVBoxLayout(self.cl_opts)
        ol.addWidget(TLabel("Folder to scan"))
        prow, self.cl_path = self.path_row("cleanup", str(Path.home() / "Downloads"))
        ol.addLayout(prow)
        self.cl_min = QSpinBox()
        self.cl_min.setRange(1, 100000)
        self.cl_min.setValue(self.settings.value("cl_min", 100, type=int))
        self.cl_min.setSuffix(tr(" MB min"))
        self.cl_days = QSpinBox()
        self.cl_days.setRange(1, 3650)
        self.cl_days.setValue(self.settings.value("cl_days", 180, type=int))
        self.cl_days.setSuffix(tr(" days unused"))
        ol.addWidget(TLabel("Large files: at least"))
        ol.addWidget(self.cl_min)
        ol.addWidget(TLabel("Large files: not opened for"))
        ol.addWidget(self.cl_days)
        ok = TButton("Close")
        ok.clicked.connect(self.cl_opts.accept)
        ol.addWidget(ok, 0, Qt.AlignRight)
        opts_btn.clicked.connect(self.cl_opts.exec)

        cards = QHBoxLayout()
        cards.setSpacing(20)
        self.cl_tables = {}
        for kind, title, hint in (("temp", "Temporary files", ""),
                                  ("old_large", "Large files not opened lately", "Left unticked, you choose"),
                                  ("empty_dir", "Empty folders", "")):
            card = Card(title, hint)
            t = make_table(["Item", "Size"], (0,), sortable=False, bare=True, row_h=58, header=False)
            t.setItemDelegateForColumn(0, TwoLineDelegate(t))
            t.itemChanged.connect(lambda _i: self.update_cl_button())
            t.doubleClicked.connect(lambda idx, tt=t: self.open_row(tt, idx.row()))
            self.attach_menu(t)
            card.body.addWidget(t)
            self.cl_tables[kind] = t
            cards.addWidget(card, 1)
        l.addLayout(cards, 1)

        bottom, self.cl_status = self.footer("Everything goes to the Recycle Bin and can be restored from there (not undoable in this app).")
        self.cl_cancel = self.cancel_button("cleanup")
        bottom.addWidget(self.cl_cancel)
        self.cl_btn = TButton("Rescan")
        self.cl_btn.clicked.connect(self.scan_cleanup)
        bottom.addWidget(self.cl_btn)
        rec = TButton("Send to Recycle Bin")
        rec.setObjectName("primary")
        rec.clicked.connect(lambda: self.recycle_checked(list(self.cl_tables.values()), self.cl_status, rec, self.after_cleanup))
        self.cl_go = rec
        bottom.addWidget(rec)
        l.addLayout(bottom)
        return w

    def after_cleanup(self):
        self.update_cl_button()
        self.scan_cleanup()

    def update_cl_button(self):
        size = 0
        for t in self.cl_tables.values():
            for r in range(t.rowCount()):
                it = t.item(r, 0)
                if it is not None and it.checkState() == Qt.Checked:
                    size += t.item(r, 1).data(SORT_ROLE) or 0
        self.cl_go.setText(tr("Send to Recycle Bin {size}", size=fmt_bytes(size)) if size else tr("Send to Recycle Bin"))

    def scan_cleanup(self):
        root = self.valid_dir(self.cl_path)
        if not root:
            return
        self.settings.setValue("cl_min", self.cl_min.value())
        self.settings.setValue("cl_days", self.cl_days.value())
        self.cl_root, self.cl_scanned = root, True
        self.cl_status.setText(tr("Scanning…"))
        self.run_task("cleanup", core.find_cleanup, (root, self.cl_min.value(), self.cl_days.value()),
                      self.on_cleanup_done, self.cl_status, self.cl_btn, self.cl_cancel)

    def on_cleanup_done(self, res):
        now = time.time()
        base = self.cl_root.parent if self.cl_root and self.cl_root.parent != self.cl_root else self.cl_root

        def rel(p):
            try:
                return str(Path(p).relative_to(base))
            except ValueError:
                return str(p)
        by_kind = {"temp": [], "old_large": [], "empty_dir": []}
        for k, p, sz, m in sorted(res["items"], key=lambda it: it[2], reverse=True):
            by_kind[k].append((p, sz, m))
        for kind, t in self.cl_tables.items():
            items = by_kind[kind]
            rows, subs = [], []
            for p, sz, m in items:
                if kind == "temp":
                    sub = rel(Path(p).parent)
                elif kind == "old_large":
                    sub = tr("Not opened for {n} days", n=max(int((now - m) // 86400), 0))
                else:
                    sub = tr("No files inside")
                rows.append([Path(p).name if kind != "empty_dir" else rel(p), (fmt_bytes(sz), sz)])
                subs.append(sub)
            t.blockSignals(True)
            if rows:
                fill_table(t, rows, checkable=True, checks=[kind != "old_large"] * len(rows), userdata=[p for p, _s, _m in items])
                for r, sub in enumerate(subs):
                    t.item(r, 0).setData(SUB_ROLE, sub)
                    it = t.item(r, 1)
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    it.setForeground(QColor(themes.color("muted")))
            else:
                fill_table(t, [[tr("Nothing found"), ("", 0)]])
                t.item(0, 0).setFlags(Qt.ItemIsEnabled)
                t.item(0, 0).setForeground(QColor(themes.color("muted")))
            t.blockSignals(False)
        self.update_cl_button()
        note = tr("Everything goes to the Recycle Bin and can be restored from there (not undoable in this app).")
        self.cl_status.setText((tr("Cancelled. ") if res["cancelled"] else "") + note + self.unr(res))
        self.log(f"Cleanup scan: {self.cl_root} ({len(res['items'])} items)")

    # -------------------------------------------------------------- processes
    def make_procs(self):
        w, l = self.page_shell("Processes", "Read-only view of what is using your CPU and memory (refreshes every 3 s).")
        row = QHBoxLayout()
        self.proc_filter = QLineEdit()
        self.proc_filter.setPlaceholderText(tr("Filter by name…"))
        self.proc_filter.textChanged.connect(self.refresh_procs)
        self.proc_sort = QComboBox()
        add_items(self.proc_sort, ["Sort: Memory", "Sort: CPU", "Sort: Name"])
        self.proc_sort.currentIndexChanged.connect(self.refresh_procs)
        row.addWidget(self.proc_filter, 1)
        row.addWidget(self.proc_sort)
        l.addLayout(row)
        self.proc_table = make_table(["Name", "PID", "CPU %", "Memory"], (0,), sortable=False)
        l.addWidget(self.proc_table, 1)
        self.proc_status = TLabel("")
        self.proc_status.setObjectName("muted")
        l.addWidget(self.proc_status)
        return w

    def refresh_procs_if_visible(self):
        if self.page_keys[self.stack.currentIndex()] == "procs":
            self.refresh_procs()

    def refresh_procs(self, *_):
        if not hasattr(self, "proc_table"):
            return
        rows = []
        cpus = max(psutil.cpu_count(logical=True) or 1, 1)
        for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_info"]):
            try:
                mi = p.info["memory_info"]
                rows.append((p.info["name"] or "?", p.info["pid"], (p.info["cpu_percent"] or 0.0) / cpus,
                             mi.rss if mi else 0))
            except (psutil.Error, AttributeError):
                continue
        q = self.proc_filter.text().lower()
        if q:
            rows = [r for r in rows if q in r[0].lower()]
        mode = self.proc_sort.currentData() or ""
        if "CPU" in mode:
            rows.sort(key=lambda r: r[2], reverse=True)
        elif "Name" in mode:
            rows.sort(key=lambda r: r[0].lower())
        else:
            rows.sort(key=lambda r: r[3], reverse=True)
        self.proc_status.setText(tr("{n} processes (showing top 80)", n=len(rows)))
        fill_table(self.proc_table, [[n, pid, f"{c:.1f}", fmt_bytes(m)] for n, pid, c, m in rows[:80]])

    # ---------------------------------------------------------------- network
    def make_network(self):
        w, l = self.page_shell("Network Tools", "Local network details, ping/DNS checks and a single-port TCP check.")
        info, il = self.panel("Local network information")
        self.network_info = QTextEdit()
        self.network_info.setReadOnly(True)
        il.addWidget(self.network_info)
        refresh = TButton("Refresh")
        refresh.clicked.connect(self.refresh_network_info)
        il.addWidget(refresh)
        l.addWidget(info, 1)
        diag, dl = self.panel("Diagnostics")
        line = QHBoxLayout()
        self.net_host = QLineEdit(self.settings.value("net_host", "1.1.1.1", type=str))
        self.net_host.setPlaceholderText(tr("Hostname or IP"))
        self.net_port = QSpinBox()
        self.net_port.setRange(1, 65535)
        self.net_port.setValue(443)
        self.net_port.setPrefix(tr("port "))
        self.ping_btn = TButton("Ping + DNS")
        self.ping_btn.setObjectName("primary")
        self.ping_btn.clicked.connect(self.run_ping)
        self.port_btn = TButton("Check port")
        self.port_btn.clicked.connect(self.run_port)
        for x, s in ((self.net_host, 1), (self.net_port, 0), (self.ping_btn, 0), (self.port_btn, 0)):
            line.addWidget(x, s)
        dl.addLayout(line)
        self.net_result = QTextEdit()
        self.net_result.setReadOnly(True)
        dl.addWidget(self.net_result)
        l.addWidget(diag, 1)
        self.refresh_network_info()
        return w

    def refresh_network_info(self):
        lines = [tr("Computer name: {v}", v=socket.gethostname()), tr("Primary local IP: {v}", v=self.local_ip)]
        try:
            stats = psutil.net_if_stats()
            for iface, addrs in psutil.net_if_addrs().items():
                details = []
                for a in addrs:
                    if a.family == socket.AF_INET:
                        details.append(f"IPv4 {a.address} / {a.netmask}")
                    elif a.family == socket.AF_INET6:
                        details.append(f"IPv6 {a.address}")
                    elif getattr(a.family, "name", "") in ("AF_LINK", "AF_PACKET"):
                        details.append(f"MAC {a.address}")
                if details:
                    st = stats.get(iface)
                    head = iface + (f"  ({tr('up') if st.isup else tr('down')}, {st.speed} Mbps)" if st else "")
                    lines.append(f"\n{head}\n  " + "\n  ".join(details))
        except Exception as e:
            lines.append(tr("Could not read network info: {e}", e=e))
        self.network_info.setPlainText("\n".join(lines))

    def run_net(self, key, fn, args, btn):
        self.settings.setValue("net_host", self.net_host.text())
        self.net_result.setPlainText(tr("Working…"))

        def done(text):
            self.net_result.setPlainText(text)
            self.log(f"{key} {self.net_host.text().strip()}")
        self.run_task(key, fn, args, done, None, btn)

    def run_ping(self):
        host = self.net_host.text().strip()
        if not core.valid_host(host):
            self.warn("Invalid host", tr("Enter a valid hostname or IP address."))
            return
        self.run_net("ping", core.ping_host, (host,), self.ping_btn)

    def run_port(self):
        host = self.net_host.text().strip()
        if not core.valid_host(host):
            self.warn("Invalid host", tr("Enter a valid hostname or IP address."))
            return
        self.run_net("port", core.check_port, (host, self.net_port.value()), self.port_btn)

    # ----------------------------------------------------------------- system
    def make_system(self):
        w, l = self.page_shell("System Info", "Read-only overview of operating system, hardware and storage.")
        panel, pl = self.panel()
        self.system_text = QTextEdit()
        self.system_text.setReadOnly(True)
        pl.addWidget(self.system_text)
        row = QHBoxLayout()
        r = TButton("Refresh")
        r.clicked.connect(self.refresh_system)
        c = TButton("Copy report")
        c.clicked.connect(lambda: (QGuiApplication.clipboard().setText(self.system_text.toPlainText()),
                                   self.notify(tr("Copied to clipboard"), "ok")))
        row.addWidget(r)
        row.addWidget(c)
        row.addStretch()
        pl.addLayout(row)
        l.addWidget(panel, 1)
        self.refresh_system()
        return w

    def refresh_system(self):
        try:
            vm = psutil.virtual_memory()
            boot = psutil.boot_time()
            lines = [f"{tr('OS')}: {platform.system()} {platform.release()}",
                     f"{tr('Version')}: {platform.version()}",
                     f"{tr('Architecture')}: {platform.machine()}",
                     f"{tr('Processor')}: {platform.processor() or tr('Not reported')}",
                     f"{tr('CPU cores')}: {psutil.cpu_count(logical=False) or '?'} {tr('physical')} / {psutil.cpu_count(logical=True) or '?'} {tr('logical')}",
                     f"RAM: {fmt_bytes(vm.total)} {tr('total')} | {fmt_bytes(vm.available)} {tr('available')}",
                     f"Python: {platform.python_version()}",
                     f"{tr('Boot time')}: {datetime.fromtimestamp(boot).strftime('%Y-%m-%d %H:%M:%S')}",
                     f"{tr('Uptime')}: {self.duration(time.time() - boot)}", "", tr("Disk partitions:")]
            for part in psutil.disk_partitions(all=False):
                try:
                    u = psutil.disk_usage(part.mountpoint)
                    lines.append(f"  {part.device} ({part.mountpoint}) — " + tr("{free} free / {total} total ({pct}% used)", free=fmt_bytes(u.free), total=fmt_bytes(u.total), pct=f"{u.percent:.0f}"))
                except (PermissionError, OSError):
                    lines.append(f"  {part.device} ({part.mountpoint}) — " + tr("access unavailable"))
            self.system_text.setPlainText("\n".join(lines))
        except Exception as e:
            self.system_text.setPlainText(tr("Unable to read system information: {e}", e=e))

    # -------------------------------------------------------------------- log
    def make_log(self):
        w, l = self.page_shell("Activity Log", "Saved between sessions (log entries are kept in English).")
        self.log_search = QLineEdit()
        self.log_search.setPlaceholderText(tr("Filter the log…"))
        self.log_search.textChanged.connect(self.filter_log)
        l.addWidget(self.log_search)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setPlainText("\n".join(self.logs))
        l.addWidget(self.log_text, 1)
        self.db_stats = TLabel("")
        self.db_stats.setObjectName("muted")
        l.addWidget(self.db_stats)
        self.refresh_db_stats()
        row = QHBoxLayout()
        for text, fn in (("Clear log", self.clear_log), ("Export log…", self.export_log)):
            b = TButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch()
        l.addLayout(row)
        return w

    def filter_log(self, text):
        self.log_text.setPlainText("\n".join(self.db.recent_logs(MAX_LOG_LINES, text.strip()) if text.strip() else self.logs))

    def refresh_db_stats(self):
        if hasattr(self, "db_stats"):
            try:
                s = self.db.stats()
            except Exception:                         # database already closed during shutdown
                return
            self.db_stats.setText(tr("Database: {actions} actions · {files} files moved · {scans} scans · {hashes} cached hashes",
                                     actions=s["actions"], files=s["files_moved"], scans=s["scans"], hashes=s["cached_hashes"]))

    def clear_log(self):
        if not self.confirm("Clear log", tr("Delete the saved activity log?")):
            return
        self.logs.clear()
        self.log_text.clear()
        self.recent_label.setText(tr("No activity yet."))
        self.db.clear_logs()

    def export_log(self):
        path, _ = QFileDialog.getSaveFileName(self, tr("Export log…"), "smart_file_manager_log.txt", "Text (*.txt)")
        if path:
            try:
                Path(path).write_text("\n".join(self.logs), encoding="utf-8")
                self.notify(tr("Log exported"), "ok")
            except OSError as e:
                self.warn("Export failed", str(e))

    # --------------------------------------------------------------- settings
    REFRESH_CHOICES = (1, 2, 3, 5, 10)

    def make_settings(self):
        w, l = self.page_shell("Settings", "Theme, language, font and refresh rate")
        theme_card = Card("Theme", divider=False)
        row = QHBoxLayout()
        row.setSpacing(12)
        row.setContentsMargins(0, 14, 0, 0)
        self.theme_group = QButtonGroup(self)
        self.theme_group.setExclusive(True)
        self.theme_buttons = {}
        for name in themes.THEME_ORDER:
            b = TButton(name)
            b.setObjectName("chip")
            b.setCheckable(True)
            b.setIcon(swatch_icon(name))
            b.setIconSize(QSize(24, 24))
            b.setCursor(Qt.PointingHandCursor)
            b.setChecked(name == themes.name)
            b.clicked.connect(lambda checked=False, n=name: self.apply_theme(n))
            self.theme_group.addButton(b)
            self.theme_buttons[name] = b
            row.addWidget(b)
        row.addStretch()
        theme_card.body.addLayout(row)
        l.addWidget(theme_card)

        cards = QHBoxLayout()
        cards.setSpacing(20)
        lang_card = Card("Language", divider=False)
        lrow = QHBoxLayout()
        lrow.setSpacing(10)
        lrow.setContentsMargins(0, 14, 0, 0)
        self.lang_group = QButtonGroup(self)
        self.lang_group.setExclusive(True)
        for code, name in i18n.LANGS.items():
            b = QPushButton(name)
            b.setObjectName("chip")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setChecked(code == i18n.lang)
            b.clicked.connect(lambda checked=False, c=code: self.on_lang_changed(c))
            self.lang_group.addButton(b)
            lrow.addWidget(b)
        lrow.addStretch()
        lang_card.body.addLayout(lrow)
        lang_card.body.addSpacing(12)
        note = TLabel("You need to restart after changing this.")
        note.setObjectName("note")
        lang_card.body.addWidget(note)
        self.restart_btn = TButton("Restart now")
        self.restart_btn.setObjectName("primary")
        self.restart_btn.setVisible(False)
        self.restart_btn.clicked.connect(self.restart_app)
        lang_card.body.addSpacing(10)
        lang_card.body.addWidget(self.restart_btn, 0, Qt.AlignLeft)
        lang_card.body.addStretch(1)

        font_card = Card("Font", divider=False)
        fl = font_card.body
        fl.addSpacing(10)
        fl.addWidget(self.field_label("Font name"))
        self.font_combo = QComboBox()
        families = sorted(set(QFontDatabase.families()), key=str.lower)
        if themes.BUNDLED not in families:
            families.insert(0, themes.BUNDLED)
        self.font_combo.addItems(families)
        self.font_combo.setCurrentText(themes.font["family"] or themes.BUNDLED)
        self.font_combo.currentTextChanged.connect(self.on_font_changed)
        fl.addWidget(self.font_combo)
        fl.addSpacing(12)
        fl.addWidget(self.field_label("Size"))
        self.font_size = IntField(8, 20, themes.font["size"])
        self.font_size.setFixedWidth(40)
        self.font_size.valueChanged.connect(self.on_font_changed)
        size_box = QFrame()
        size_box.setObjectName("pairfield")
        sbl = QHBoxLayout(size_box)
        sbl.setContentsMargins(10, 0, 10, 0)
        sbl.setSpacing(0)
        sbl.addWidget(self.font_size)
        sbl.addWidget(QLabel("pt"))
        sbl.addStretch()
        fl.addWidget(size_box)
        fl.addStretch(1)

        refresh_card = Card("Refresh", divider=False)
        rl = refresh_card.body
        rl.addSpacing(10)
        rl.addWidget(self.field_label("Live stats update rate"))
        self.refresh_combo = QComboBox()
        for sec in self.REFRESH_CHOICES:
            self.refresh_combo.addItem(tr("Every {n} seconds", n=sec) if sec > 1 else tr("Every 1 second"), sec)
        cur = self.settings.value("refresh_sec", 2, type=int)
        self.refresh_combo.setCurrentIndex(max(self.refresh_combo.findData(cur), 0))
        self.refresh_combo.currentIndexChanged.connect(lambda _i: self.set_refresh(self.refresh_combo.currentData()))
        rl.addWidget(self.refresh_combo)
        rl.addStretch(1)

        for c in (lang_card, font_card, refresh_card):
            cards.addWidget(c, 1)
        l.addLayout(cards)
        l.addStretch(1)
        bottom, _note = self.footer("Saved automatically")
        reset = TButton("Reset to defaults")
        reset.clicked.connect(self.reset_settings)
        bottom.addWidget(reset)
        l.addLayout(bottom)
        return w

    def field_label(self, text):
        lab = TLabel(text)
        lab.setObjectName("fieldLabel")
        return lab

    def on_lang_changed(self, code):
        self.settings.setValue("lang", code)
        self.restart_btn.setVisible(code != i18n.lang)
        if code != i18n.lang:
            self.notify(tr("Restart the app to apply the language"), "info")

    def on_font_changed(self, *_):
        fam = self.font_combo.currentText()
        themes.set_font("" if fam == themes.BUNDLED else fam, self.font_size.value())
        self.settings.setValue("font_family", themes.font["family"])
        self.settings.setValue("font_size", themes.font["size"])
        self.apply_theme(themes.name, save=False)

    def restart_app(self):
        frozen = getattr(sys, "frozen", False)
        args = sys.argv[1:] if frozen else sys.argv
        QProcess.startDetached(sys.executable, args)
        self.quit_app()

    def set_refresh(self, v):
        self.settings.setValue("refresh_sec", v)
        self.timer.start(v * 1000)

    def reset_settings(self):
        if self.confirm("Reset", tr("Forget saved folders and settings? (The language resets on next launch.)")):
            self.stop_watch()
            self.settings.clear()
            self.watch_check.blockSignals(True)
            self.watch_check.setChecked(False)
            self.watch_check.blockSignals(False)
            themes.set_font("", themes.defaults["size"])
            self.font_combo.blockSignals(True)
            self.font_size.blockSignals(True)
            self.font_combo.setCurrentText(themes.BUNDLED)
            self.font_size.setValue(themes.defaults["size"])
            self.font_combo.blockSignals(False)
            self.font_size.blockSignals(False)
            self.theme_buttons["Light"].setChecked(True)
            self.apply_theme("Light", save=False)
            self.notify(tr("Settings reset"), "ok")

    # -------------------------------------------------------------- lifecycle
    def closeEvent(self, event):
        if (self.watch_dir and self.tray and self.tray.isVisible() and not self._quitting
                and self.isVisible()):
            event.ignore()
            self.hide()
            self.tray.showMessage(APP_NAME, tr("Still running in the tray to auto-organize. Right-click the tray icon to quit."))
            return
        self.timer.stop()
        self.proc_timer.stop()
        self.stop_watch(keep_setting=True)
        self.settings.setValue("geometry", self.saveGeometry())
        running = [w for w in self.workers.values() if w.isRunning()]
        for w in running:
            w.cancel()
        for w in running:
            w.wait(9000)
        if self.tray:
            self.tray.hide()
        self.db.close()
        event.accept()
        QApplication.quit()


class CrashBridge(QObject):
    crashed = Signal(str, str)


def main():
    app = QApplication(sys.argv)
    bridge = CrashBridge()
    bridge.crashed.connect(lambda err, path: QMessageBox.critical(
        None, tr("Unexpected error"),
        tr("Something went wrong: {e}\n\nA report was saved to:\n{path}\nPlease send that file if you report this problem.", e=err, path=path)))
    crash.install(LOG_DIR, APP_NAME, VERSION,
                  lambda path, et, ex: bridge.crashed.emit(f"{et.__name__}: {ex}", str(path or "(could not be written)")))
    app.setStyle("Fusion")
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)       # keep running in the tray while auto-organize is on
    load_bundled_fonts()
    app.setFont(QFont(themes.BUNDLED, 10))
    app.setWindowIcon(QIcon(make_app_pixmap()))
    window = MainWindow()
    window.show()
    if "--selftest" in sys.argv:                 # used by CI: prove the app starts, then exit cleanly
        def finish():
            window._quitting = True
            window.close()
        QTimer.singleShot(1500, finish)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
