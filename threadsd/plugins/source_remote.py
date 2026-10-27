"""Status source "remote-machine": other Macs' threadsd (phase 3: webshop-mac).
~/.config/threadsd/peers.json = [
  {"name": "webshop-mac", "url": "http://100.x.y.z:7878", "token": "..."},     # runs threadsd
  {"name": "mac2", "sendmessage": ["⌂ Root · webshop @mac2"]}                  # Remote Control only
]
A peer with a url: its /state sessions are merged in as-is (each carries its own "machine"),
with transport "threadsd-http", so /send is forwarded to the daemon that owns the session.
A peer with only "sendmessage" names: one row per name, state unknown ("idle"), with
transport "sendmessage-bridge" (the ◎ Master forwards with SendMessage).
Result: ctx.remote_rows, complete Session rows."""
import json
import os
import urllib.request

from . import Source, register_source
from ._common import PEERS_PATH, log


@register_source
class RemoteSource(Source):
    name = "remote-machine"
    interval = 5.0

    def poll(self, ctx):
        try:
            with open(PEERS_PATH) as f:
                peers = json.load(f)
        except (OSError, ValueError):
            ctx.remote_rows = []
            return
        rows = []
        for p in peers:
            for n in p.get("sendmessage", []):
                rows.append({"id": f"rc:{n}", "name": n, "title": n, "agent": "claude", "machine": p["name"],
                             "role": "root" if n.startswith("⌂ Root") else "thread", "surface": None,
                             "workspace": None, "cwd": None, "state": "idle", "mark": "○", "last_message": "",
                             "questions": [], "transport": "sendmessage-bridge", "remote": True})
            if not p.get("url"):
                continue
            try:
                req = urllib.request.Request(p["url"].rstrip("/") + "/state",
                                             headers={"Authorization": f"Bearer {p['token']}"})
                with urllib.request.urlopen(req, timeout=4) as r:
                    st = json.load(r)
            except Exception as e:
                log("remote-machine:", p.get("name"), "unreachable:", repr(e)[:120])
                continue
            for s in st.get("sessions", []):
                if s.get("transport") == "cmux-send" or "transport" not in s:  # don't re-export a peer's peers
                    rows.append(dict(s, transport="threadsd-http", remote=True, _peer=p))
        ctx.remote_rows = rows
