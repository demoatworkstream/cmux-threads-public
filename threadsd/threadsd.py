#!/usr/bin/env python3
"""threadsd: live state of the agent sessions in cmux (Claude Code, Codex, ...), served over Tailscale.

  threadsd.py serve                         run the daemon
  threadsd.py state                         print what /state returns (no server needed)
  threadsd.py submit SURFACE WORKSPACE TEXT  deliver text with the agent's submit (testing)
  threadsd.py rc-recover [--dry-run]        one Remote Control recovery pass
  threadsd.py health                        exit 0 if the running daemon answers /healthz

The core here owns the state model, the versioned schema and the HTTP API. Everything
agent- or source-specific is a plugin (plugins/): agent adapters, status sources,
transports and UI adapters, found through registries. The core never branches on a
plugin's name. API: API.md next to this file. Python stdlib only.
"""
import base64, hashlib, hmac, json, os, queue, re, secrets, socket, sqlite3, sys, threading, time, uuid, zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import plugins  # noqa: E402
from plugins import AGENTS, BRAINS, SOURCES, TELEMETRY, TRANSPORTS, UIS, call_store  # noqa: E402
from plugins._otel import redact  # noqa: E402
from plugins.transport_cmux_send import LOCK as TRANSPORT_LOCK  # noqa: E402
from plugins import source_artifacts  # noqa: E402  (Artifacts tab: /artifacts, backed by the Artifact Shelf index)
from plugins._common import (CONF, DATA, DB_PATH, MARKS, SCHEMA_VERSION, SPOOL, STATUS_RE, TOKEN_PATH,  # noqa: E402
                             UPLOADS, file_meta, iso, last_status, log, log_send, parse_questions, run, ts_epoch)

plugins.load_all()

VERSION = "1.1.0"
PORT = int(os.environ.get("THREADSD_PORT", "7878"))
UPKEEP_INTERVAL = int(os.environ.get("THREADSD_RC_INTERVAL", "120"))  # seconds; 0 disables
LONG_TEXT = 3000  # longer messages are written to a file and sent as one "read <file>" line
MARK_STATE = {"▶": "working", "?": "needs_you", "✓": "finished", "○": "idle", "✗": "failed"}
STATE_MARK = {v: k for k, v in MARK_STATE.items()}
NO_CAPS = {"submit": False, "interrupt": False, "remote_control": False, "attachments": False,
           "usage_limit_retry": False, "prompts": False}
QUEUE_MAX_AGE = 6 * 3600  # a queued send that can't be delivered within this fails
PUBLIC = ("id", "name", "title", "role", "project", "machine", "agent", "agent_label", "capabilities",
          "remote_control", "transport", "cwd", "surface", "workspace", "mark", "state", "last_message",
          "last_message_at", "last_message_id", "last_status", "questions", "attention", "prompt", "root_id",
          "updated_at")


# ------------------------------------------------------- message parsing ----



def parse_status(text):
    """The last STATUS: line wins: a merged turn often reports working, then needs you."""
    m = last_status(text)
    return m.group(1).lower().replace(" ", "_") if m else None


def summary_line(text):
    """The first line that says something: a bare "STATUS: …" line or bold heading is skipped."""
    for line in text.strip().split("\n"):
        l = line.strip().strip("*").strip()
        if l and not re.fullmatch(r"STATUS:\s*\**\s*(working|needs you|finished|failed)\.?", l, re.I):
            return l[:300]
    return text.strip()[:300]


def thread_ask_key(r):
    ask = r.get("ask") or {}
    return f'{r["id"]}:msg:{ask.get("id") or r.get("last_message_id") or ""}'


# ----------------------------------------------------------------- state ----

class Hub:
    """Fan-out of SSE events to every connected /events client."""

    def __init__(self):
        self.lock = threading.Lock()
        self.subs = set()

    def subscribe(self):
        q = queue.Queue(maxsize=1000)
        with self.lock:
            self.subs.add(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            self.subs.discard(q)

    def publish(self, event, data):
        data = dict(data, schema_version=SCHEMA_VERSION)
        with self.lock:
            for q in list(self.subs):
                try:
                    q.put_nowait((event, data))
                except queue.Full:
                    self.subs.discard(q)  # a stuck client: drop it, it will reconnect


class Store:
    def __init__(self):
        self.db = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=5)
        self.db.executescript("""
          pragma journal_mode=wal;
          create table if not exists events(id integer primary key, ts real, event text, session_id text, payload text);
          create table if not exists sessions(id text primary key, data text, updated_at real);
          create table if not exists hookstate(session_id text primary key, state text, at real,
                                               transcript_path text, cwd text, surface_uuid text);
          create table if not exists sends(send_id text primary key, ts real, data text);
          create table if not exists qstate(key text primary key, session_id text, status text, at real,
                                            client text, question text);
          create table if not exists queue(send_id text primary key, ts real, target text, text text,
                                           rec text, attempts integer default 0, last_detail text);
        """)
        self.lock = threading.Lock()

    def x(self, sql, args=()):
        with self.lock:
            cur = self.db.execute(sql, args)
            self.db.commit()
            return cur.fetchall()


class Ctx:
    """Facts the status sources gather each tick, shared with the agent adapters."""

    def __init__(self, store):
        self.store = store
        self.surfaces, self.by_tty, self.workspaces, self.procs, self.ws_status = [], {}, {}, {}, {}
        self.hooks = {r[0]: {"state": r[1], "at": r[2], "transcript_path": r[3], "cwd": r[4]}
                      for r in store.x("select session_id, state, at, transcript_path, cwd from hookstate")}
        self.codex_threads, self.remote_rows, self.paths = {}, [], {}
        self.claimed, self.dirty = set(), set()
        self.cmux_error = None


