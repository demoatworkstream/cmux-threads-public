"""Telemetry sink "calls": the voice-call store on this Mac. One OpenTelemetry trace per call.

Every OTLP/HTTP JSON export (POST /v1/traces, already redacted) is kept as it came, compressed,
in ~/.local/share/threadsd/voice-traces.db (table `otlp`), so the history can be replayed into
any OTel backend later. Its spans are indexed (table `spans`) for reading, and each call gets a
row (table `calls`) with the user's rating and the /state snapshot from when the call started.

Reads (GET /voice/calls...) come from here: the call list, one call's timeline and transcript
(with the crash and MetricKit reports of that call, from ~/.local/share/threadsd/diagnostics),
ratings, and the fine-tuning exports for brain/ (good calls -> chat examples, bad -> eval cases).
The span names and attributes it reads are the app's convention in threadsd/API.md ("Voice calls").
Nothing is sent anywhere else.
"""
import json
import os
import sqlite3
import threading
import time
import zlib

from . import Telemetry, register_telemetry
from ._common import DATA, DIAGNOSTICS, iso, log
from ._otel import spans as flatten

DB = os.path.join(DATA, "voice-traces.db")
KEEP_CALLS = 2000
ROOT = "voice.call"
RATINGS = ("good", "bad")


def _ns_iso(ns):
    return iso(ns / 1e9) if ns else None


def _json(s, default=None):
    if isinstance(s, (list, dict)):
        return s
    try:
        return json.loads(s)
    except (TypeError, ValueError):
        return default


