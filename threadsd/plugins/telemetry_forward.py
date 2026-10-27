"""Telemetry sink "forward": also send every trace export to other OTLP/HTTP backends you run
(self-hosted Langfuse, Jaeger, an OTel Collector), listed in ~/.config/threadsd/telemetry.json:

  {"forward": [{"name": "langfuse", "url": "http://127.0.0.1:3000/api/public/otel/v1/traces",
                "headers": {"Authorization": "Basic <base64 public:secret>"}}]}

Off when the file is missing. What's sent is the same redacted OTLP JSON the call store keeps.
Only backends on this Mac or the tailnet are accepted (localhost, 100.64.0.0/10, *.ts.net):
nothing leaves the user's machines. Sends run in the background and never slow the phone down;
a backend that's down loses those exports (the call store still has them: replay from
`voice-traces.db` table `otlp`).
"""
import ipaddress
import json
import os
import queue
import threading
import urllib.request
from urllib.parse import urlparse

from . import Telemetry, register_telemetry
from ._common import CONF, log

PATH = os.path.join(CONF, "telemetry.json")


def allowed(url):
    """Local or tailnet only."""
    host = (urlparse(url).hostname or "").lower()
    if host in ("localhost",) or host.endswith(".ts.net"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip in ipaddress.ip_network("100.64.0.0/10")


def targets():
    try:
        with open(PATH) as f:
            rows = json.load(f).get("forward") or []
    except (OSError, ValueError, AttributeError):
        return []
    out = []
    for r in rows:
        if isinstance(r, dict) and r.get("url"):
            if allowed(r["url"]):
                out.append(r)
            else:
                log(f"telemetry: not forwarding to {r['url']}: only localhost or the tailnet")
    return out


@register_telemetry
class Forward(Telemetry):
    name = "forward"

    def __init__(self):
        self.q = queue.Queue(maxsize=200)
        self.thread = None

    def ingest(self, doc, meta):
        if not targets():
            return
        if not self.thread:
            self.thread = threading.Thread(target=self.loop, daemon=True)
            self.thread.start()
        try:
            self.q.put_nowait(json.dumps(doc).encode())
        except queue.Full:
            log("telemetry: forward queue full; dropped one export")

    def loop(self):
        while True:
            body = self.q.get()
            for t in targets():
                req = urllib.request.Request(t["url"], data=body, method="POST",
                                             headers=dict(t.get("headers") or {}, **{"Content-Type": "application/json"}))
                try:
                    urllib.request.urlopen(req, timeout=10).read()
                except Exception as e:
                    log(f"telemetry: forward to {t.get('name') or t['url']} failed: {str(e)[:160]}")
