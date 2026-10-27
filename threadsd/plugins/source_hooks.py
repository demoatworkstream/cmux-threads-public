"""Status source "hooks": Claude Code hook events spooled by hook.sh (SessionStart,
UserPromptSubmit, Stop, Notification, and PreToolUse/PostToolUse for AskUserQuestion). Each event
is stored in sqlite and folded into ctx.hooks[session_id] = {state, at, transcript_path, cwd, prompt}."""
import glob
import json
import os
import time

from . import Source, register_source
from ._common import SPOOL, iso, log

EVENT_STATE = {"SessionStart": "idle", "UserPromptSubmit": "working", "Stop": "idle"}


@register_source
class HooksSource(Source):
    name = "hooks"
    interval = 0.5

    def poll(self, ctx):
        for f in sorted(glob.glob(os.path.join(SPOOL, "*.json"))):
            try:
                mtime = os.stat(f).st_mtime  # sub-second, unlike the hook's own date +%s
                with open(f, "rb") as fh:
                    raw = fh.read()
                os.unlink(f)
            except OSError:
                continue
            try:
                ev = json.loads(raw)
            except Exception:
                log("spool: bad json", f, raw[:200])
                continue
            p = ev.get("payload") or {}
            sid, name = p.get("session_id"), ev.get("event") or p.get("hook_event_name")
            if not sid:
                continue
            ctx.store.x("insert into events(ts, event, session_id, payload) values(?,?,?,?)",
                        (mtime, name, sid, json.dumps(p)[:20000]))
            h = ctx.hooks.setdefault(sid, {"state": None, "at": 0})
            for k in ("transcript_path", "cwd"):
                if p.get(k):
                    h[k] = p[k]
            st = EVENT_STATE.get(name)
            if name == "Notification" and "permission" in ((p.get("notification_type") or "") + " " +
                                                          (p.get("message") or "")).lower():
                st = "needs_permission"
            if p.get("tool_name") == "AskUserQuestion":
                if name == "PreToolUse":
                    from .agent_claude import ask_prompt  # the prompt shape lives with the Claude adapter
                    h["prompt"] = dict(ask_prompt(p.get("tool_use_id") or f"hook:{mtime}", p.get("tool_input") or {},
                                                  iso(mtime)), source="hooks")
                else:
                    h.pop("prompt", None)
            if name in ("Stop", "UserPromptSubmit", "SessionStart"):
                h.pop("prompt", None)  # a turn boundary: no dialog can still be open
            if st and mtime >= (h.get("at") or 0):
                h["state"], h["at"] = st, mtime
            ctx.store.x("insert or replace into hookstate values(?,?,?,?,?,?)",
                        (sid, h.get("state"), h.get("at"), h.get("transcript_path"), h.get("cwd"), ev.get("surface")))
            ctx.dirty.add(sid)  # re-read its last message now
