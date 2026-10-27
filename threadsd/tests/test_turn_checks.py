"""POST/GET /ui/turn-checks: Smart Turn clips from calls are stored with an index.
Run: python3 threadsd/tests/test_turn_checks.py"""
import base64
import json
import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from plugins import ui_turn_checks  # noqa: E402


def wav(n=160):
    data = b"\0\0" * n
    return (b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, 16000, 32000, 2, 16)
            + b"data" + struct.pack("<I", len(data)) + data)


class TurnChecksTest(unittest.TestCase):
    def setUp(self):
        ui_turn_checks.DIR = tempfile.mkdtemp()
        self.ui = ui_turn_checks.TurnChecks()

    def check(self, **kw):
        body = {"name": "abc-001-shadow", "wav_base64": base64.b64encode(wav()).decode(), "trace_id": "abc", "n": 1,
                "stage": "shadow", "p_complete": 0.31, "partial": "explain to me how to", "resumed": True}
        body.update(kw)
        return self.ui.handle(None, body, "100.1.2.3 Threads", None)

    def test_store_and_list(self):
        code, body = self.check()
        self.assertEqual((code, body["stored"]), (200, "abc-001-shadow.wav"))
        self.assertTrue(os.path.exists(os.path.join(ui_turn_checks.DIR, "abc-001-shadow.wav")))
        with open(os.path.join(ui_turn_checks.DIR, "index.jsonl")) as fh:
            row = json.loads(fh.readline())
        self.assertNotIn("wav_base64", row)
        self.assertEqual((row["p_complete"], row["resumed"], row["wav"]), (0.31, True, "abc-001-shadow.wav"))
        r = self.ui.render(None)
        self.assertEqual((r["count"], r["recent"][0]["partial"]), (1, "explain to me how to"))

    def test_bad_name_refused(self):
        self.assertEqual(self.check(name="../etc/x")[0], 400)
        self.assertEqual(self.check(name=".hidden")[0], 400)

    def test_not_wav_refused(self):
        self.assertEqual(self.check(wav_base64=base64.b64encode(b"hello").decode())[0], 400)
        self.assertEqual(self.check(wav_base64="!!")[0], 400)

    def test_too_large_refused(self):
        self.assertEqual(self.check(wav_base64=base64.b64encode(wav(2 << 20)).decode())[0], 413)


if __name__ == "__main__":
    unittest.main()