class State:
    def __init__(self):
        self.store, self.hub = Store(), Hub()
        self.ctx = Ctx(self.store)
        self.lock = threading.RLock()
        self.sessions = {}  # id -> public Session
        self.rows = {}  # id -> internal row (adapter fields + public fields)
        self.machine = machine_name()
        self.master_id = None
        self.master_seen = {}  # master id -> (log meta, number of messages already emitted)
        self.source_at = {}
        self.wake = threading.Event()
        self.tick_lock = threading.Lock()
        rows = self.store.x("select key, status, at from qstate")
        self.qstate = {k: st for k, st, _ in rows}  # key -> status
        self.qstate_at = {k: at for k, _, at in rows}  # key -> when it was set

    def tick(self, force=False):
        with self.tick_lock:
            self._tick(force)

    def reapply(self):
        """Recompute the public sessions from the last rows (e.g. after a skip), without polling."""
        with self.tick_lock:
            self.finalize([r for r in self.rows.values() if not r.get("remote")])

    def _tick(self, force=False):
        ctx = self.ctx
        for name, src in SOURCES.items():
            if force or time.time() - self.source_at.get(name, 0) >= src.interval:
                self.source_at[name] = time.time()
                try:
                    src.poll(ctx)
                except Exception as e:
                    log(f"source {name} error", repr(e))
        ctx.claimed = set()
        rows = []
        for name, agent in AGENTS.items():
            try:
                found = agent.discover(ctx)
                for r in found:
                    agent.refresh(ctx, r)
                rows += found
            except Exception as e:
                log(f"agent {name} error", repr(e))
        ctx.dirty = set()
        # Any other tab with a status mark: agent unknown, state from the mark, read-only.
        for s in ctx.surfaces:
            if s["surface"] not in ctx.claimed and s["title"][:1] in MARKS and s["title"][1:2] == " ":
                rows.append({"id": f"cmux:{s['workspace']}:{s['surface']}", "agent": "unknown", "name": "",
                             "surface": s["surface"], "workspace": s["workspace"], "tab_title": s["title"],
                             "cwd": None, "remote_control": False})
        self.finalize(rows)

    def finalize(self, rows):
        roots = {}
        for r in rows:
            r["title"] = r.get("tab_title", "")
            r["tab_mark"] = r["title"][:1] if r["title"][:1] in MARKS and r["title"][1:2] == " " else None
            if r["tab_mark"]:
                r["title"] = r["title"][2:]
            r["name"] = r["name"] or r["title"]
            r["title"] = r["title"] or r["name"]
            n, t = r["name"], r.get("tab_title", "")
            r["role"] = "master" if n.startswith("◎") or t.startswith("◎") else \
                        "root" if n.startswith("⌂ Root") or t.startswith("⌂ Root") else "thread"
            if r["role"] == "root" and r.get("workspace"):
                proj = re.sub(r"^⌂ Root · ", "", n if n.startswith("⌂") else r["title"])
                roots[r["workspace"]] = (r["id"], re.sub(r"\s*\(\d+/\d+\)$", "", proj))
        built = {}
        for r in rows:
            root = roots.get(r.get("workspace"))
            if r["role"] == "master":
                r["project"] = "master"
            elif r["role"] == "root":
                r["project"] = roots.get(r.get("workspace"), (None, None))[1] or \
                    re.sub(r"\s*\(\d+/\d+\)$", "", re.sub(r"^⌂ Root · ", "", r["name"]))
            elif root:
                r["project"] = root[1]
            elif " · " in r["name"]:
                r["project"] = r["name"].split(" · ")[0]
            else:
                r["project"] = os.path.basename(r.get("cwd") or "") or None
            r["root_id"] = root[0] if root and r["role"] == "thread" else None
            r["state"] = self.resolve_state(r)
            msg = r.get("last_message") or ""
            ask = r.get("ask")  # the latest STATUS-bearing message, if it said "needs you" (maybe stale)
            if ask:
                qs = [dict(q, asked_at=ask["ts"], message_id=ask["id"], source_message_id=ask["id"],
                           stale=ask["stale"] or r["state"] == "working")
                      for q in parse_questions(r["id"], ask["text"])]
            elif r["state"] == "needs_you" and msg:  # needs you from the tab mark only
                qs = [dict(q, asked_at=r.get("last_message_at"), message_id=r.get("last_message_id"),
                           source_message_id=r.get("last_message_id"), stale=False)
                      for q in parse_questions(r["id"], msg)]
            else:
                qs = []
            for q in qs:
                # A mark older than the message asking it belongs to an earlier ask of the same
                # question (same text, same position): the new ask is open again.
                asked = ts_epoch(q["asked_at"]) if q.get("asked_at") else None
                fresh = asked is None or self.qstate_at.get(q["id"], 0) >= asked - 1
                q["status"] = self.qstate.get(q["id"], "open") if fresh else "open"
            r["questions"] = qs
            # The thread as a whole: skipped until it asks something new. A needs-you thread
            # whose question didn't parse is keyed by the message it came from.
            thread_key = thread_ask_key(r)
            open_qs = [q for q in qs if q["status"] == "open"]
            if qs:  # open (even stale) questions keep the thread in Needs you until skipped or closed
                r["attention"] = "open" if open_qs else \
                    ("skipped" if any(q["status"] == "skipped" for q in qs) else "answered")
            elif r["state"] == "needs_you":
                r["attention"] = self.qstate.get(thread_key, "open")
            else:
                r["attention"] = None
            last_id = r.get("last_message_id")
            r["last_status"] = (summary_line(msg)
                                if msg and last_id and (not ask or ask["id"] != last_id) else None)
            r["last_message"] = msg[:400]
            r["mark"] = r["tab_mark"] or STATE_MARK.get(r["state"], "○")
            agent = AGENTS.get(r["agent"])
            r["agent_label"] = agent.label if agent else r["agent"]
            r["capabilities"] = dict(agent.capabilities) if agent else dict(NO_CAPS)
            r["transport"] = "cmux-send"
            r["machine"] = self.machine
            built[r["id"]] = r
        for r in self.ctx.remote_rows:  # complete rows from other machines (remote-machine source)
            r = dict(r, activity_at=r.get("updated_at"))
            r.setdefault("capabilities", dict(NO_CAPS, submit=True))
            r.setdefault("agent_label", (AGENTS.get(r.get("agent")) or plugins.Agent()).label or r.get("agent"))
            built.setdefault(r["id"], r)
        masters = sorted((r for r in built.values() if r.get("role") == "master" and not r.get("remote")),
                         key=lambda r: (r["name"] != "◎ Master", r.get("surface") is None))
        self.master_id = masters[0]["id"] if masters else None
        self.apply(built)
        if self.master_id:
            self.emit_master_messages(built[self.master_id])

    @staticmethod
    def resolve_state(r):
        """working (live) > needs a permission > usage limit > the last STATUS: line > the tab mark."""
        if r.get("state") and r.get("remote"):
            return r["state"]
        if r.get("prompt"):
            return "needs_you"  # a dialog is waiting for an answer
        if r.get("working"):
            return "working"
        if r.get("needs_permission"):
            return "needs_you"
        if r.get("usage_limit"):
            return "failed"
        st = parse_status(r.get("last_message"))
        if st in ("needs_you", "finished", "failed"):
            return st
        # no STATUS line, or a stale "working" from a turn that has ended: the mark decides
        return {"?": "needs_you", "✓": "finished", "✗": "failed"}.get(r.get("tab_mark"), "idle")

    def apply(self, built):
        now = time.time()
        with self.lock:
            self.rows = built
            for sid, r in built.items():
                s = {k: r.get(k) for k in PUBLIC}
                old = self.sessions.get(sid)
                # Real activity time (transcript/hook/rollout); poll time only when nothing better exists.
                act = r.get("activity_at")
                if isinstance(act, str):
                    act = ts_epoch(act)
                s["updated_at"] = iso(act) if act else (old or {}).get("updated_at") or iso(now)
                same = old and {k: v for k, v in old.items() if k != "updated_at"} == \
                    {k: v for k, v in s.items() if k != "updated_at"}
                if same and abs((ts_epoch(old["updated_at"]) or 0) - (ts_epoch(s["updated_at"]) or 0)) < 60:
                    continue  # only the activity time moved, by less than a minute: no event
                self.sessions[sid] = s
                self.store.x("insert or replace into sessions values(?,?,?)", (sid, json.dumps(s), now))
                self.hub.publish("session", s)
            for sid in [k for k in self.sessions if k not in built]:
                del self.sessions[sid]
                self.store.x("delete from sessions where id=?", (sid,))
                self.hub.publish("removed", {"id": sid})

    def emit_master_messages(self, row):
        meta = file_meta(row.get("log_path"))
        seen = self.master_seen.get(row["id"])
        if not meta or (seen and seen[0] == meta):
            return
        agent = AGENTS.get(row["agent"])
        msgs = agent.messages(row) if agent else []
        self.master_seen[row["id"]] = (meta, len(msgs))
        if seen is None:
            return  # first look: history is served by /sessions/{id}/messages
        for m in msgs[seen[1]:] if len(msgs) >= seen[1] else msgs[-1:]:
            self.hub.publish("message", {"session_id": row["id"], **m})

    def snapshot(self):
        with self.lock:
            sessions = sorted(self.sessions.values(), key=lambda s: (s["role"] != "master", s["role"] != "root",
                                                                    s["project"] or "", s["name"]))
            m = self.sessions.get(self.master_id) if self.master_id else None
            return {
                "schema_version": SCHEMA_VERSION,
                "machine": self.machine,
                "generated_at": iso(),
                "master": {"session_id": m["id"], "state": m["state"]} if m else None,
                "sessions": [dict(s) for s in sessions],
                "agents": {n: {"label": a.label, "capabilities": a.capabilities} for n, a in AGENTS.items()},
            }

    def get(self, sid):
        with self.lock:
            return self.rows.get(sid)

    def rows_for(self, agent):
        with self.lock:
            return [r for r in self.rows.values() if r.get("agent") == agent and not r.get("remote")]

    def tick_soon(self):
        self.wake.set()

    def set_qstate(self, keys, sid, status, client, question=""):
        """Record answered/skipped for question ids (or a thread-level key); None clears."""
        for k in keys:
            if status:
                self.store.x("insert or replace into qstate values(?,?,?,?,?,?)",
                             (k, sid, status, time.time(), client, question))
                self.qstate[k] = status
                self.qstate_at[k] = time.time()
            else:
                self.store.x("delete from qstate where key=?", (k,))
                self.qstate.pop(k, None)
                self.qstate_at.pop(k, None)

    def queued_for(self, sid):
        return bool(self.store.x("select 1 from queue where target=? limit 1", (sid,)))

    def deliver(self, row, text):
        t = TRANSPORTS.get(row.get("transport") or "cmux-send")
        if not t:
            return False, f"no transport {row.get('transport')!r}"
        return t.deliver(self, row, text)