@register_telemetry
class Calls(Telemetry):
    name = "calls"
    reads = True

    def __init__(self, path=None):
        self.path = path
        self.db = None
        self.lock = threading.Lock()

    def x(self, sql, args=()):
        with self.lock:
            if self.db is None:
                os.makedirs(os.path.dirname(self.path or DB), exist_ok=True)
                self.db = sqlite3.connect(self.path or DB, check_same_thread=False, timeout=5)
                self.db.executescript("""
                  pragma journal_mode=wal;
                  create table if not exists otlp(id integer primary key, received_at real, client text, body blob);
                  create table if not exists spans(trace_id text, span_id text, parent_id text, name text, kind integer,
                      start_ns integer, end_ns integer, status integer, status_message text, attrs text, events text,
                      resource text, scope text, primary key(trace_id, span_id));
                  create index if not exists spans_trace on spans(trace_id);
                  create table if not exists calls(trace_id text primary key, first_seen real, rating text, note text,
                      rated_at real, state blob);
                """)
            cur = self.db.execute(sql, args)
            self.db.commit()
            return cur.fetchall()

    # ------------------------------------------------------------------ write --

    def ingest(self, doc, meta):
        rows = flatten(doc)
        if not rows:
            return
        raw = zlib.compress(json.dumps(doc, ensure_ascii=False).encode())
        self.x("insert into otlp(received_at, client, body) values (?,?,?)", (meta.get("received_at", time.time()), meta.get("client"), raw))
        for s in rows:
            # A span may arrive more than once (the call's span rides along while open; the app retries
            # a batch it isn't sure was taken): the newest copy wins, but an open copy never replaces an ended one.
            self.x("insert into spans values (?,?,?,?,?,?,?,?,?,?,?,?,?) on conflict(trace_id, span_id) do update set "
                   "parent_id=excluded.parent_id, name=excluded.name, kind=excluded.kind, start_ns=excluded.start_ns, "
                   "end_ns=excluded.end_ns, status=excluded.status, status_message=excluded.status_message, attrs=excluded.attrs, "
                   "events=excluded.events, resource=excluded.resource, scope=excluded.scope "
                   "where excluded.end_ns > 0 or spans.end_ns = 0",
                   (s["trace_id"], s["span_id"], s["parent_id"], s["name"], s["kind"], s["start_ns"], s["end_ns"],
                    s["status"], s["status_message"], json.dumps(s["attrs"], ensure_ascii=False),
                    json.dumps(s["events"], ensure_ascii=False), json.dumps(s["resource"], ensure_ascii=False), s["scope"]))
        for tid in {s["trace_id"] for s in rows}:
            if not self.x("select 1 from calls where trace_id=?", (tid,)):
                snap = meta.get("snapshot")
                state = None
                try:
                    state = zlib.compress(json.dumps(snap(), ensure_ascii=False).encode()) if snap else None
                except Exception as e:  # the snapshot is a nicety for the eval cases
                    log("telemetry: no state snapshot", repr(e))
                self.x("insert or ignore into calls(trace_id, first_seen, state) values (?,?,?)", (tid, time.time(), state))
        self.prune()

    def prune(self):
        old = self.x("select trace_id from calls order by first_seen desc limit -1 offset ?", (KEEP_CALLS,))
        for (tid,) in old:
            self.x("delete from spans where trace_id=?", (tid,))
            self.x("delete from calls where trace_id=?", (tid,))

    def rate(self, trace_id, rating, note):
        if not self.x("select 1 from calls where trace_id=?", (trace_id,)):
            return False
        self.x("update calls set rating=?, note=?, rated_at=? where trace_id=?",
               (rating if rating in RATINGS else None, (note or "").strip()[:2000] or None, time.time(), trace_id))
        return True

    # ------------------------------------------------------------------- read --

    def _spans(self, trace_id):
        out = []
        for r in self.x("select span_id, parent_id, name, kind, start_ns, end_ns, status, status_message, attrs, events, resource, scope "
                        "from spans where trace_id=? order by start_ns", (trace_id,)):
            out.append({"span_id": r[0], "parent_id": r[1], "name": r[2], "kind": r[3], "start_ns": r[4], "end_ns": r[5],
                        "status": r[6], "status_message": r[7], "attrs": _json(r[8], {}), "events": _json(r[9], []),
                        "resource": _json(r[10], {}), "scope": r[11]})
        return out

    def _summary(self, trace_id, spans, row):
        root = next((s for s in spans if s["name"] == ROOT and not s["parent_id"]), None)
        start = min((s["start_ns"] for s in spans if s["start_ns"]), default=0)
        end = root["end_ns"] if root and root["end_ns"] else max((s["end_ns"] for s in spans), default=0)
        res = (root or (spans[0] if spans else {})).get("resource", {})
        ra = root["attrs"] if root else {}
        turns = [s for s in spans if s["name"] == "voice.turn"]
        ttfw = sorted(s["attrs"]["voice.ttfw_ms"] for s in turns if isinstance(s["attrs"].get("voice.ttfw_ms"), (int, float)))
        crashes = self._crashes(trace_id, start, end, res)
        status = "crashed" if crashes and any(c["kind"] != "error" for c in crashes) else \
                 "open" if not root or not root["end_ns"] else \
                 "error" if any(s["status"] == 2 for s in spans) else "ok"
        first = next((s["attrs"].get("voice.user.text") for s in turns if s["attrs"].get("voice.user.text")), None)
        return {
            "trace_id": trace_id, "started_at": _ns_iso(start), "ended_at": _ns_iso(end) if root and root["end_ns"] else None,
            "duration_s": round((end - start) / 1e9, 1) if start and end else None, "status": status,
            "title": first, "turns": len(turns), "brain": ra.get("voice.brain.name"),
            "ttfw_ms_p50": ttfw[len(ttfw) // 2] if ttfw else None,
            "errors": sum(1 for s in spans if s["status"] == 2), "barge_ins": sum(1 for s in spans for e in s["events"] if e["name"] == "voice.barge_in"),
            "crashes": len(crashes), "app_build": res.get("service.version") and f'{res.get("service.version")} ({res.get("threads.app.build", "?")})',
            "device": res.get("device.model.identifier"), "os": " ".join(str(x) for x in (res.get("os.name"), res.get("os.version")) if x),
            "rating": row[0] if row else None, "note": row[1] if row else None,
        }

    def _crashes(self, trace_id, start_ns, end_ns, res):
        """Diagnostics reports of this call: tagged with its trace id, or (MetricKit, which can't be
        tagged) a crash on the same build whose time window covers the call."""
        out = []
        if not os.path.isdir(DIAGNOSTICS):
            return out
        for f in sorted(os.listdir(DIAGNOSTICS)):
            if not f.endswith(".json"):
                continue
            try:
                with open(os.path.join(DIAGNOSTICS, f)) as fh:
                    r = json.load(fh)
            except (OSError, ValueError):
                continue
            match = "trace_id" if r.get("trace_id") == trace_id else None
            if not match and r.get("kind") == "metrickit" and start_ns and r.get("build") == res.get("threads.app.build"):
                for d in ((r.get("payload") or {}).get("crashDiagnostics") or []):
                    meta = d.get("diagnosticMetaData") or {}
                    t = (r.get("payload") or {}).get("timeStampEnd") or meta.get("timeStamp")
                    if t and _within(t, start_ns, end_ns):
                        match = "time"
            if match:
                text = r.get("text") or ""
                out.append({"file": f, "kind": r.get("kind"), "at": r.get("at"), "area": r.get("area"), "match": match,
                            "first_line": text.strip().split("\n")[0][:200]})
        return out

    def calls(self, limit=50):
        out = []
        for tid, rating, note in self.x("select trace_id, rating, note from calls order by first_seen desc limit ?", (limit,)):
            out.append(self._summary(tid, self._spans(tid), (rating, note)))
        return out

    def call(self, trace_id):
        row = self.x("select rating, note from calls where trace_id=?", (trace_id,))
        spans = self._spans(trace_id)
        if not row and not spans:
            return None
        summary = self._summary(trace_id, spans, row[0] if row else None)
        start = min((s["start_ns"] for s in spans if s["start_ns"]), default=0)
        timeline = [{"span_id": s["span_id"], "parent_id": s["parent_id"], "name": s["name"],
                     "start_ms": round((s["start_ns"] - start) / 1e6) if start else 0,
                     "duration_ms": round((s["end_ns"] - s["start_ns"]) / 1e6) if s["end_ns"] else None,
                     "status": ("error" if s["status"] == 2 else "ok" if s["end_ns"] else "open"),
                     "status_message": s["status_message"], "attrs": s["attrs"],
                     "events": [dict(e, at_ms=round((e["time_ns"] - start) / 1e6) if start else 0) for e in s["events"]]}
                    for s in spans]
        by_id = {s["span_id"]: s for s in spans}
        transcript = [{"role": s["attrs"]["voice.transcript.role"], "text": s["attrs"].get("voice.transcript.text", ""),
                       "at": _ns_iso(s["start_ns"]),
                       "via": by_id.get(s["parent_id"], {}).get("attrs", {}).get("voice.reply.via")
                       if s["attrs"]["voice.transcript.role"] == "assistant" else None,
                       "interrupted": bool(s["attrs"].get("voice.speak.interrupted")),
                       "outcome": s["attrs"].get("threads.ui.outcome")}
                      for s in spans if s["attrs"].get("voice.transcript.role")]
        crashes = self._crashes(trace_id, start, max((s["end_ns"] for s in spans), default=0),
                                spans[0]["resource"] if spans else {})
        return dict(summary, resource=spans[0]["resource"] if spans else {}, spans=timeline,
                    transcript=transcript, crash_reports=crashes)

    def raw(self, since_id=0, limit=500):
        """The stored OTLP exports, oldest first, for replaying into another backend."""
        return [(i, json.loads(zlib.decompress(b))) for i, b in
                self.x("select id, body from otlp where id > ? order by id limit ?", (since_id, limit))]

    # ----------------------------------------------------------------- export --

    def export(self, rating, fmt=None):
        """good -> brain/data/seeds/calls-good.jsonl lines: each model request of the call exactly as
        sent (messages, tools) plus the response message. bad -> calls-bad.jsonl: one eval Case per
        turn ({id, cat, turns, expect.not, state, note}): the brain must not repeat what it did."""
        out = []
        for tid, note, state in self.x("select trace_id, note, state from calls where rating=? order by first_seen", (rating,)):
            spans = self._spans(tid)
            if rating == "good":
                for s in spans:
                    a = s["attrs"]
                    if a.get("gen_ai.operation.name") != "chat" or s["status"] == 2 or a.get("threads.trainable") is False:
                        continue
                    msgs, reply, tools = _json(a.get("gen_ai.input.messages")), _json(a.get("gen_ai.output.messages")), \
                        _json(a.get("gen_ai.tool.definitions"))
                    if not isinstance(msgs, list) or not isinstance(reply, list) or not reply:
                        continue
                    out.append({"messages": msgs + reply[:1], "tools": tools or [], "kind": "call-good",
                                "source": f"call:{tid}", "span_id": s["span_id"], "model": a.get("gen_ai.request.model")})
            else:
                snap = _json(zlib.decompress(state).decode()) if state else None
                said = []  # the call so far: the case replays every earlier turn, then judges this one
                for s in spans:
                    a = s["attrs"]
                    if s["name"] != "voice.turn" or not a.get("voice.user.text"):
                        continue
                    said.append(a["voice.user.text"])
                    did = {"kind": a.get("voice.action.kind") or "say"}
                    if a.get("voice.action.target"):
                        did["target"] = a["voice.action.target"]
                    if did["kind"] == "say" and a.get("voice.reply.text"):
                        did["speech_contains"] = a["voice.reply.text"][:80]
                    if did == {"kind": "say"}:
                        continue  # nothing specific to avoid
                    case = {"id": f"call-{tid[:12]}-{a.get('voice.turn.n', len(out))}", "cat": "call-bad",
                            "turns": list(said), "expect": {"not": did},
                            "got": {k: a.get(f"voice.{k}") for k in ("reply.text", "reply.via", "action.kind", "action.target",
                                                                      "action.question", "action.text") if a.get(f"voice.{k}") is not None},
                            "note": note, "source": f"call:{tid}"}
                    if snap:
                        case["state"] = snap
                    out.append(case)
        return out


def _within(t, start_ns, end_ns):
    """MetricKit times look like "2026-10-25 14:03:00 +0000"; allow its one-minute granularity."""
    from datetime import datetime
    for f in ("%Y-%m-%d %H:%M:%S %z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            ts = datetime.strptime(t.replace("Z", "+0000"), f).timestamp() * 1e9
            return start_ns - 60e9 <= ts <= (end_ns or start_ns) + 60e9
        except (ValueError, AttributeError):
            continue
    return False
