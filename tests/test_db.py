import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import core  # noqa: E402
import db  # noqa: E402


def make(d, name, text="x"):
    p = Path(d) / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


class DbTests(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.db = db.Database(self.d / "t.db")

    def tearDown(self):
        self.db.close()

    def test_undo_push_pop_and_reload(self):
        a, b = make(self.d, "a.txt"), make(self.d, "b.txt")
        self.db.push_undo("first", [(self.d / "o1", a)])
        self.db.push_undo("second", [(self.d / "o2", b)])
        self.assertEqual([l for l, _ in self.db.load_undo_stack()], ["first", "second"])
        self.db.pop_undo()
        self.assertEqual([l for l, _ in self.db.load_undo_stack()], ["first"])
        self.assertEqual(self.db.undo_history()[0][3], "undone")          # history is kept

    def test_stale_batches_are_dropped(self):
        a = make(self.d, "a.txt")
        self.db.push_undo("kept", [(self.d / "o", a)])
        self.db.push_undo("gone", [(self.d / "o2", self.d / "missing.txt")])
        self.assertEqual([l for l, _ in self.db.load_undo_stack()], ["kept"])
        self.assertEqual(self.db.undo_history()[0][3], "stale")

    def test_logs_filter_and_clear(self):
        self.db.add_log("Moved 3 files")
        self.db.add_log("Rules saved")
        self.assertEqual(len(self.db.recent_logs()), 2)
        self.assertEqual(len(self.db.recent_logs(query="moved")), 1)
        self.assertEqual(self.db.recent_logs(query="100%"), [])           # wildcard characters are escaped
        self.db.clear_logs()
        self.assertEqual(self.db.recent_logs(), [])

    def test_rules_keep_order(self):
        self.db.set_rules([("*.psd", "Design"), ("invoice", "Money")])
        self.db.set_rules([("invoice", "Money"), ("*.psd", "Design")])
        self.assertEqual(self.db.get_rules(), [("invoice", "Money"), ("*.psd", "Design")])

    def test_hash_cache_skips_unchanged_files(self):
        make(self.d, "s/a.dat", "same" * 100), make(self.d, "s/b.dat", "same" * 100)
        c = self.db.hash_cache
        r1 = core.find_duplicates(self.d / "s", 1, cache=c)
        self.assertEqual((c.hits, len(r1["groups"])), (0, 1))
        c.reset_counters()
        r2 = core.find_duplicates(self.d / "s", 1, cache=c)
        self.assertEqual((c.misses, len(r2["groups"])), (0, 1))          # second scan: everything from the cache
        c.reset_counters()
        make(self.d, "s/b.dat", "diff" * 100)                            # file changed -> rehashed
        os.utime(self.d / "s/b.dat", (1, 1))
        self.assertEqual(core.find_duplicates(self.d / "s", 1, cache=c)["groups"], [])

    def test_scan_history_and_stats(self):
        self.db.add_scan("duplicates", "/x", 10, 2, 3, 999, 4, 5, 0.5)
        s = self.db.stats()
        self.assertEqual((s["scans"], s["wasted_bytes"]), (1, 999))
        self.assertEqual(self.db.scan_history()[0][1], "duplicates")

    def test_legacy_migration_runs_once(self):
        a = make(self.d, "a.txt")
        j, lg = self.d / "j.json", self.d / "a.log"
        j.write_text(json.dumps([{"label": "old", "moves": [[str(self.d / "o"), str(a)]]}]))
        lg.write_text("[2026-01-01 10:00:00] hello\nplain line\n")
        self.db.migrate_legacy(j, lg, [("*.psd", "Design")])
        self.db.migrate_legacy(j, lg, [("*.psd", "Design")])             # second call does nothing
        self.assertEqual([l for l, _ in self.db.load_undo_stack()], ["old"])
        self.assertEqual(self.db.recent_logs()[1], "[2026-01-01 10:00:00] hello")
        self.assertEqual(len(self.db.recent_logs()), 2)
        self.assertEqual(self.db.get_rules(), [("*.psd", "Design")])


if __name__ == "__main__":
    unittest.main()
