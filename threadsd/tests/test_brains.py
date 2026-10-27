"""GET /voice/brains: the mac provider reads brains.json and reports health.
Run: python3 threadsd/tests/test_brains.py"""
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from plugins import brain_mac  # noqa: E402


class Models(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"data": [{"id": "default_model"}]}).encode()
        self.send_response(200 if self.path == "/v1/models" else 404)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class BrainsTest(unittest.TestCase):
    def setUp(self):
        self.server = HTTPServer(("127.0.0.1", 0), Models)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.dir = tempfile.mkdtemp()
        brain_mac.PATH = os.path.join(self.dir, "brains.json")
        self.provider = brain_mac.Mac()

    def tearDown(self):
        self.server.shutdown()

    def write(self, rows):
        with open(brain_mac.PATH, "w") as f:
            json.dump(rows, f)

    def test_no_config_lists_nothing(self):
        self.assertEqual(self.provider.list(), [])

    def test_up_and_down(self):
        up = f"http://127.0.0.1:{self.server.server_port}"
        self.write([{"id": "a", "name": "Mac brain", "url": up}, {"id": "b", "name": "Gone", "url": "http://127.0.0.1:9"}])
        a, b = self.provider.list()
        self.assertTrue(a["up"])
        self.assertIsInstance(a["latency_ms"], int)
        self.assertEqual((a["id"], a["name"], a["model"], a["api"]), ("a", "Mac brain", "default_model", "openai"))
        self.assertFalse(b["up"])
        self.assertIsNone(b["latency_ms"])
        self.assertIn("error", b)

    def test_cached_between_checks(self):
        up = f"http://127.0.0.1:{self.server.server_port}"
        self.write([{"url": up}])
        first = self.provider.list()[0]["last_checked"]
        self.server.shutdown()
        again = self.provider.list()[0]
        self.assertTrue(again["up"])  # within the TTL, the last check stands
        self.assertEqual(again["last_checked"], first)

    def test_bad_config_is_ignored(self):
        with open(brain_mac.PATH, "w") as f:
            f.write("{not json")
        self.assertEqual(self.provider.list(), [])


if __name__ == "__main__":
    unittest.main()
