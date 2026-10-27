"""POST/GET /ui/diagnostics: reports from the apps are stored and listed.
Run: python3 threadsd/tests/test_diagnostics.py"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from plugins import ui_diagnostics  # noqa: E402


class DiagnosticsTest(unittest.TestCase):
    def setUp(self):
        ui_diagnostics.DIR = tempfile.mkdtemp()
        self.ui = ui_diagnostics.Diagnostics()

    def test_store_and_list(self):
        code, body = self.ui.handle(None, {"kind": "crash", "build": "7", "text": "--- Crashed: signal 6 ---\nStack:"}, "100.1.2.3 Threads", None)
        self.assertEqual(code, 200)
        self.assertTrue(os.path.exists(os.path.join(ui_diagnostics.DIR, body["stored"])))
        r = self.ui.render(None)["reports"][0]
        self.assertEqual((r["kind"], r["build"], r["first_line"]), ("crash", "7", "--- Crashed: signal 6 ---"))

    def test_unknown_kind_refused(self):
        self.assertEqual(self.ui.handle(None, {"kind": "spam"}, "c", None)[0], 400)

    def test_too_large_refused(self):
        self.assertEqual(self.ui.handle(None, {"kind": "error", "text": "x" * (5 << 20)}, "c", None)[0], 413)


if __name__ == "__main__":
    unittest.main()
