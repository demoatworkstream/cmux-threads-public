"""Agent adapter "codex": Codex CLI threads. No Claude hooks fire in Codex, so it reads Codex's
own files: the rollout JSONL the codex process has open (found with lsof), the codex-sqlite
source for cwd, cmux's codex= workspace status, and the screen as a tiebreaker."""
import re
import time

from . import Agent, register_agent
from ._cmux import key, read_screen, type_text
from ._common import file_meta, log, message_facts, norm, run, tail_jsonl

UUID_RE = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$")
PLACEHOLDERS = re.compile(r"^(Ask Codex to do anything|Find and fix a bug in @filename|Explain this codebase|"
                          r"Summarize recent commits|Implement \{feature\}|Write tests for @filename|"
                          r"Improve documentation in @filename|Run /review on my current changes|"
                          r"Use /skills to list available skills)$")
SCREEN_TTL = 3.0


def parse_box(screen):
    """Text in Codex's composer (the last › line near the bottom), or None if none."""
    lines = (screen or "").rstrip().split("\n")
    for i in range(len(lines) - 1, max(-1, len(lines) - 15), -1):
        if lines[i].startswith("›"):
            out = [lines[i][1:]]
            for l in lines[i + 1:]:
                if not l.startswith("  ") or not l.strip():
                    break
                out.append(l)
            return "\n".join(out)
    return None


def box_empty(box):
    return box is not None and (not box.strip() or bool(PLACEHOLDERS.match(box.strip())))


def working_on_screen(scr):
    return "Working (" in scr and "esc to interrupt" in scr


def rollout_messages(path):
    msgs = []
    for d in tail_jsonl(path, 8 << 20):
        p = d.get("payload") or {}
        if d.get("type") != "response_item" or p.get("type") != "message" or p.get("role") not in ("user", "assistant"):
            continue
        text = "\n".join(c.get("text", "") for c in p.get("content") or [] if c.get("type") in ("input_text", "output_text"))
        if not text.strip() or (p["role"] == "user" and
                                re.match(r"\s*(#\s*AGENTS\.md|<environment_context|<user_instructions|<permissions)", text)):
            continue
        mid = p.get("id") or "ts:" + (d.get("timestamp") or "")
        msgs.append({"id": mid, "role": p["role"], "text": text.strip(), "ts": d.get("timestamp") or "", "system": False})
    return msgs


