"""Agent adapter "claude": Claude Code sessions.

Discover: every running session writes ~/.claude/sessions/<pid>.json (session id, name, cwd,
busy/idle, bridgeSessionId when Remote Control is on). The pid's tty joins it to a cmux surface.
Status: that file plus the hooks source (newest signal wins); the transcript JSONL gives the
last assistant message and the chat history.
"""
import glob
import hashlib
import json
import os
import re
import time

from . import Agent, register_agent
from ._cmux import above_box, key, parse_box, read_screen, send_raw, spinner_on, status_line, type_text
from ._common import HOME, file_meta, iso, log, log_send, message_facts, norm, run, tail_jsonl

SESSIONS_DIR = os.path.join(HOME, ".claude/sessions")
PROJECTS_DIR = os.path.join(HOME, ".claude/projects")
SYSTEM_PROMPT = re.compile(r"^\s*Master Console fallback check")
SKIP_USER = re.compile(r"^\s*(<(command-|local-command|system-reminder|bash-|task-notification)|\[Request interrupted)")


def transcript_messages(path, max_bytes=8 << 20):
    """User/assistant text turns (tool rows skipped; consecutive assistant texts merged).
    Turns started automatically (a scheduled/cron prompt, e.g. "Master Console fallback check")
    and every reply until the next real user message carry "system": True."""
    msgs, sys_turn = [], False
    for d in tail_jsonl(path, max_bytes):
        if d.get("isSidechain"):
            continue
        t, ts, uid = d.get("type"), d.get("timestamp") or "", d.get("uuid") or ""
        text, role, system, origin = None, None, False, "user"
        if t == "user":
            c = (d.get("message") or {}).get("content")
            if isinstance(c, str):
                text = c
            elif isinstance(c, list) and not any(x.get("type") == "tool_result" for x in c if isinstance(x, dict)):
                text = "\n".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
            if text is None:
                continue  # a tool result: same turn
            automatic = d.get("turnOrigin") == "scheduled" or bool(SYSTEM_PROMPT.match(text))
            origin = "session" if "<cross-session-message" in text[:300] else "user"
            if d.get("isCompactSummary"):  # a system row; the turn it continues is unchanged
                msgs.append({"id": uid, "role": "user", "text": text.strip(), "ts": ts, "system": True,
                             "origin": "system"})
                continue
            if d.get("isMeta") and not automatic and origin == "user":
                continue
            role, sys_turn = "user", automatic
            system = automatic
        elif t == "attachment":  # a message typed while the session was busy
            a = d.get("attachment") or {}
            if a.get("type") == "queued_command" and isinstance(a.get("prompt"), str):
                text, role, sys_turn = a["prompt"], "user", False
        elif t == "assistant":
            c = (d.get("message") or {}).get("content")
            if isinstance(c, list):
                text = "\n".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
            role, system = "assistant", sys_turn
        if not text or not text.strip() or (role == "user" and not system and SKIP_USER.match(text)):
            continue
        if role == "assistant" and msgs and msgs[-1]["role"] == "assistant" and msgs[-1]["system"] == system:
            msgs[-1]["text"] += "\n\n" + text.strip()
            msgs[-1]["ts"] = ts or msgs[-1]["ts"]
            continue
        msgs.append({"id": uid, "role": role, "text": text.strip(), "ts": ts, "system": system,
                     "origin": "system" if system else (origin if role == "user" else "agent")})
    return msgs


def pending_ask(path, max_bytes=2 << 20):
    """The last AskUserQuestion tool_use in the transcript that has no tool_result yet, or None."""
    open_calls = {}
    for d in tail_jsonl(path, max_bytes):
        c = (d.get("message") or {}).get("content")
        if not isinstance(c, list) or d.get("isSidechain"):
            continue
        for x in c:
            if not isinstance(x, dict):
                continue
            if x.get("type") == "tool_use" and x.get("name") == "AskUserQuestion":
                open_calls[x.get("id")] = (x.get("input") or {}, d.get("timestamp") or "", d.get("uuid") or "")
            elif x.get("type") == "tool_result":
                open_calls.pop(x.get("tool_use_id"), None)
    if not open_calls:
        return None
    tid, (inp, ts, uid) = list(open_calls.items())[-1]
    return dict(ask_prompt(tid, inp, ts), message_id=uid)