def machine_name():
    rc, out, _ = run(["scutil", "--get", "LocalHostName"], timeout=3)
    return out.strip() if rc == 0 and out.strip() else socket.gethostname().split(".")[0]


# ---------------------------------------------------------------- sends ----

def do_send(state, body, client):
    send_id = uuid.uuid4().hex[:12]
    target = body.get("target")
    text = body.get("text") or ""
    rec = {"time": iso(), "send_id": send_id, "client": client, "target": target, "text": text,
           "question_id": body.get("question_id"), "attachments": []}

    def finish(code, status, detail):
        rec.update(status=status, detail=detail, result=status)
        log_send(rec)
        state.store.x("insert or replace into sends values(?,?,?)", (send_id, time.time(), json.dumps(rec)))
        resp = {"send_id": send_id, "status": status, "detail": detail}
        state.hub.publish("send", resp)
        log("send", send_id, target, status, detail)
        return code, dict(resp, schema_version=SCHEMA_VERSION)

    kind = body.get("kind") or "answer"
    rec["kind"] = kind
    sid = state.master_id if target == "master" else target
    r = state.get(sid) if sid else None
    rec["session_id"] = sid
    kind = {"hide": "skip", "hide_all": "skip_all", "unhide": "unskip", "unhide_all": "unskip_all"}.get(kind, kind)
    if kind not in ("answer", "skip", "skip_all", "unskip", "unskip_all"):
        return finish(400, "failed", f"unknown kind {kind!r}")
    if not r:
        return finish(404, "failed", "unknown target")
    qid = body.get("question_id")
    q = next((q for q in r["questions"] if q["id"] == qid), None) if qid else None
    if kind in ("skip", "unskip") and not q:
        return finish(404, "failed", "unknown question_id")
    if kind != "answer":
        # Skip = ignore/clear, stored in threadsd so every device and the console agree. Nothing is
        # ever sent to the thread. It stays cleared until the thread asks a new question (new ids).
        keys = [q["id"]] if q else [x["id"] for x in r["questions"]] + [thread_ask_key(r)]
        state.set_qstate(keys, r["id"], None if kind.startswith("unskip") else "skipped", client,
                         q["text"] if q else "")
        state.reapply()
        return finish(200, "unskipped" if kind.startswith("unskip") else "skipped",
                      ("back in Needs you" if kind.startswith("unskip") else "cleared from Needs you")
                      + "; nothing was sent to the thread")
    choices = body.get("choices") or []
    if kind == "answer" and not (isinstance(text, str) and text.strip()) and (choices or body.get("other")):
        # Quick-select answer composed here: "1, 3" or "1, 3 — plus: <other>" (or just the other text)
        nums = ", ".join(str(int(c)) for c in choices if str(c).isdigit())
        other = str(body.get("other") or "").strip()
        text = f"{nums} — plus: {other}" if nums and other else nums or other
        rec["text"] = text
    if not isinstance(text, str) or not (text.strip() or body.get("attachments")):
        return finish(400, "failed", "text is required")
    rec.update(surface=r.get("surface"), workspace=r.get("workspace"), agent=r.get("agent"), machine=r.get("machine"))
    if not r["capabilities"].get("submit"):
        return finish(409, "failed", f"agent {r.get('agent')!r} can't be sent to")
    if kind == "answer" and qid:
        if q is None:
            parts = qid.split(":")
            q = {"n": parts[-2] if len(parts) > 2 else "?", "text": ""}
        picked = re.match(r"^\s*(\d+(?:\s*,\s*\d+)*)\b", text)
        opts = {str(o["n"]): o["label"] for o in q.get("options") or []}
        if picked and opts:  # name the picked options so the reply reads on its own
            names = [opts.get(n.strip()) for n in picked.group(1).split(",")]
            if all(names):
                text = f'{picked.group(1)} ({"; ".join(names)}){text[picked.end():]}'
        text = f'Answer to question {q["n"]} ("{q["text"]}"): {text}'
    atts = body.get("attachments") or []
    if atts:
        d = os.path.join(UPLOADS, send_id)
        os.makedirs(d, exist_ok=True)
        for i, a in enumerate(atts):
            name = re.sub(r"[^\w.\- ]", "_", os.path.basename(a.get("name") or "") or f"file{i}")
            path = os.path.join(d, name)
            if os.path.exists(path):
                path = os.path.join(d, f"{i}-{name}")
            try:
                with open(path, "wb") as f:
                    f.write(base64.b64decode(a.get("data_base64") or ""))
            except Exception as e:
                return finish(400, "failed", f"bad attachment {name}: {e}")
            rec["attachments"].append(path)
            text += f"\nAttached: {path}"
    if len(text) > LONG_TEXT:
        d = os.path.join(UPLOADS, send_id)
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "message.md")
        with open(p, "w") as f:
            f.write(text)
        text = f"{text.splitlines()[0][:200]} … (long message from the phone; read {p} for the full text)"
    rec["delivered_text"] = text
    marks = ([q["id"]], "answered") if q else None  # recorded once delivered or queued
    if r.get("prompt") or state.queued_for(r["id"]):
        res = enqueue(state, send_id, r, text, rec, "answer the question above first" if r.get("prompt")
                      else "waiting behind an earlier queued message")
    else:
        ok, detail = state.deliver(r, text)
        agent = AGENTS.get(r.get("agent"))
        if not ok and agent and detail in agent.retry_details:
            res = enqueue(state, send_id, r, text, rec, "the input box is hidden; will retry")
        else:
            res = finish(200 if ok else 409, "delivered" if ok else "failed", detail)
    if marks and res[1]["status"] in ("delivered", "queued"):
        state.set_qstate(marks[0], r["id"], marks[1], client, q["text"] if q else "")
        state.tick_soon()
    return res


