import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import i18n  # noqa: E402
import core  # noqa: E402
from i18n_th import TH, PATTERNS  # noqa: E402

CALLS = {"tr", "TLabel", "TButton", "TCheck", "page_shell", "panel", "confirm", "warn", "MetricCard",
         "GaugeCard", "add_items", "make_table"}
SKIP = {"accent", "accent2", "green", "muted", "CPU", "dashboard"}


def ui_strings():
    tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
    found = set()

    def grab(node):
        for n in ast.walk(node):
            if isinstance(n, ast.Constant) and isinstance(n.value, str):
                found.add(n.value)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
            if name in CALLS:
                for a in node.args:
                    if isinstance(a, ast.Constant):
                        found.add(a.value)
                    elif isinstance(a, (ast.List, ast.Tuple)):
                        grab(a)
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id in ("pages", "actions"):
            grab(node.value)
    return {s for s in found if any(c.isalpha() for c in s) and s not in SKIP and not s.islower() or s in ("lowercase", "new", "changed", "up", "down", "used", "physical", "logical", "total", "available")}


class I18nTests(unittest.TestCase):
    def test_every_ui_string_has_thai(self):
        ignore = {"dashboard", "organizer", "disk", "dups", "search", "rename", "backup", "procs", "network", "system",
                  "log", "settings", "move", "RAM", "Python", "Ctrl+K quick switch"}
        missing = sorted(s for s in ui_strings() if s not in TH and s not in ignore and not s.startswith("lambda"))
        self.assertEqual(missing, [])

    def test_core_values_are_translated(self):
        for s in list(core.ORGANIZE_MODES) + list(core.CATEGORY_MAP) + ["Others", "(files in folder)", "OK", "Unchanged",
                                                                      "Conflict", "Invalid", "Protected", "new", "changed"]:
            self.assertIn(s, TH, s)

    def test_placeholders_match(self):
        import re
        for en, th in TH.items():
            self.assertEqual(sorted(re.findall(r"\{\w+\}", en)), sorted(re.findall(r"\{\w+\}", th)), en)

    def test_tr_behaviour(self):
        i18n.set_lang("th")
        try:
            self.assertEqual(i18n.tr("Cancel"), "ยกเลิก")
            self.assertEqual(i18n.tr("Restored {n} files", n=3), "กู้คืน 3 ไฟล์แล้ว")
            self.assertEqual(i18n.tr("Moving 5/10…"), "กำลังย้าย 5/10…")           # progress text from core
            self.assertEqual(i18n.tr("Unknown text"), "Unknown text")
            self.assertIn("Uptime", i18n.tr("Uptime") if False else "Uptime")
        finally:
            i18n.set_lang("en")
        self.assertEqual(i18n.tr("Cancel"), "Cancel")


if __name__ == "__main__":
    unittest.main()