def ask_prompt(tool_use_id, inp, opened_at):
    qs = []
    for q in inp.get("questions") or []:
        qs.append({"text": q.get("question", ""), "header": q.get("header", ""),
                   "multi_select": bool(q.get("multiSelect")),
                   "options": [{"label": o.get("label", ""), "description": o.get("description", "")}
                               for o in q.get("options") or []]})
    return {"id": tool_use_id, "kind": "ask_user_question", "questions": qs, "allows_other": True,
            "opened_at": opened_at, "asked_at": opened_at, "message_id": None, "source": "transcript"}


OPTION_RE = re.compile(r"^\s*(❯)?\s*(\d+)\.\s+(\[(.)\]\s+)?(.*?)\s*$")


def dialog_options(screen):
    """The numbered menu around the cursor (❯ N. …) at the bottom of the screen:
    [{"n", "label", "checked", "cursor", "line"}]. Only the block holding the cursor, so numbered
    lists in the conversation above never count. An unnumbered "Submit" row (multi-select) is
    returned with n = None."""
    lines = (screen or "").rstrip().split("\n")
    cur = next((i for i in range(len(lines) - 1, max(-1, len(lines) - 40), -1)
                if re.match(r"^\s*❯\s*(\d+\.|Submit\s*$)", lines[i])), None)
    if cur is None:
        return []
    def block_line(l):
        return l.strip() and not l.lstrip().startswith(("─", "╌"))
    a = cur
    while a > 0 and block_line(lines[a - 1]):
        a -= 1
    b = cur
    while b + 1 < len(lines) and block_line(lines[b + 1]):
        b += 1
    out = []
    for i in range(a, b + 1):
        m = OPTION_RE.match(lines[i])
        if m:
            out.append({"n": int(m.group(2)), "label": m.group(5), "checked": (m.group(4) or " ") != " ",
                        "cursor": bool(m.group(1)), "line": i})
        elif re.match(r"^\s*(❯)?\s*Submit\s*$", lines[i]):
            out.append({"n": None, "label": "Submit", "checked": False, "cursor": "❯" in lines[i], "line": i})
    return out


def dialog_question(screen, opts):
    lines = (screen or "").rstrip().split("\n")
    first = opts[0]["line"]
    return next((lines[i].strip() for i in range(first - 1, max(-1, first - 8), -1)
                 if lines[i].strip() and not lines[i].lstrip().startswith(("─", "╌", "←"))), "")


def screen_prompt(screen, waiting_for):
    """A permission or confirm menu parsed from the screen (when there's no structured source)."""
    opts = [o for o in dialog_options(screen) if o["n"] is not None]
    if not opts:
        return None
    q = dialog_question(screen, opts)
    labels = [o["label"] for o in opts]
    kind = "permission" if "permission" in (waiting_for or "") else "confirm"
    pid = "screen:" + hashlib.sha1((q + "|" + "|".join(labels)).encode()).hexdigest()[:12]
    return {"id": pid, "kind": kind, "allows_other": False, "opened_at": None, "asked_at": None,
            "message_id": None, "source": "screen",
            "questions": [{"text": q, "header": "", "multi_select": False,
                           "options": [{"label": l, "description": ""} for l in labels]}]}


def find_transcript(sid, cwd=None):
    if cwd:
        p = os.path.join(PROJECTS_DIR, re.sub(r"[^A-Za-z0-9]", "-", cwd), sid + ".jsonl")
        if os.path.exists(p):
            return p
    hits = glob.glob(os.path.join(PROJECTS_DIR, "*", sid + ".jsonl"))
    return hits[0] if hits else None