def enqueue(state, send_id, r, text, rec, reason):
    """Hold a send until the session's input box is back (persisted; see queue_loop)."""
    rec.update(status="queued", detail=reason, result="queued")
    log_send(rec)
    state.store.x("insert or replace into queue values(?,?,?,?,?,?,?)",
                  (send_id, time.time(), r["id"], text, json.dumps(rec), 0, reason))
    resp = {"send_id": send_id, "status": "queued", "detail": reason}
    state.hub.publish("send", resp)
    log("send", send_id, r["id"], "queued", reason)
    return 202, dict(resp, schema_version=SCHEMA_VERSION)


def queue_loop(state):
    """Deliver queued sends once their session shows its input box again, oldest first, one per
    session per pass. Each attempt is retried; a send fails only after QUEUE_MAX_AGE, or when the
    session is gone, or on an error that retrying can't fix."""
    while True:
        time.sleep(1.5)
        try:
            done = set()
            for send_id, ts, target, text, rec, attempts, last in state.store.x(
                    "select send_id, ts, target, text, rec, attempts, last_detail from queue order by ts"):
                if target in done:
                    continue
                done.add(target)
                rec = json.loads(rec)
                r = state.get(target)
                status, detail = None, None
                if time.time() - ts > QUEUE_MAX_AGE:
                    status, detail = "failed", f"not delivered within {QUEUE_MAX_AGE // 3600} h ({last})"
                elif not r:
                    if time.time() - ts > 120:
                        status, detail = "failed", "session is gone"
                elif r.get("prompt") or r.get("working") and r.get("agent") == "codex":
                    continue  # still waiting for the answer (Codex: for its turn to end)
                else:
                    ok, d = state.deliver(r, text)
                    if ok:
                        status, detail = "delivered", d
                    else:
                        state.store.x("update queue set attempts=attempts+1, last_detail=? where send_id=?", (d, send_id))
                        if attempts + 1 >= 3 and attempts % 3 == 2:
                            log("queue: still retrying", send_id, target, d)
                if status:
                    state.store.x("delete from queue where send_id=?", (send_id,))
                    rec.update(time=iso(), status=status, detail=detail, result=status, attempts=attempts + 1)
                    log_send(rec)
                    state.hub.publish("send", {"send_id": send_id, "status": status, "detail": detail})
                    log("send", send_id, target, status, detail, "(from queue)")
        except Exception as e:
            log("queue error", repr(e))


