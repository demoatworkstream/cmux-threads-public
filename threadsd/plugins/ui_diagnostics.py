"""UI adapter "diagnostics": crash and error reports from the apps, kept on this Mac.

POST /ui/diagnostics with one report -> stored as JSON in ~/.local/share/threadsd/diagnostics/
  {kind: "crash"|"metrickit"|"error", app, version, build, os, device, at, text?, events?, payload?,
   trace_id?}  (trace_id: the voice call that was running; the call store links it to that call)
  The Threads iOS app sends what the last run left behind (its recent events and the stack
  at the crash, or Apple's MetricKit report) at the next launch, and non-fatal errors as
  they happen. Reports over 4 MB are refused. The newest 500 are kept.
GET /ui/diagnostics -> {"reports": [{file, kind, at, build, area?, first_line}]}, newest first.
Nothing is sent anywhere else."""
import json
import os
import time

from . import UIS, UI, register_ui  # noqa: F401
from ._common import DIAGNOSTICS

DIR = DIAGNOSTICS
KEEP = 500
KINDS = ("crash", "metrickit", "error")


@register_ui
class Diagnostics(UI):
    name = "diagnostics"

    def render(self, snap):
        out = []
        for f in sorted(os.listdir(DIR), reverse=True)[:50] if os.path.isdir(DIR) else []:
            try:
                with open(os.path.join(DIR, f)) as fh:
                    r = json.load(fh)
            except (OSError, ValueError):
                continue
            text = r.get("text") or ""
            out.append({"file": f, "kind": r.get("kind"), "at": r.get("at"), "build": r.get("build"),
                        "area": r.get("area"), "trace_id": r.get("trace_id"),
                        "first_line": text.strip().split("\n")[0][:200]})
        return {"reports": out}

    def handle(self, state, body, client, send):
        kind = body.get("kind")
        if kind not in KINDS:
            return 400, {"error": f"kind must be one of {', '.join(KINDS)}"}
        data = json.dumps(dict(body, client=client), ensure_ascii=False)
        if len(data) > 4 << 20:
            return 413, {"error": "report too large"}
        os.makedirs(DIR, exist_ok=True)
        name = f"{time.strftime('%Y%m%d-%H%M%S')}-{int(time.time() * 1000) % 1000:03d}-{kind}.json"
        with open(os.path.join(DIR, name), "w") as fh:
            fh.write(data)
        for old in sorted(os.listdir(DIR))[:-KEEP]:
            os.remove(os.path.join(DIR, old))
        return 200, {"stored": name}
