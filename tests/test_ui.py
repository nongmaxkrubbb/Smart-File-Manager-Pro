"""UI smoke tests (headless). Skipped automatically if PySide6/psutil are missing."""
import os
import sys
import time
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["HOME"] = os.environ["USERPROFILE"] = tempfile.mkdtemp()   # keep settings/logs out of the real profile
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from PySide6.QtWidgets import QApplication, QMessageBox
    from PySide6.QtCore import Qt
    import app as appmod
    HAVE_QT = True
except Exception:       # pragma: no cover
    HAVE_QT = False


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class UiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qa = QApplication.instance() or QApplication([])
        cls.qa.setStyle("Fusion")
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
        QMessageBox.information = staticmethod(lambda *a, **k: None)
        QMessageBox.warning = staticmethod(lambda *a, **k: None)

    def setUp(self):
        self.w = appmod.MainWindow()
        self.d = Path(tempfile.mkdtemp())

    def tearDown(self):
        self.w.close()

    def wait(self, key):
        t = time.time()
        while self.w.workers[key].isRunning() and time.time() - t < 20:
            self.qa.processEvents()
        for _ in range(5):
            self.qa.processEvents()

    def test_every_page_builds_and_themes_apply(self):
        for i in range(self.w.stack.count()):
            self.w.change_page(i)
        for name in appmod.themes.THEMES:
            self.w.apply_theme(name, save=False)
        self.assertEqual(self.w.stack.count(), 14)

    def test_organize_then_undo(self):
        (self.d / "a.jpg").write_text("x")
        (self.d / "b.pdf").write_text("y")
        self.w.org_path.setText(str(self.d))
        self.w.scan_organizer()
        self.assertEqual(self.w.org_table.rowCount(), 2)
        self.w.organize_files()
        self.wait("move")
        self.assertTrue((self.d / "Images" / "a.jpg").exists())
        self.w.undo_last()
        self.wait("move")
        self.assertTrue((self.d / "a.jpg").exists())
        self.assertFalse((self.d / "Images").exists())

    def test_duplicates_flow(self):
        (self.d / "one.dat").write_text("same" * 50)
        (self.d / "two.dat").write_text("same" * 50)
        self.w.dup_path.setText(str(self.d))
        self.w.dup_min.setValue(0)
        self.w.scan_dups()
        self.wait("dups")
        self.assertEqual(self.w.dup_table.rowCount(), 3)       # 1 group header + 2 files
        self.w.move_dups()
        self.wait("move")
        self.assertEqual(len(list((self.d / "_Duplicates").iterdir())), 1)
        self.assertEqual(len([p for p in self.d.glob("*.dat")]), 1)      # the kept copy

    def test_rename_flow(self):
        files = [self.d / "My File.txt", self.d / "Other.txt"]
        for f in files:
            f.write_text("z")
        self.w.rn_files = [str(f) for f in files]
        self.w.rn_prefix.setText("p_")
        self.w.apply_rename()
        self.wait("move")
        self.assertTrue((self.d / "p_Other.txt").exists())
        self.w.undo_last()
        self.wait("move")
        self.assertTrue((self.d / "Other.txt").exists())

    def test_backup_flow(self):
        src, dst = self.d / "src", self.d / "dst"
        src.mkdir()
        (src / "a.txt").write_text("1")
        self.w.bk_src.setText(str(src))
        self.w.bk_dst.setText(str(dst))
        self.w.bk_preview()
        self.wait("backup")
        self.assertTrue(self.w.bk_start.isEnabled())
        self.w.bk_run()
        self.wait("backup")
        self.assertTrue((dst / "a.txt").exists())

    def test_disk_and_search(self):
        (self.d / "big.bin").write_bytes(b"x" * 5000)
        self.w.disk_path.setText(str(self.d))
        self.w.scan_disk()
        self.wait("disk")
        self.assertEqual(self.w.disk_table.rowCount(), 1)
        self.w.search_path.setText(str(self.d))
        self.w.search_query.setText("*.bin")
        self.w.run_search()
        self.wait("search")
        self.assertEqual(self.w.search_table.rowCount(), 1)

    def test_sorting_is_numeric_and_rows_keep_their_data(self):
        for name, size in (("a.bin", 900), ("b.bin", 10), ("c.bin", 5000)):
            (self.d / name).write_bytes(b"x" * size)
        self.w.disk_path.setText(str(self.d))
        self.w.scan_disk()
        self.wait("disk")
        t = self.w.disk_table
        t.sortByColumn(1, Qt.AscendingOrder)
        self.assertEqual([Path(t.item(r, 0).text()).name for r in range(3)], ["b.bin", "a.bin", "c.bin"])
        t.sortByColumn(1, Qt.DescendingOrder)
        self.assertEqual(Path(t.item(0, 0).text()).name, "c.bin")

    def test_protected_folder_is_blocked_in_organizer(self):
        warned = []
        self.w.warn = lambda title, text: warned.append(title)
        self.w.org_path.setText(os.environ.get("WINDIR", r"C:\Windows") if os.name == "nt" else "/usr")
        self.w.scan_organizer()
        self.assertEqual(warned, ["Protected folder"])
        self.assertEqual(self.w.org_table.rowCount(), 0)

    def test_custom_rule_applies_in_organizer(self):
        (self.d / "logo.psd").write_text("x")
        self.w.rules = appmod.core.parse_rules([{"pattern": "*.psd", "folder": "Design"}])
        self.w.org_path.setText(str(self.d))
        self.w.scan_organizer()
        self.assertIn("Design", self.w.org_table.item(0, 0).data(Qt.UserRole)[1])

    def test_undo_survives_restart_via_database(self):
        (self.d / "a.jpg").write_text("x")
        self.w.org_path.setText(str(self.d))
        self.w.scan_organizer()
        self.w.organize_files()
        self.wait("move")
        self.assertTrue(appmod.DB_FILE.exists())
        w2 = appmod.MainWindow()                       # "restart"
        try:
            self.assertEqual(len(w2.undo_stack), len(self.w.undo_stack))
            self.assertTrue(any(label.startswith("Organize") for label, _ in w2.undo_stack))
        finally:
            w2.close()

    def test_auto_organize_only_moves_settled_files(self):
        old, fresh = self.d / "old.jpg", self.d / "fresh.jpg"
        old.write_text("x")
        fresh.write_text("y")
        past = time.time() - 60
        os.utime(old, (past, past))
        self.w.watch_dir = self.d
        self.w.auto_organize()
        self.wait("move")
        self.assertTrue((self.d / "Images" / "old.jpg").exists())
        self.assertTrue(fresh.exists())                # still "being written": left alone
        self.w.watch_dir = None

    def test_context_menu_refuses_to_run_executables(self):
        exe = self.d / "setup.exe"
        exe.write_text("x")
        notes = []
        self.w.notify = lambda text, kind="info": notes.append(kind)
        self.w.open_file(str(exe))
        self.assertEqual(notes, ["warn"])

    def test_thai_ui_has_no_leftover_english(self):
        import re
        from PySide6.QtWidgets import QLabel, QPushButton, QCheckBox
        from PySide6.QtCore import QSettings
        st = QSettings("SmartFileManagerPro", "SmartFileManagerPro")
        st.setValue("lang", "th")                        # MainWindow reads the language from settings
        try:
            w = appmod.MainWindow()
            for i in range(w.stack.count()):
                w.change_page(i)
            self.qa.processEvents()
            allowed = re.compile(r"^(English|Steam|CPU|RAM|PID|CSV|IPv\d|SHA|Ping|DNS|TCP|Ctrl|[^A-Za-z]*)$|^CPU %$|^CPU \d+%\s+·\s+RAM \d+%$|^Ping \+ DNS$|Smart|SMART|Ctrl|\.\.\.|Python|v\d|[\u0e00-\u0e7f]")
            left = []
            for cls in (QLabel, QPushButton, QCheckBox):
                for wd in w.findChildren(cls):
                    t = wd.text().strip()
                    if t and re.search(r"[A-Za-z]{3,}", t) and not allowed.search(t) and not t.startswith("["):
                        left.append(t)
            for t in w.findChildren(appmod.QTableWidget):
                for c in range(t.columnCount()):
                    h = t.horizontalHeaderItem(c).text()
                    if re.search(r"[A-Za-z]{3,}", h) and not allowed.search(h):
                        left.append(h)
            w.close()
            self.assertEqual(sorted(set(left)), [])
        finally:
            st.setValue("lang", "en")
            appmod.i18n.set_lang("en")

    def test_theme_and_font_choices_apply_and_persist(self):
        self.w.apply_theme("Neon Violet")
        self.assertEqual(self.w.settings.value("theme"), "Neon Violet")
        self.assertIn("#0b1020", self.w.styleSheet())
        self.w.font_size.setValue(13)
        self.assertEqual(appmod.themes.font["size"], 13)
        self.assertEqual(self.w.settings.value("font_size", type=int), 13)
        self.w.apply_theme("Light")
        self.w.font_size.setValue(10)

    def test_sidebar_has_rail_tiles_and_more_menu_pages(self):
        self.assertEqual(len(self.w.nav_by_key), len(appmod.RAIL_KEYS) + 1)      # + settings
        for key in appmod.MORE_KEYS:
            self.w.goto(key)
            self.assertTrue(self.w.more_btn.isChecked())
        self.w.goto("dashboard")
        self.assertFalse(self.w.more_btn.isChecked())

    def test_dashboard_score_and_suggestions(self):
        (self.d / "a.jpg").write_text("x")
        self.w.settings.setValue("path/organizer", str(self.d))
        self.w.refresh_health()
        self.wait("health")
        self.assertIsNotNone(self.w.health)
        self.assertEqual(self.w.score_ring.value, self.w.health["score"])

    def test_organizer_preview_refreshes_by_itself_and_button_counts_ticked_files(self):
        for n in ("a.jpg", "b.pdf", "c.mp4"):
            (self.d / n).write_text("x")
        self.w.org_path.setText(str(self.d))
        self.w.goto("organizer")
        self.assertEqual(self.w.org_table.rowCount(), 3)
        self.assertIn("3", self.w.org_go.text())
        self.w.org_table.item(0, 0).setCheckState(Qt.Unchecked)
        self.assertIn("2", self.w.org_go.text())
        self.w.set_org_mode("By month")
        self.assertEqual(self.w.org_table.rowCount(), 3)

    def test_cleanup_page_prechecks_safe_items_only(self):
        tmp, big = self.d / "x.tmp", self.d / "huge.bin"
        tmp.write_text("t")
        big.write_bytes(b"x" * 2_000_000)
        old = time.time() - 400 * 86400
        for f in (tmp, big):
            os.utime(f, (old, old))
        self.w.cl_path.setText(str(self.d))
        self.w.cl_min.setValue(1)
        self.w.cl_days.setValue(180)
        self.w.scan_cleanup()
        self.wait("cleanup")
        state = {}
        for t in self.w.cl_tables.values():
            for r in range(t.rowCount()):
                if t.item(r, 0).flags() & Qt.ItemIsUserCheckable:
                    state[Path(self.w.row_path(t, r)).name] = t.item(r, 0).checkState()
        self.assertEqual(state, {"huge.bin": Qt.Unchecked, "x.tmp": Qt.Checked})

    def test_similar_page_lists_groups_with_thumbnails(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow not installed")
        g = Image.new("L", (240, 240))
        g.putdata([int(255 * (1 - (i % 240) / 240)) for i in range(240 * 240)])
        g.convert("RGB").save(self.d / "a.png")
        g.resize((120, 120)).convert("RGB").save(self.d / "b.jpg")
        self.w.sim_path.setText(str(self.d))
        self.w.scan_similar()
        self.wait("similar")
        t = self.w.sim_table
        self.assertEqual(t.rowCount(), 2)
        self.assertEqual(t.item(0, 0).checkState(), Qt.Unchecked)       # the larger copy is kept
        self.assertEqual(t.item(1, 0).checkState(), Qt.Checked)
        self.assertFalse(t.item(0, 0).icon().isNull())


if __name__ == "__main__":
    unittest.main()
