"""Paths and helpers shared by the core and the plugins."""
import hashlib
import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone

SCHEMA_VERSION = 1
HOME = os.path.expanduser("~")
CONF = os.path.join(HOME, ".config/threadsd")
DATA = os.path.join(HOME, ".local/share/threadsd")
SPOOL = os.path.join(DATA, "spool")
UPLOADS = os.path.join(DATA, "uploads")
DIAGNOSTICS = os.path.join(DATA, "diagnostics")
SENDS_LOG = os.path.join(DATA, "sends.jsonl")
DB_PATH = os.path.join(DATA, "threadsd.db")
TOKEN_PATH = os.path.join(CONF, "token")
PEERS_PATH = os.path.join(CONF, "peers.json")
MARKS = "▶?✓○✗"


def log(*a):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), *a, flush=True)


def iso(t=None):
    return datetime.fromtimestamp(t if t is not None else time.time(), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(args, timeout=10, env=None):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=env)
        return p.returncode, p.stdout, p.stderr
    except Exception as e:
        return 1, "", str(e)


def norm(s):
    return re.sub(r"\s+", "", (s or "").replace("\xa0", ""))


def tail_jsonl(path, max_bytes):
    """Parsed rows of the last max_bytes of a JSONL file (first partial line dropped)."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
                f.readline()
            data = f.read()
    except OSError:
        return []
    out = []
    for raw in data.split(b"\n"):
        if raw.strip():
            try:
                out.append(json.loads(raw))
            except Exception:
                pass
    return out


def file_meta(path):
    try:
        st = os.stat(path)
        return (st.st_mtime, st.st_size)
    except (OSError, TypeError):
        return None


def log_send(rec):
    os.makedirs(DATA, exist_ok=True)
    with open(SENDS_LOG, "a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


STATUS_RE = re.compile(r"STATUS:\s*\**\s*(working|needs you|finished|failed)", re.I)


def last_status(text):
    ms = list(STATUS_RE.finditer(text or ""))
    return ms[-1] if ms else None


def needs_you_body(text):
    """The text of the last "STATUS: needs you" block with real content (up to the next STATUS
    line). A trailing one-liner such as "STATUS: needs you — report saved" doesn't count."""
    ms = list(STATUS_RE.finditer(text))
    for i in range(len(ms) - 1, -1, -1):
        if ms[i].group(1).lower() == "needs you":
            body = text[ms[i].end():ms[i + 1].start() if i + 1 < len(ms) else len(text)]
            if len(body.strip(" -—:\n")) > 40:
                return body
    return text if not ms else text[ms[-1].end():] or text


def parse_questions(sid, text, strict=False):
    """Questions in the needs-you block, each {id, n, text, options, multi, allow_other, parse}.
    The PARSERS plugins find questions and their numbered options (parser_numbered: the
    "(pick one)" / "(pick any)" convention, else a heuristic); without any, the last paragraph
    that asks something is the one question."""
    from . import PARSERS
    body = needs_you_body(text)
    items = []
    for p in sorted(PARSERS.values(), key=lambda p: p.priority):
        items = p.parse(body)
        if items:
            break
    if strict and items and not any("?" in i["text"] or i["options"] for i in items) \
            and len(body.strip()) < 80:
        items = []
    if not items:
        paras = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
        q = next((p for p in reversed(paras) if "?" in p), "")
        items = [{"text": re.sub(r"\s+", " ", q.replace("**", "")).strip(), "options": [], "multi": False,
                  "allow_other": True, "parse": None}] if q else []
    out = []
    for n, q in enumerate(items, 1):
        if not q["text"]:
            continue
        out.append(dict(q, id=f"{sid}:{n}:{hashlib.sha1(q['text'].encode()).hexdigest()[:8]}", n=n))
    return out


def message_facts(msgs):
    """From a chat ([{role, text, ts, id, system, origin}], oldest first) derive what the core needs:
    last: the last assistant message (None if someone else spoke last);
    ask:  the latest assistant message whose last STATUS line is "needs you" AND that actually asks
          something (a numbered question list, or a paragraph with "?"). One-line bookkeeping
          reports ("STATUS: needs you — report saved") are skipped. A later "finished"/"failed"
          report closes it. stale=True once the user (not another session) wrote after it."""
    real = [m for m in msgs if not m.get("system")]
    last = real[-1] if real and real[-1]["role"] == "assistant" else None
    ask = None
    for i in range(len(real) - 1, -1, -1):
        m = real[i]
        if m["role"] != "assistant":
            continue
        st = last_status(m["text"])
        if not st:
            continue
        kind = st.group(1).lower()
        if kind in ("finished", "failed"):
            break
        if kind == "needs you" and parse_questions("", m["text"], strict=True):
            ask = dict(m, stale=any(x["role"] == "user" and x.get("origin", "user") == "user"
                                   for x in real[i + 1:]))
            break
    return last, ask


def ts_epoch(ts):
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None
