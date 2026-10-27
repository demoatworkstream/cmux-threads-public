"""Status source "cmux": tab titles/marks, surfaces and ttys (cmux tree), the agent process
running on each tty (ps), and each workspace's cmux status entries (cmux list-status)."""
import json
import os
import re
import time

from . import Source, register_source
from ._cmux import cmux
from ._common import log, run


@register_source
class CmuxSource(Source):
    name = "cmux"
    interval = 3.0
    status_interval = 6.0

    def __init__(self):
        self.last_status = 0

    def poll(self, ctx):
        rc, out, err = cmux("tree", "--all", "--json")
        if rc != 0:
            log("cmux tree failed:", err.strip()[:200])
            ctx.cmux_error = err.strip()[:200]
            return
        ctx.cmux_error = None
        try:
            d = json.loads(out)
        except Exception:
            return
        by_tty, surfaces, wss = {}, [], {}
        for w in d.get("windows", []):
            for ws in w.get("workspaces", []):
                wss[ws["ref"]] = ws.get("title") or ""
                for pane in ws.get("panes", []):
                    for s in pane.get("surfaces", []):
                        if s.get("type") != "terminal":
                            continue
                        rec = {"surface": s["ref"], "workspace": ws["ref"], "title": s.get("title") or "",
                               "tty": s.get("tty")}
                        surfaces.append(rec)
                        if rec["tty"]:
                            by_tty[rec["tty"]] = rec
        procs = {}  # tty -> [(pid, executable basename)]
        rc, out, _ = run(["ps", "-A", "-o", "pid=,tty=,args="])
        for line in out.split("\n"):
            parts = line.split(None, 2)
            if len(parts) == 3 and parts[1] in by_tty:
                procs.setdefault(parts[1], []).append((int(parts[0]), os.path.basename(parts[2].split()[0])))
        ctx.surfaces, ctx.by_tty, ctx.workspaces, ctx.procs = surfaces, by_tty, wss, procs
        if time.time() - self.last_status >= self.status_interval:
            self.last_status = time.time()
            status = {}
            for ws in wss:
                rc, out, _ = cmux("list-status", "--workspace", ws)
                status[ws] = dict(re.findall(r"^(\w+)=(.*?)(?:\s+icon=.*)?$", out, re.M))
            ctx.ws_status = status