def do_prompt_answer(state, body, client):
    target = body.get("session") or body.get("target")
    sid = state.master_id if target == "master" else target
    r = state.get(sid) if sid else None
    fail = lambda code, d: (code, {"status": "failed", "detail": d, "schema_version": SCHEMA_VERSION})
    if not r:
        return fail(404, "unknown session")
    agent = AGENTS.get(r.get("agent"))
    if not agent or not r["capabilities"].get("prompts"):
        return fail(409, "this agent can't answer prompts")
    p = r.get("prompt")
    if not p:
        return fail(409, "no open prompt")
    if body.get("prompt_id") != p["id"]:
        return fail(409, "prompt changed; reload /state")
    answers = body.get("answers")
    if answers is None:
        answers = [{"choices": body.get("choices") or [], "other_text": body.get("other_text")}]
    if not isinstance(answers, list) or len(answers) != len(p["questions"]):
        return fail(400, f"need one answer per question ({len(p['questions'])})")
    for q, a in zip(p["questions"], answers):
        labels = [o["label"] for o in q["options"]]
        ch, other = a.get("choices") or [], (a.get("other_text") or "").strip()
        if other and not p.get("allows_other"):
            return fail(400, "this prompt takes no free text")
        if not ch and not other:
            return fail(400, f"no answer for {q['text']!r}")
        if not q["multi_select"] and (len(ch) + bool(other)) != 1:
            return fail(400, f"pick exactly one for {q['text']!r}")
        bad = [c for c in ch if c not in labels]
        if bad:
            return fail(400, f"unknown option(s) {bad} for {q['text']!r}")
    with TRANSPORT_LOCK:
        ok, detail = agent.answer_prompt(r, p, answers)
    log_send({"time": iso(), "send_id": "ans-" + uuid.uuid4().hex[:8], "client": client, "target": r["id"],
              "surface": r.get("surface"), "text": json.dumps(answers, ensure_ascii=False), "prompt_id": p["id"],
              "attachments": [], "status": "answered" if ok else "failed", "result": "answered" if ok else "failed",
              "detail": detail})
    state.hub.publish("prompt_answer", {"session_id": r["id"], "prompt_id": p["id"],
                                        "status": "answered" if ok else "failed", "detail": detail})
    log("prompt answer", r["id"], p["id"], ok, detail)
    return (200 if ok else 409), {"status": "answered" if ok else "failed", "detail": detail,
                                  "schema_version": SCHEMA_VERSION}


def do_traces(state, body, client, token):
    """POST /v1/traces: an OTLP/HTTP JSON export. Redacted once here, then given to every telemetry sink."""
    if not isinstance(body.get("resourceSpans"), list):
        return 400, {"error": "expected an OTLP ExportTraceServiceRequest (resourceSpans)"}
    doc = redact(body, extra=(token,))
    meta = {"client": client, "received_at": time.time(), "snapshot": state.snapshot}
    for sink in TELEMETRY.values():
        try:
            sink.ingest(doc, meta)
        except Exception as e:  # one sink failing must not lose the others
            log(f"telemetry {sink.name} error", repr(e))
    return 200, {"partialSuccess": {}}