def in_insert_mode(screen):
    return "-- INSERT --" in status_line(screen)


@register_agent
class Claude(Agent):
    name = "claude"
    label = "Claude"
    process_names = ("claude",)
    capabilities = {"submit": True, "interrupt": True, "remote_control": True,
                    "attachments": True, "usage_limit_retry": False, "prompts": True}
    launch = ('claude --dangerously-skip-permissions --remote-control "<project> · <slug>" '
              '-n "<project> · <slug>" [-w <slug>] "$(cat <prompt-file>)"')
    naming = "session name <project> · <slug> (-n); tab title <mark> <topic>"
    status_doc = ("~/.claude/sessions/<pid>.json (busy/idle) and the hooks source, newest wins; "
                  "transcript JSONL for the last message")
    submit_doc = ("make sure the box is in -- INSERT -- (else send A, confirm), type, enter, verify box empty + "
                  "spinner; only if the text is still there and the session is idle: escape, ~1.5 s, enter")
    interrupt_doc = "escape while working (twice if the box is in -- INSERT --: the first only leaves INSERT)"
    usage_limit_doc = "none"

    def __init__(self):
        self.msg_cache = {}  # transcript path -> (meta, last assistant message, pending AskUserQuestion)
        self.screens = {}  # surface -> (time, screen), for prompt detection
        self.sent_at = {}  # session id -> time of the last delivered send
        self.sent_text = {}  # session id -> text of the last delivered send
        self.prompt_seen = {}  # screen prompt id -> asked_at

    # discover / status ----------------------------------------------------
    def discover(self, ctx):
        files = []
        for f in glob.glob(os.path.join(SESSIONS_DIR, "*.json")):
            try:
                with open(f) as fh:
                    d = json.load(fh)
                pid = int(d.get("pid") or os.path.basename(f).split(".")[0])
                os.kill(pid, 0)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if d.get("sessionId") and d.get("kind", "interactive") == "interactive":
                d["pid"] = pid
                files.append(d)
        ttys = {}
        if files:
            rc, out, _ = run(["ps", "-o", "pid=,tty=", "-p", ",".join(str(d["pid"]) for d in files)])
            for line in out.split("\n"):
                parts = line.split()
                if len(parts) == 2:
                    ttys[int(parts[0])] = parts[1]
        rows = []
        for d in files:
            sid = d["sessionId"]
            tr = ctx.by_tty.get(ttys.get(d["pid"], "?")) or {}
            if tr:
                ctx.claimed.add(tr["surface"])
            h = ctx.hooks.get(sid, {})
            rows.append({
                "id": sid, "agent": self.name, "name": d.get("name") or "",
                "surface": tr.get("surface"), "workspace": tr.get("workspace"), "tab_title": tr.get("title", ""),
                "cwd": d.get("cwd") or h.get("cwd"), "pid": d["pid"],
                "remote_control": bool(d.get("bridgeSessionId")),
                "_file_busy": d.get("status") == "busy", "_file_at": (d.get("statusUpdatedAt") or 0) / 1000.0,
                "_waiting_for": d.get("waitingFor") if d.get("status") == "waiting" else None,
            })
        return rows

    def refresh(self, ctx, row):
        sid = row["id"]
        h = ctx.hooks.get(sid, {})
        hs, hat = h.get("state"), h.get("at") or 0
        if hs and hat > row["_file_at"]:
            row["working"], row["needs_permission"] = hs == "working", hs == "needs_permission"
        else:
            row["working"], row["needs_permission"] = row["_file_busy"], False
        # Right after a send the session file can lag (or a fast turn can finish between two
        # polls), so show working until the session reports a status newer than the send.
        sent = self.sent_at.get(sid, 0)
        if sent > max(row["_file_at"], hat) and time.time() - sent < 30:
            row["working"] = True
        tp = h.get("transcript_path") or ctx.paths.get(sid) or find_transcript(sid, row["cwd"])
        ctx.paths[sid] = tp
        row["log_path"] = tp
        meta = file_meta(tp)
        c = self.msg_cache.get(tp)
        if meta and (not c or c[0] != meta or sid in ctx.dirty):
            last, ask = message_facts(transcript_messages(tp, max_bytes=1 << 20))
            c = (meta, last, pending_ask(tp), ask)
            self.msg_cache[tp] = c
        last = c[1] if c else None
        row["last_message"] = last["text"] if last else ""
        row["last_message_at"] = last["ts"] if last else None
        row["last_message_id"] = last["id"] if last else None
        row["ask"] = c[3] if c else None
        # Real activity time: the transcript's last write, the session file, the last hook event.
        row["activity_at"] = max(meta[0] if meta else 0, row["_file_at"], hat) or None
        row["prompt"] = self.find_prompt(ctx, row, c[2] if c else None)
        if row["prompt"]:
            row["working"] = False

    def find_prompt(self, ctx, row, transcript_ask):
        """An open dialog that replaces the input box: AskUserQuestion (hooks, else the transcript's
        unanswered tool call), else a permission/confirm menu read from the screen. Only while the
        session file says it's waiting on the user, so a stale tool call never shows."""
        waiting = row.get("_waiting_for")
        hp = (ctx.hooks.get(row["id"]) or {}).get("prompt")
        if hp:
            return hp
        if not waiting:
            return None
        if transcript_ask:
            return transcript_ask
        if not row.get("surface"):
            return None
        c = self.screens.get(row["surface"])
        if not c or time.time() - c[0] > 2:
            c = (time.time(), read_screen(row["surface"], row["workspace"], 40) or "")
            self.screens[row["surface"]] = c
        p = screen_prompt(c[1], waiting)
        if p:
            first = self.prompt_seen.setdefault(p["id"], iso(min(time.time(), row["_file_at"] or time.time())))
            p["opened_at"] = p["asked_at"] = first  # the session file's waiting-since, else first seen
        return p

    def messages(self, row):
        return transcript_messages(row["log_path"]) if row.get("log_path") else []

    def note_delivered(self, row, text):
        self.sent_at[row["id"]] = time.time()
        self.sent_text[row["id"]] = text

    # submit ---------------------------------------------------------------
    def submit(self, row, text):
        """Reliable submit into a Claude Code box (vim mode).

        Typing into a box in vim NORMAL mode runs the text as vim commands: "From …" loses
        "Fro" (F r = find 'r', o = open line + INSERT), "Read …" loses "Rea", "Supervisor"
        loses "S", and the box then shows -- INSERT --, which hides the cause. A previous
        escape-then-enter submit leaves the box in NORMAL. So: confirm INSERT before typing.
        Escape is only a fallback, and only when the session is idle: in a busy session it
        interrupts the turn."""
        s, w = row["surface"], row["workspace"]
        scr = read_screen(s, w)
        if scr is None:
            return False, "cannot read screen"
        before = parse_box(scr)
        if before is None:
            return False, "no Claude input box on screen"
        if not in_insert_mode(scr):
            # A, not i: NORMAL mode's cursor sits on the last character, and i would insert before it.
            send_raw(s, w, "A")
            time.sleep(0.3)
            scr = read_screen(s, w) or ""
            if not in_insert_mode(scr):
                if norm(parse_box(scr)) == norm(before) + "A":  # no vim mode at all: the A was typed
                    key(s, w, "backspace")
                else:
                    return False, "could not put the input box in INSERT mode"
        type_text(s, w, text)
        time.sleep(0.4)
        scr = read_screen(s, w) or ""
        box = parse_box(scr) or ""
        if norm(box) != norm(text) and not (norm(before) == "" and re.fullmatch(r"(\[Pasted text[^\]]*\])+", norm(box))):
            # The box held something else (a draft we can't tell from a suggestion, or typed text).
            # Remove exactly what we typed: the cursor sits right after it.
            for _ in range(len(text)):
                key(s, w, "backspace")
            return False, "input box not empty"
        head = norm(text)[:30]
        for attempt in range(4):
            # Busy = the live state (session file/hooks) or a spinner. While a reply streams there
            # is often no spinner on screen, so the screen alone can't tell.
            if attempt and not (row.get("working") or spinner_on(scr)):
                # Fallback for a stuck enter, idle sessions only.
                for _ in range(3):
                    key(s, w, "escape")
                    time.sleep(1.5)
                    if not in_insert_mode(read_screen(s, w) or ""):
                        break
            key(s, w, "enter")
            deadline = time.time() + 5
            while time.time() < deadline:
                time.sleep(0.5)
                scr = read_screen(s, w) or ""
                box = parse_box(scr)
                if box is not None and norm(box) != norm(text):
                    if spinner_on(scr) or head in norm(above_box(scr)[-6000:]):
                        return True, "submitted" + (f" (after {attempt + 1} tries)" if attempt else "")
            log("claude submit: retry", s, attempt)
        return False, "text still in input box after 4 tries"

    # prompts --------------------------------------------------------------
    def answer_prompt(self, row, prompt, answers):
        """Answer an open dialog with keystrokes. answers: one {"choices": [label…], "other_text"}
        per question. Re-reads the menu before every move, moves the cursor by label (never by
        counting blind), and only reports success once the dialog has closed."""
        sf, w = row["surface"], row["workspace"]

        def scr():
            return read_screen(sf, w, 45) or ""

        def move_to(match):
            for _ in range(4):
                opts = dialog_options(scr())
                cur = next((i for i, o in enumerate(opts) if o["cursor"]), None)
                tgt = next((i for i, o in enumerate(opts) if match(o)), None)
                if cur is None or tgt is None:
                    return None
                if cur == tgt:
                    return opts[tgt]
                for _ in range(abs(tgt - cur)):
                    key(sf, w, "down" if tgt > cur else "up")
                time.sleep(0.3)
            return None

        def is_label(label):
            return lambda o: o["label"].rstrip(".") == label.rstrip(".")

        def is_other(o):
            return o["label"].startswith("Type something")

        qs = prompt["questions"]
        for qi, q in enumerate(qs):
            a = answers[qi]
            shown = dialog_question(scr(), dialog_options(scr()) or [{"line": 0}])
            if q["text"] and norm(q["text"])[:40] not in norm(shown):
                return False, f"question {qi + 1} is not on screen (the dialog changed?)"
            choices, other = a.get("choices") or [], (a.get("other_text") or "").strip()
            if not q["multi_select"]:
                if other:
                    if not move_to(is_other):
                        return False, "no 'Type something' row"
                    type_text(sf, w, other)
                    time.sleep(0.3)
                elif not move_to(is_label(choices[0])):
                    return False, f"option {choices[0]!r} not found"
                key(sf, w, "enter")
            else:
                for label in choices:
                    o = move_to(is_label(label))
                    if not o:
                        return False, f"option {label!r} not found"
                    if not o["checked"]:
                        key(sf, w, "enter")  # enter toggles a multi-select row (space doesn't)
                        time.sleep(0.3)
                if other:
                    if not move_to(is_other):
                        return False, "no 'Type something' row"
                    type_text(sf, w, other)
                    time.sleep(0.3)
                if not move_to(lambda o: o["n"] is None and o["label"] == "Submit"):
                    return False, "no Submit row"
                key(sf, w, "enter")
            time.sleep(0.7)
        if prompt["kind"] == "ask_user_question" and len(qs) > 1:
            if not move_to(lambda o: o["label"].startswith("Submit answers")):
                return False, "review screen not found"
            key(sf, w, "enter")
        for _ in range(16):  # verify the dialog closed
            time.sleep(0.5)
            if prompt["source"] == "transcript" or prompt["kind"] == "ask_user_question":
                p = pending_ask(row["log_path"]) if row.get("log_path") else None
                if not p or p["id"] != prompt["id"]:
                    return True, "answered"
            else:
                now = screen_prompt(scr(), "permission")
                if not now or now["id"] != prompt["id"]:
                    return True, "answered"
        return False, "dialog still open after answering"

    def interrupt(self, row):
        """Escape interrupts a busy session, but in vim INSERT mode the first escape only
        switches to NORMAL, so that takes two."""
        sf, w = row["surface"], row["workspace"]
        scr = read_screen(sf, w) or ""
        if not (row.get("working") or spinner_on(scr)):
            return False, "not working; nothing to interrupt"
        if in_insert_mode(scr):
            key(sf, w, "escape")
            time.sleep(0.4)
        key(sf, w, "escape")
        seen = False
        for _ in range(6):
            time.sleep(0.5)
            scr = read_screen(sf, w, 40) or ""
            if "Interrupted" in scr:
                seen = True
                break
        # Interrupted before the turn really started, Claude shows no "Interrupted" line and just
        # puts the prompt back into the box. Clear it either way.
        self._clear_restored_prompt(row, scr)
        return True, "interrupted" if seen else "escape sent; the prompt was taken back"

    def _clear_restored_prompt(self, row, scr):
        """An interrupt puts the interrupted prompt back into the box, which would block every
        later send ("input box not empty"). Clear it, but only if it is exactly that last
        prompt, never anything the user typed."""
        users = [m["text"] for m in self.messages(row) if m["role"] == "user" and not m.get("system")]
        candidates = {norm(t) for t in users[-1:] + [self.sent_text.get(row["id"], "")] if t}
        if not candidates:
            return
        sf, w = row["surface"], row["workspace"]
        for _ in range(6):  # the prompt is restored a moment after the interrupt
            box = parse_box(scr)
            if box and norm(box) in candidates:
                break
            time.sleep(0.4)
            scr = read_screen(sf, w, 40) or ""
        else:
            return
        if not in_insert_mode(scr):
            send_raw(sf, w, "A")
            time.sleep(0.3)
        for _ in range(len(box) + 2):
            key(sf, w, "backspace")

    # upkeep: Remote Control recovery (port of rc-recover.sh) ---------------
    def upkeep(self, state, dry=False):
        """Resend /remote-control <name> where the latest Remote Control notice on screen is the
        disconnect and the input box is empty."""
        done = []
        for r in state.rows_for(self.name):
            if not r.get("surface"):
                continue
            out = read_screen(r["surface"], r["workspace"], 150)
            if not out:
                continue
            ev = [l for l in out.split("\n")
                  if re.search(r"Remote Control (disconnected|active|connected)|/remote-control is active", l)]
            if not ev or "Remote Control disconnected" not in ev[-1]:
                continue
            box = parse_box(out)
            if box is None or norm(box):
                log("rc-recover: SKIP", r["surface"], r["name"], "input box not empty")
                continue
            cmd = f"/remote-control {r['name']}".rstrip()
            log("rc-recover: RECOVER" + (" (dry run)" if dry else ""), r["surface"], r["name"])
            done.append({"surface": r["surface"], "name": r["name"]})
            if dry:
                continue
            ok, detail = state.deliver(r, cmd)
            log_send({"time": iso(), "send_id": "rc-" + str(int(time.time() * 1000)), "client": "threadsd rc-recover",
                      "target": r["id"], "surface": r["surface"], "text": cmd, "attachments": [],
                      "status": "delivered" if ok else "failed", "result": "delivered" if ok else "failed",
                      "detail": detail})
        return done
