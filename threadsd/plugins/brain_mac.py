"""Brain provider "mac": the voice brains served on this Mac (mlx_lm.server, Ollama, any
OpenAI-compatible server), listed in ~/.config/threadsd/brains.json:

  [{"id": "mac-35b", "name": "Mac brain · Qwen3.6 35B", "url": "http://100.x.y.z:8091",
    "model": "default_model", "api": "openai"}]

`url` is what the phone calls, so it must be reachable over Tailscale. threadsd checks each
one itself (GET /v1/models, or /api/tags for "api": "ollama") at most every 15 s, and
reports up/down and the round trip. Nothing here is specific to a model.
"""
import json
import os
import threading
import time
import urllib.request

from . import Brain, register_brain
from ._common import CONF

PATH = os.path.join(CONF, "brains.json")
TTL = 15


@register_brain
class Mac(Brain):
    name = "mac"

    def __init__(self):
        self.lock = threading.Lock()
        self.cache = {}  # url -> (checked_at, up, latency_ms, error)

    def configured(self):
        try:
            with open(PATH) as f:
                rows = json.load(f)
        except (OSError, ValueError):
            return []
        return [r for r in rows if isinstance(r, dict) and r.get("url")]

    def probe(self, url, api):
        path = "/api/tags" if api == "ollama" else "/v1/models"
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(url.rstrip("/") + path, timeout=3) as r:
                r.read(4096)
            return True, int((time.monotonic() - t0) * 1000), None
        except Exception as e:  # down, refused, timed out: all just "down"
            return False, None, str(e)[:120]

    def list(self):
        out = []
        for r in self.configured():
            url, api = r["url"], r.get("api", "openai")
            with self.lock:
                hit = self.cache.get(url)
            if not hit or time.time() - hit[0] > TTL:
                hit = (time.time(), *self.probe(url, api))
                with self.lock:
                    self.cache[url] = hit
            checked, up, latency, error = hit
            row = {"id": r.get("id") or url, "name": r.get("name") or url, "url": url,
                   "model": r.get("model", "default_model"), "api": api, "up": up, "latency_ms": latency,
                   "last_checked": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(checked)), "provider": self.name}
            if error:
                row["error"] = error
            out.append(row)
        return out