def do_interrupt(state, body, client):
    r = state.get(state.master_id if body.get("target") == "master" else body.get("target"))
    if not r:
        return 404, {"status": "failed", "detail": "unknown target", "schema_version": SCHEMA_VERSION}
    agent = AGENTS.get(r.get("agent"))
    if not agent or not r["capabilities"].get("interrupt") or r.get("remote"):
        return 409, {"status": "failed", "detail": "interrupt not supported", "schema_version": SCHEMA_VERSION}
    # Right after a send, the live state can lag a second or two behind the screen.
    deadline = time.time() + 3
    while not r.get("working") and time.time() < deadline:
        time.sleep(0.5)
        r = state.get(r["id"]) or r
    ok, detail = agent.interrupt(r)
    log_send({"time": iso(), "send_id": "int-" + uuid.uuid4().hex[:8], "client": client, "target": r["id"],
              "surface": r.get("surface"), "text": "<interrupt>", "attachments": [],
              "status": "delivered" if ok else "failed", "result": "delivered" if ok else "failed", "detail": detail})
    return (200 if ok else 409), {"status": "delivered" if ok else "failed", "detail": detail,
                                  "schema_version": SCHEMA_VERSION}


def upkeep(state, dry=False):
    """Each agent's periodic chores (Claude: Remote Control recovery)."""
    out = {}
    for name, agent in AGENTS.items():
        if hasattr(agent, "upkeep"):
            out[name] = agent.upkeep(state, dry=dry)
    return out


# ----------------------------------------------------------------- http ----

def load_token():
    os.makedirs(CONF, mode=0o700, exist_ok=True)
    if not os.path.exists(TOKEN_PATH):
        fd = os.open(TOKEN_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_urlsafe(32) + "\n")
    os.chmod(TOKEN_PATH, 0o600)
    with open(TOKEN_PATH) as f:
        return f.read().strip()


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        if not isinstance(sys.exc_info()[1], (ConnectionError, BrokenPipeError, TimeoutError)):
            super().handle_error(request, client_address)