@register_agent
class Codex(Agent):
    name = "codex"
    label = "Codex"
    process_names = ("codex",)
    capabilities = {"submit": True, "interrupt": True, "remote_control": False,
                    "attachments": True, "usage_limit_retry": True, "prompts": False}
    launch = ('codex --no-alt-screen --dangerously-bypass-approvals-and-sandbox -C <dir> [--add-dir <dir>] '
              '[--worktree] [-i <image>] "$(cat <prompt-file>)"')
    naming = "no session name and no Remote Control; the tab title <mark> <topic> is its only name"
    status_doc = ("rollout JSONL (lsof on the codex pid): last task_started / task_complete; codex-sqlite for cwd; "
                  "cmux codex= workspace status; screen as tiebreaker")
    submit_doc = "type, enter, verify Working; never escape (it can interrupt a task)"
    interrupt_doc = "escape while Working (Codex shows 'esc to interrupt'); ctrl+c twice quits Codex, never used"
    usage_limit_doc = "state failed, last_message 'usage limit, retry at <time>'; the root's background watcher resumes it"

    def __init__(self):
        self.rollout = {}  # pid -> rollout path
        self.parsed = {}  # rollout path -> (meta, result)
        self.screens = {}  # surface -> (time, screen)

    def rollout_for(self, pid):
        p = self.rollout.get(pid)
        if p and file_meta(p):
            return p
        rc, out, _ = run(["lsof", "-p", str(pid), "-Fn"], timeout=5)
        hits = [l[1:] for l in out.split("\n") if l.startswith("n") and "/rollout-" in l and l.endswith(".jsonl")]
        if hits:
            self.rollout[pid] = hits[-1]
            return hits[-1]
        return None

    def discover(self, ctx):
        rows = []
        for s in ctx.surfaces:
            if s["surface"] in ctx.claimed:
                continue
            pid = next((p for p, exe in ctx.procs.get(s["tty"] or "", []) if exe in self.process_names), None)
            if not pid:
                continue
            ctx.claimed.add(s["surface"])
            path = self.rollout_for(pid)
            m = UUID_RE.search(path or "")
            tid = m.group(1) if m else None
            t = ctx.codex_threads.get(tid, {}) if tid else {}
            rows.append({"id": tid or f"codex:{s['surface']}", "agent": self.name, "name": "",
                         "surface": s["surface"], "workspace": s["workspace"], "tab_title": s["title"],
                         "cwd": t.get("cwd"), "pid": pid, "remote_control": False, "log_path": path})
        for r in rows:
            r["_alone_in_ws"] = sum(1 for x in rows if x["workspace"] == r["workspace"]) == 1
        return rows

    def parse(self, path):
        meta = file_meta(path)
        if not meta:
            return {"working": False, "last_message": "", "usage_limit": None}
        c = self.parsed.get(path)
        if c and c[0] == meta:
            return c[1]
        working, last, limit = False, "", None
        for d in tail_jsonl(path, 2 << 20):
            p = d.get("payload") or {}
            if d.get("type") != "event_msg":
                continue
            if p.get("type") == "task_started":
                working, limit = True, None
            elif p.get("type") == "task_complete":
                working = False
                err = p.get("error") or {}
                if err.get("codex_error_info") == "usage_limit_exceeded":
                    m = re.search(r"try again at ([^.]+)", err.get("message", ""))
                    limit = "usage limit, retry at " + (m.group(1).strip() if m else "later")
                elif p.get("last_agent_message"):
                    last, limit = p["last_agent_message"], None
        lastm, ask = message_facts(rollout_messages(path))
        r = {"working": working, "last_message": last, "usage_limit": limit, "ask": ask,
             "last_message_at": lastm["ts"] if lastm else None, "last_message_id": lastm["id"] if lastm else None,
             "activity_at": meta[0]}
        self.parsed[path] = (meta, r)
        return r

    def refresh(self, ctx, row):
        r = self.parse(row["log_path"]) if row.get("log_path") else {"working": False, "last_message": "", "usage_limit": None}
        row["working"], row["usage_limit"] = r["working"], r["usage_limit"]
        for k in ("ask", "last_message_at", "last_message_id", "activity_at"):
            row[k] = r.get(k)
        row["last_message"] = r["usage_limit"] or r["last_message"]
        row["needs_permission"] = False
        # cmux's codex= entry covers a whole workspace, so trust it only for a lone Codex there.
        ws_codex = (ctx.ws_status.get(row["workspace"]) or {}).get("codex", "")
        if row["_alone_in_ws"] and re.match(r"(Running|Working)", ws_codex):
            row["working"] = True
        c = self.screens.get(row["surface"])
        if c and time.time() - c[0] < SCREEN_TTL:
            scr = c[1]
        else:
            scr = read_screen(row["surface"], row["workspace"], 25) or ""
            self.screens[row["surface"]] = (time.time(), scr)
        if working_on_screen(scr):
            row["working"] = True
        elif row["working"] and "Working (" not in scr and parse_box(scr) is not None:
            row["working"] = False  # files say a task is open, but the idle prompt shows: interrupted

    def messages(self, row):
        return rollout_messages(row["log_path"]) if row.get("log_path") else []

    def submit(self, row, text):
        """Codex submit: type, enter, verify Working. Never escape: it can interrupt a task."""
        s, w = row["surface"], row["workspace"]
        scr = read_screen(s, w)
        if scr is None:
            return False, "cannot read screen"
        before = parse_box(scr)
        if before is None:
            return False, "no Codex composer on screen"
        if not box_empty(before):
            return False, "input box not empty"
        type_text(s, w, text)
        time.sleep(0.4)
        box = parse_box(read_screen(s, w) or "") or ""
        if norm(text) not in norm(box) and "[Pasted Content" not in box:
            for _ in range(len(text)):
                key(s, w, "backspace")
            return False, "input box not empty"
        for attempt in range(3):
            key(s, w, "enter")
            deadline = time.time() + 8
            while time.time() < deadline:
                time.sleep(0.5)
                scr = read_screen(s, w) or ""
                if "Working (" in scr or (box_empty(parse_box(scr)) and norm(text)[:30] in norm(scr)):
                    return True, "submitted" + (f" (after {attempt + 1} tries)" if attempt else "")
            log("codex submit: retry", s, attempt)
        return False, "Codex did not start Working after 3 enters"

    def interrupt(self, row):
        scr = read_screen(row["surface"], row["workspace"]) or ""
        if not working_on_screen(scr):
            return False, "not working; nothing to interrupt"
        key(row["surface"], row["workspace"], "escape")
        return True, "escape sent"
