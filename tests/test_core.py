import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import core  # noqa: E402
import crash  # noqa: E402

PROT = os.environ.get("WINDIR", r"C:\Windows") if os.name == "nt" else "/usr"   # a system folder on this OS


def make(d, name, content="x"):
    p = Path(d) / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


class CoreTests(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.d = Path(self._t.name)

    def tearDown(self):
        self._t.cleanup()

    def test_fmt_and_categorize(self):
        self.assertEqual(core.fmt_bytes(1536), "1.5 KB")
        self.assertEqual(core.categorize("a/b/pic.JPG"), "Images")
        self.assertEqual(core.categorize("run.py"), "Code")
        self.assertEqual(core.categorize("noext"), "Others")

    def test_unique_dest(self):
        make(self.d, "a.txt")
        self.assertEqual(core.unique_dest(self.d, "a.txt").name, "a (1).txt")
        self.assertEqual(core.unique_dest(self.d, "b.txt").name, "b.txt")

    def test_organize_move_undo(self):
        make(self.d, "a.jpg"), make(self.d, "b.pdf"), make(self.d, "c.crdownload"), make(self.d, ".hidden")
        make(self.d, "Documents/b.pdf", "other")
        plan = core.plan_organize(self.d)
        self.assertEqual({p.name for p, *_ in plan}, {"a.jpg", "b.pdf"})
        moves, skipped, errors = core.move_files([(p, dest) for p, _c, _s, dest in plan])
        self.assertEqual((len(moves), errors), (2, []))
        self.assertEqual((self.d / "Documents" / "b.pdf").read_text(), "other")   # not overwritten
        self.assertTrue((self.d / "Documents" / "b (1).pdf").exists())
        restored, _, errs = core.undo_moves(moves)
        self.assertEqual((restored, errs), (2, []))
        self.assertTrue((self.d / "a.jpg").exists() and not (self.d / "Images").exists())
        self.assertEqual((self.d / "Documents" / "b.pdf").read_text(), "other")

    def test_analyze_disk(self):
        make(self.d, "big.bin", "x" * 5000), make(self.d, "sub/small.txt", "y"), make(self.d, "sub/mid.txt", "z" * 100)
        r = core.analyze_disk(self.d, limit=2)
        self.assertEqual([os.path.basename(p) for _s, p, _m in r["top"]], ["big.bin", "mid.txt"])
        self.assertEqual(r["scanned"], 3)
        self.assertIn("sub", r["folders"])

    def test_duplicates(self):
        make(self.d, "a.dat", "same" * 100), make(self.d, "b.dat", "same" * 100), make(self.d, "c.dat", "diff" * 100)
        r = core.find_duplicates(self.d, 1)
        self.assertEqual(len(r["groups"]), 1)
        self.assertEqual({os.path.basename(p) for p, _ in r["groups"][0][1]}, {"a.dat", "b.dat"})

    def test_search(self):
        make(self.d, "invoice_1.pdf"), make(self.d, "notes.txt")
        self.assertEqual(len(core.search_files(self.d, "invoice")["results"]), 1)
        self.assertEqual(len(core.search_files(self.d, "*.txt")["results"]), 1)

    def test_rename(self):
        files = [make(self.d, "My File.txt"), make(self.d, "Other.txt")]
        plan = core.plan_rename(files, spaces="Underscore", case="lowercase")
        self.assertEqual([x[1] for x in plan], ["my_file.txt", "other.txt"])
        plan = core.plan_rename(files, find="Other", replace="My File")
        self.assertEqual(plan[1][2], "Conflict")           # collides with an existing file
        plan = core.plan_rename(files, number=True, pad=2)
        self.assertEqual(plan[1][1], "Other_02.txt")
        plan = core.plan_rename(files, find="My", replace="a/b")
        self.assertEqual(plan[0][2], "Invalid")
        plan = core.plan_rename(files, prefix="x_")
        moves, errors = core.apply_rename(plan)
        self.assertEqual(len(moves), 2)
        self.assertTrue((self.d / "x_Other.txt").exists())
        core.undo_moves(moves)
        self.assertTrue((self.d / "Other.txt").exists())

    def test_backup(self):
        src, dst = self.d / "src", self.d / "dst"
        make(src, "a.txt", "1"), make(src, "sub/b.txt", "2")
        res = core.plan_backup(src, dst)
        self.assertEqual(len(res["plan"]), 2)
        out = core.run_backup(res["plan"])
        self.assertEqual((out["copied"], out["errors"]), (2, []))
        self.assertEqual(len(core.plan_backup(src, dst)["plan"]), 0)      # up to date
        (dst / "extra.txt").write_text("keep me")
        core.run_backup(core.plan_backup(src, dst)["plan"])
        self.assertTrue((dst / "extra.txt").exists())                      # never deletes
        with self.assertRaises(ValueError):
            core.plan_backup(src, src / "inside")

    def test_network_validation(self):
        self.assertFalse(core.valid_host("-oProxy"))
        self.assertFalse(core.valid_host("a b"))
        self.assertTrue(core.valid_host("example.com"))
        self.assertIn("between 1 and 65535", core.check_port("localhost", 99999))



class SafetyTests(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.d = Path(self._t.name)

    def tearDown(self):
        self._t.cleanup()

    def test_protected_folders(self):
        self.assertIsNotNone(core.protected_reason("/"))
        self.assertIsNotNone(core.protected_reason(os.path.join(PROT, "bin")))
        self.assertIsNone(core.protected_reason(self.d))

    def test_move_refuses_protected_destination(self):
        f = make(self.d, "a.txt")
        moves, skipped, errors = core.move_files([(f, os.path.join(PROT, "sfm_test"))])
        self.assertEqual((moves, len(errors)), ([], 1))
        self.assertTrue(f.exists())

    def test_backup_refuses_protected_destination(self):
        make(self.d, "src/a.txt")
        with self.assertRaises(ValueError):
            core.plan_backup(self.d / "src", os.path.join(PROT, "sfm_backup"))

    def test_unreadable_are_counted(self):
        errs = []
        list(core.walk_files("/definitely/not/here", errors=errs))
        self.assertEqual(len(errs), 1)
        self.assertEqual(core.analyze_disk("/definitely/not/here")["unreadable"], 1)

    def test_journal_roundtrip_and_stale_entries(self):
        a, b = make(self.d, "x/a.txt"), self.d / "gone.txt"
        j = self.d / "journal.json"
        core.save_journal(j, [("kept", [(self.d / "orig.txt", a)]), ("stale", [(self.d / "o2", b)])])
        loaded = core.load_journal(j)
        self.assertEqual([label for label, _ in loaded], ["kept"])      # entries whose files vanished are dropped
        self.assertEqual(core.load_journal(self.d / "missing.json"), [])
        j.write_text("not json")
        self.assertEqual(core.load_journal(j), [])

    def test_rules(self):
        rules = core.parse_rules([{"pattern": "*.PSD", "folder": "Design"}, {"pattern": "invoice", "folder": "Money"},
                                  {"pattern": "x", "folder": "../evil"}, {"pattern": "", "folder": "Nope"}])
        self.assertEqual(rules, [("*.psd", "Design"), ("invoice", "Money")])     # traversal + empty rejected
        make(self.d, "logo.psd"), make(self.d, "my invoice 3.pdf"), make(self.d, "pic.jpg")
        plan = {p.name: dest.name for p, _c, _s, dest in core.plan_organize(self.d, "By type", rules)}
        self.assertEqual(plan, {"logo.psd": "Design", "my invoice 3.pdf": "Money", "pic.jpg": "Images"})

    def test_rule_folders_are_tidied_on_undo(self):
        core.set_extra_dirs([("*.psd", "Design")])
        try:
            f = make(self.d, "logo.psd")
            moves, _, _ = core.move_files([(f, self.d / "Design")])
            core.undo_moves(moves)
            self.assertFalse((self.d / "Design").exists())
        finally:
            core.set_extra_dirs([])

    def test_rename_protected_status(self):
        plan = core.plan_rename([os.path.join(PROT, "some_file.txt")], prefix="x_")
        self.assertEqual(plan[0][2], "Protected")

    def test_trash(self):
        try:
            import send2trash  # noqa: F401
        except ImportError:
            self.skipTest("send2trash not installed")
        f = make(self.d, "junk.txt")
        try:
            done, errors = core.trash_files([str(f)])
        except Exception as e:                                   # headless CI without a trash dir
            self.skipTest(f"trash unavailable here: {e}")
        if errors:
            self.skipTest(f"trash unavailable here: {errors}")
        self.assertEqual(len(done), 1)
        self.assertFalse(f.exists())


class CleanupAndPhotoTests(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.d = Path(self._t.name)

    def tearDown(self):
        self._t.cleanup()

    def age(self, path, days):
        t = __import__("time").time() - days * 86400
        os.utime(path, (t, t))

    def test_cleanup_finds_the_right_things(self):
        old_tmp, fresh_tmp = make(self.d, "a.tmp"), make(self.d, "b.tmp")
        self.age(old_tmp, 3)
        big = make(self.d, "movie.bin", "x" * 2_000_000)
        self.age(big, 400)
        recent_big = make(self.d, "recent.bin", "x" * 2_000_000)
        (self.d / "e1" / "e2").mkdir(parents=True)
        make(self.d, "full/keep.txt")
        res = core.find_cleanup(self.d, min_mb=1, old_days=180)
        got = {(k, os.path.basename(p)) for k, p, *_ in res["items"]}
        self.assertEqual(got, {("temp", "a.tmp"), ("old_large", "movie.bin"), ("empty_dir", "e1")})   # e2 not listed separately

    def test_partial_downloads_need_a_week(self):
        f = make(self.d, "x.crdownload")
        self.age(f, 3)
        self.assertEqual(core.find_cleanup(self.d)["items"], [])
        self.age(f, 10)
        self.assertEqual(len(core.find_cleanup(self.d)["items"]), 1)

    def _jpeg(self, name, taken=None, color=(200, 100, 50)):
        from PIL import Image
        img = Image.new("RGB", (64, 64), color)
        ex = Image.Exif()
        if taken:
            ex.get_ifd(0x8769)[0x9003] = taken
        p = self.d / name
        img.save(p, exif=ex)
        return p

    def test_exif_date_and_photo_mode(self):
        try:
            import PIL  # noqa: F401
        except ImportError:
            self.skipTest("Pillow not installed")
        a = self._jpeg("a.jpg", "2019:05:04 10:00:00")
        b = self._jpeg("b.jpg")
        self.assertEqual(core.exif_date(a).strftime("%Y-%m"), "2019-05")
        self.assertIsNone(core.exif_date(b))
        plan = {p.name: dest for p, _c, _s, dest in core.plan_organize(self.d, "Photos by date taken")}
        self.assertEqual(plan["a.jpg"], self.d / "Images" / "2019-05")
        self.assertEqual(plan["b.jpg"].parent, self.d / "Images")

    def test_similar_images(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow not installed")
        import random
        grad = Image.new("L", (240, 240))
        grad.putdata([int(255 * (1 - (i % 240) / 240)) for i in range(240 * 240)])
        grad.convert("RGB").save(self.d / "orig.png")
        grad.resize((120, 120)).convert("RGB").save(self.d / "small.jpg", quality=70)
        Image.new("RGB", (300, 300), (30, 30, 30)).save(self.d / "flat_dark.png")      # flat image must NOT match a gradient
        Image.new("RGB", (300, 300), (240, 240, 240)).save(self.d / "flat_light.png")  # ...nor another flat image of different brightness
        rnd = random.Random(7)
        noise = Image.new("L", (240, 240))
        noise.putdata([rnd.randint(0, 255) for _ in range(240 * 240)])
        noise.convert("RGB").save(self.d / "noise.png")
        res = core.find_similar_images(self.d, 6)
        self.assertEqual(len(res["groups"]), 1)
        names = [os.path.basename(f[0]) for f in res["groups"][0]]
        self.assertEqual(names, ["orig.png", "small.jpg"])            # biggest first = the one to keep

    def test_crash_report_is_written(self):
        try:
            raise ValueError("boom")
        except ValueError as e:
            path = crash.write_report(self.d, "App", "1.0", type(e), e, e.__traceback__)
        text = Path(path).read_text(encoding="utf-8")
        self.assertIn("ValueError: boom", text)
        self.assertIn("App v1.0", text)



class TidyScoreTests(unittest.TestCase):
    def test_clean_folder_scores_100_and_clutter_lowers_it(self):
        self.assertEqual(core.tidy_score(), 100)
        self.assertLess(core.tidy_score(loose=200), 100)
        self.assertLess(core.tidy_score(loose=200, dup_files=10, temp_bytes=2 * 1024 ** 3), core.tidy_score(loose=200))
        self.assertGreaterEqual(core.tidy_score(loose=10 ** 6, dup_files=10 ** 6, temp_bytes=10 ** 12, old_large_bytes=10 ** 12, empty_dirs=999), 0)

    def test_health_summary_counts_loose_and_duplicates(self):
        d = Path(tempfile.mkdtemp())
        (d / "a.dat").write_bytes(b"x" * 300)
        (d / "b.dat").write_bytes(b"x" * 300)
        (d / "c.jpg").write_bytes(b"y")
        res = core.health_summary(d, min_dup_size=1)
        self.assertEqual(res["loose"], 3)
        self.assertEqual(res["dup_files"], 1)
        self.assertEqual(res["score"], core.tidy_score(3, 1, 300))


if __name__ == "__main__":
    unittest.main()