def make_handler(state, token):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "threadsd/" + VERSION

        def log_message(self, fmt, *a):
            pass

        def client(self):
            return f"{self.client_address[0]} {self.headers.get('User-Agent', '')}".strip()

        def json(self, code, obj):
            b = json.dumps(dict(obj, schema_version=SCHEMA_VERSION), ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def authed(self):
            h = self.headers.get("Authorization", "")
            if h.startswith("Bearer ") and hmac.compare_digest(h[7:].strip(), token):
                return True
            log("401", self.client(), self.command, self.path)
            self.json(401, {"error": "unauthorized"})
            return False

        def do_GET(self):
            if not self.authed():
                return
            u = urlparse(self.path)
            if u.path == "/healthz":
                return self.json(200, {"ok": True, "version": VERSION, "cmux_error": state.ctx.cmux_error,
                                       "pid": LIFE.get("pid"), "started_at": iso(LIFE.get("started_at")),
                                       "previous_run": PREVIOUS})
            if u.path == "/state":
                return self.json(200, state.snapshot())
            if u.path == "/agents":
                return self.json(200, {"agents": {n: a.describe() for n, a in AGENTS.items()},
                                       "sources": list(SOURCES), "transports": list(TRANSPORTS), "uis": list(UIS),
                                       "telemetry": list(TELEMETRY)})
            m = re.fullmatch(r"/ui/([\w-]+)", u.path)
            if m and m.group(1) in UIS:
                return self.json(200, UIS[m.group(1)].render(state.snapshot()))
            m = re.fullmatch(r"/sessions/([^/]+)/messages", u.path)
            if m:
                r = state.get(state.master_id if m.group(1) == "master" else m.group(1))
                agent = AGENTS.get(r.get("agent")) if r else None
                if not agent or r.get("remote"):
                    return self.json(404, {"error": "unknown session"})
                try:
                    limit = max(1, min(500, int(parse_qs(u.query).get("limit", ["50"])[0])))
                except ValueError:
                    limit = 50
                q = parse_qs(u.query)
                msgs = agent.messages(r)
                if q.get("include_system", ["0"])[0] not in ("1", "true"):
                    msgs = [m for m in msgs if not m.get("system")]
                return self.json(200, {"messages": msgs[-limit:]})
            if u.path == "/voice/brains":
                return self.json(200, {"brains": [b for p in BRAINS.values() for b in p.list()]})
            if u.path.startswith("/voice/calls"):
                return self.voice_calls_get(u)
            if u.path == "/artifacts":
                code, body = source_artifacts.list_artifacts(parse_qs(u.query), state.snapshot()["sessions"])
                return self.json(code, body)
            m = re.fullmatch(r"/artifacts/([^/]+)", u.path)
            if m:
                code, body = source_artifacts.get_artifact(m.group(1), state.snapshot()["sessions"])
                return self.json(code, body)
            if u.path == "/events":
                return self.events()
            self.json(404, {"error": "not found"})

        def do_POST(self):
            if not self.authed():
                return
            u = urlparse(self.path)
            try:
                n = int(self.headers.get("Content-Length") or 0)
                if n > 60 << 20:
                    return self.json(413, {"error": "too large"})
                data = self.rfile.read(n) or b"{}"
                if "protobuf" in self.headers.get("Content-Type", ""):
                    return self.json(415, {"error": "send OTLP/HTTP as JSON (Content-Type: application/json)"})
                if self.headers.get("Content-Encoding", "").lower() == "gzip":
                    z = zlib.decompressobj(16 + zlib.MAX_WBITS)  # gzip framing; capped so a small body can't balloon
                    data = z.decompress(data, 64 << 20)
                    if z.unconsumed_tail:
                        return self.json(413, {"error": "too large"})
                body = json.loads(data)
            except Exception:
                return self.json(400, {"error": "bad json"})
            if u.path == "/send":
                return self.json(*do_send(state, body, self.client()))
            m = re.fullmatch(r"/ui/([\w-]+)", u.path)
            if m and m.group(1) in UIS:
                return self.json(*UIS[m.group(1)].handle(state, body, self.client() + f" via ui/{m.group(1)}",
                                                         lambda b: do_send(state, b, self.client() + f" via ui/{m.group(1)}")))
            if u.path == "/prompt/answer":
                return self.json(*do_prompt_answer(state, body, self.client()))
            if u.path == "/interrupt":
                return self.json(*do_interrupt(state, body, self.client()))
            if u.path == "/rc-recover":
                return self.json(200, {"recovered": upkeep(state, dry=bool(body.get("dry_run")))})
            if u.path == "/v1/traces":
                return self.json(*do_traces(state, body, self.client(), token))
            m = re.fullmatch(r"/voice/calls/([0-9a-f]{32})/rating", u.path)
            if m and call_store():
                if body.get("rating") not in ("good", "bad", None):
                    return self.json(400, {"error": "rating must be good, bad or null"})
                ok = call_store().rate(m.group(1), body.get("rating"), body.get("note"))
                return self.json(200 if ok else 404, {"rated": ok} if ok else {"error": "unknown call"})
            m = re.fullmatch(r"/artifacts/([^/]+)/pin", u.path)
            if m:
                code, out = source_artifacts.pin_artifact(m.group(1), bool(body.get("pinned", True)),
                                                           state.snapshot()["sessions"])
                return self.json(code, out)
            self.json(404, {"error": "not found"})

        def voice_calls_get(self, u):
            store, q = call_store(), parse_qs(u.query)
            if not store:
                return self.json(404, {"error": "no call store"})
            if u.path == "/voice/calls":
                try:
                    limit = max(1, min(500, int(q.get("limit", ["50"])[0])))
                except ValueError:
                    limit = 50
                return self.json(200, {"calls": store.calls(limit)})
            if u.path == "/voice/calls/export":
                rating = q.get("rating", ["good"])[0]
                if rating not in ("good", "bad"):
                    return self.json(400, {"error": "rating must be good or bad"})
                rows = store.export(rating, None)
                if q.get("format", ["json"])[0] != "jsonl":
                    return self.json(200, {"rating": rating, "examples": rows})
                b = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                return self.wfile.write(b)
            m = re.fullmatch(r"/voice/calls/([0-9a-f]{32})", u.path)
            c = store.call(m.group(1)) if m else None
            return self.json(200, {"call": c}) if c else self.json(404, {"error": "unknown call"})

        def events(self):
            q = state.hub.subscribe()
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()
                self.close_connection = True
                for s in state.snapshot()["sessions"]:
                    self.sse("session", dict(s, schema_version=SCHEMA_VERSION))
                self.wfile.flush()
                beat = time.time() + 15  # every 15 s, busy or not, so clients can time out on silence
                while True:
                    try:
                        ev, data = q.get(timeout=max(0.1, beat - time.time()))
                        self.sse(ev, data)
                    except queue.Empty:
                        pass
                    if time.time() >= beat:
                        self.wfile.write(b": heartbeat\n\n")
                        beat = time.time() + 15
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                state.hub.unsubscribe(q)

        def sse(self, ev, data):
            self.wfile.write(f"event: {ev}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode())

    return H


TS_NET = re.compile(r"inet (100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d+\.\d+)")


def tailscale_ip():
    """This Mac's Tailscale address (100.64.0.0/10 on an up utun interface), or None.
    Read from ifconfig: the Tailscale app's own CLI hangs outside a login shell (as under launchd)."""
    rc, out, _ = run(["/sbin/ifconfig"], timeout=5)
    up = False
    for line in out.split("\n"):
        if line and not line[0].isspace():
            up = line.startswith("utun") and "UP" in line and "RUNNING" in line
        elif up:
            m = TS_NET.search(line)
            if m:
                return m.group(1)
    return None


# ----------------------------------------------------------------- main ----

def loop(state, ip=None):
    last_ip = 0
    while True:
        if ip and time.time() - last_ip > 30:
            last_ip = time.time()
            if tailscale_ip() != ip:
                log(f"Tailscale address {ip} is gone; exiting so launchd restarts us")
                os._exit(1)
        try:
            state.tick()
        except Exception as e:
            log("loop error", repr(e))
        state.wake.wait(0.7)
        state.wake.clear()


def upkeep_loop(state):
    while UPKEEP_INTERVAL > 0:
        time.sleep(UPKEEP_INTERVAL)
        try:
            upkeep(state)
        except Exception as e:
            log("upkeep error", repr(e))


def new_state():
    for d in (DATA, SPOOL, UPLOADS):
        os.makedirs(d, exist_ok=True)
    os.chmod(DATA, 0o700)
    st = State()
    st.tick(force=True)
    return st


# ---- lifecycle: say how every run ended, so a death is never silent ----
LIFE_PATH = os.path.join(DATA, "lifecycle.json")
LIFE = {}       # this run: pid, started_at, heartbeat, exit, boot, python, script
PREVIOUS = None  # how the previous run ended (for /healthz)


def boot_time():
    m = re.search(r"sec = (\d+)", run(["sysctl", "-n", "kern.boottime"])[1] or "")
    return int(m.group(1)) if m else None


def life_write(**kw):
    LIFE.update(kw)
    try:
        tmp = LIFE_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(LIFE, f)
        os.replace(tmp, LIFE_PATH)
    except OSError:
        pass


def life_start():
    """Log how the previous run ended and start this one's record."""
    global PREVIOUS
    try:
        prev = json.load(open(LIFE_PATH))
    except (OSError, ValueError):
        prev = None
    boot = boot_time()
    if prev:
        ended = prev.get("exit")
        if not ended:
            hb = prev.get("heartbeat") or 0
            ended = ("the Mac restarted (booted %s)" % iso(boot)) if boot and boot > hb else \
                "killed without a signal (SIGKILL, crash or out of memory)"
        PREVIOUS = {"pid": prev.get("pid"), "started_at": iso(prev.get("started_at")),
                    "last_heartbeat": iso(prev.get("heartbeat")), "ended": ended,
                    "ended_at": iso(prev.get("exit_at")) if prev.get("exit_at") else None}
        log(f"previous run: pid {prev.get('pid')} started {PREVIOUS['started_at']}, last heartbeat "
            f"{PREVIOUS['last_heartbeat']}, ended: {ended}")
    now = time.time()
    life_write(pid=os.getpid(), started_at=now, heartbeat=now, exit=None, exit_at=None, boot=boot,
               python=sys.version.split()[0], script=os.path.abspath(__file__))


def life_end(why):
    if not LIFE.get("exit"):
        life_write(exit=why, exit_at=time.time())
        log(f"exit: {why}")


def heartbeat_loop(st):
    """lifecycle.json every 60 s; a log line every 10 min."""
    n = 0
    while True:
        time.sleep(60)
        n += 1
        life_write(heartbeat=time.time())
        if n % 10 == 0:
            log(f"heartbeat: up {int((time.time() - LIFE['started_at']) / 3600)}h, {len(st.rows)} sessions, "
                f"{len(st.hub.subs)} event streams, cmux {'ok' if not st.ctx.cmux_error else st.ctx.cmux_error}")


def install_exit_logging():
    import atexit
    import signal
    import traceback

    def on_signal(sig, _frame):
        name = signal.Signals(sig).name
        if sig == signal.SIGHUP:  # the terminal it was started from went away: keep running
            log("got SIGHUP (its terminal closed); still running")
            return
        life_end(f"signal {name}")
        os._exit(0)

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGQUIT):
        signal.signal(sig, on_signal)
    atexit.register(lambda: life_end("exited"))
    threading.excepthook = lambda a: log(f"thread {a.thread.name if a.thread else '?'} crashed:\n" +
                                         "".join(traceback.format_exception(a.exc_type, a.exc_value, a.exc_traceback)))


def serve():
    try:
        os.setsid()  # leave the terminal's session: closing that tab can't signal threadsd
    except OSError:
        pass  # already a session or group leader
    install_exit_logging()
    life_start()
    token = load_token()
    ip = os.environ.get("THREADSD_BIND") or None
    while not ip:
        ip = tailscale_ip()
        if not ip:
            log("Tailscale is not up (no 100.x address); not listening. Retrying in 15 s.")
            time.sleep(15)
    st = new_state()
    if st.ctx.cmux_error:
        log("WARNING: no cmux access:", st.ctx.cmux_error, "- see SKILL.md / ui/threadsd-api.md: start it with start.sh")
    threading.Thread(target=loop, args=(st, None if os.environ.get("THREADSD_BIND") else ip), daemon=True).start()
    threading.Thread(target=upkeep_loop, args=(st,), daemon=True).start()
    threading.Thread(target=queue_loop, args=(st,), daemon=True).start()
    threading.Thread(target=heartbeat_loop, args=(st,), daemon=True, name="heartbeat").start()
    if source_artifacts.available():
        threading.Thread(target=source_artifacts.watch_index, args=(st.hub, threading.Event()),
                         daemon=True).start()
    srv = Server((ip, PORT), make_handler(st, token))
    log(f"threadsd {VERSION} listening on http://{ip}:{PORT} (machine {st.machine}; "
        f"agents {', '.join(AGENTS)}; sources {', '.join(SOURCES)}; transports {', '.join(TRANSPORTS)})")
    try:
        srv.serve_forever()
    except BaseException as e:
        import traceback
        log("serve crashed:\n" + traceback.format_exc())
        life_end(f"crashed: {type(e).__name__}: {e}")
        raise


def health():
    """0: the daemon answers /healthz. 1: it doesn't. 2: not installed here (no token). 3: Tailscale is down."""
    import urllib.request
    if not os.path.exists(TOKEN_PATH):
        print("threadsd is not installed on this Mac (no token)")
        return 2
    ip = os.environ.get("THREADSD_BIND") or tailscale_ip()
    if not ip:
        print("Tailscale is down; threadsd can't listen until it is up")
        return 3
    with open(TOKEN_PATH) as f:
        req = urllib.request.Request(f"http://{ip}:{PORT}/healthz", headers={"Authorization": "Bearer " + f.read().strip()})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # straight to the tailnet, never a proxy
    for _ in range(2):  # one retry: a busy daemon is slow, not dead
        try:
            h = json.loads(opener.open(req, timeout=5).read())
            print(f"threadsd ok: pid {h.get('pid')}, up since {h.get('started_at')}"
                  + (f", cmux error: {h['cmux_error']}" if h.get("cmux_error") else ""))
            return 0
        except Exception as e:
            err = e
    print(f"threadsd doesn't answer on {ip}:{PORT}: {err}")
    return 1


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "serve"
    if cmd == "serve":
        serve()
    elif cmd == "state":
        print(json.dumps(new_state().snapshot(), ensure_ascii=False, indent=1))
    elif cmd == "submit":
        st = new_state()
        r = next((r for r in st.rows.values() if r.get("surface") == sys.argv[2]), None)
        print(st.deliver(r, sys.argv[4]) if r else "no session on that surface")
    elif cmd == "rc-recover":
        print(json.dumps(upkeep(new_state(), dry="--dry-run" in sys.argv), ensure_ascii=False))
    elif cmd == "health":
        sys.exit(health())
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
