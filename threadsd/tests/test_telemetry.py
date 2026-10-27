"""POST /v1/traces -> the call store: list, one call's timeline, ratings, crash links, exports.
Run: python3 threadsd/tests/test_telemetry.py"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from plugins import _otel, telemetry_calls, telemetry_forward  # noqa: E402

T = "0af7651916cd43dd8448eb211c80319c"
T0 = 1761400000_000_000_000  # ns


def kv(k, v):
    if isinstance(v, bool):
        return {"key": k, "value": {"boolValue": v}}
    if isinstance(v, int):
        return {"key": k, "value": {"intValue": str(v)}}
    return {"key": k, "value": {"stringValue": v}}


def span(sid, parent, name, start_ms, end_ms, attrs, status=0, events=()):
    return {"traceId": T, "spanId": sid, "parentSpanId": parent, "name": name,
            "startTimeUnixNano": str(T0 + start_ms * 1_000_000), "endTimeUnixNano": str(T0 + end_ms * 1_000_000) if end_ms else "0",
            "attributes": [kv(k, v) for k, v in attrs.items()], "status": {"code": status},
            "events": [{"name": e, "timeUnixNano": str(T0 + start_ms * 1_000_000)} for e in events]}


def export(spans):
    return {"resourceSpans": [{"resource": {"attributes": [kv("service.name", "threads-ios"), kv("service.version", "1.0"),
                                                           kv("threads.app.build", "42"), kv("device.model.identifier", "iPhone18,1")]},
                               "scopeSpans": [{"scope": {"name": "threads.voice"}, "spans": spans}]}]}


MSGS = [{"role": "system", "content": "You are Threads."}, {"role": "user", "content": "tell magpie ship it"}]
REPLY = [{"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "send_to_thread", "arguments": {"thread": "magpie", "text": "Ship it"}}}]}]


class TelemetryTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        telemetry_calls.DIAGNOSTICS = os.path.join(d, "diagnostics")
        os.makedirs(telemetry_calls.DIAGNOSTICS)
        self.store = telemetry_calls.Calls(os.path.join(d, "voice.db"))
        meta = {"client": "test", "snapshot": lambda: {"sessions": [{"id": "s1", "name": "magpie"}]}}
        # Two exports, as the app sends them: the turn first, the call's root span at hang-up.
        self.store.ingest(export([
            span("b000000000000001", "a000000000000000", "voice.turn", 1000, 3000,
                 {"voice.turn.n": 1, "voice.user.text": "tell magpie ship it", "voice.action.kind": "toThread",
                  "voice.action.target": "s1", "voice.reply.text": "Tell Magpie: Ship it. Send it?", "voice.ttfw_ms": 900}),
            span("c000000000000001", "b000000000000001", "chat default_model", 1100, 1800,
                 {"gen_ai.operation.name": "chat", "gen_ai.request.model": "default_model",
                  "gen_ai.input.messages": json.dumps(MSGS), "gen_ai.output.messages": json.dumps(REPLY),
                  "gen_ai.tool.definitions": json.dumps([{"type": "function", "function": {"name": "send_to_thread"}}])}),
            span("d000000000000001", "b000000000000001", "voice.speak", 1900, 2900,
                 {"voice.transcript.role": "assistant", "voice.transcript.text": "Tell Magpie: Ship it. Send it?",
                  "voice.speak.interrupted": True}, events=["voice.barge_in"]),
        ]), meta)
        self.store.ingest(export([span("a000000000000000", "", "voice.call", 0, 5000, {"voice.brain.name": "Mac · Qwen3 4B"})]), meta)

    def test_list_and_detail(self):
        c = self.store.calls()[0]
        self.assertEqual((c["trace_id"], c["status"], c["turns"], c["brain"], c["ttfw_ms_p50"], c["barge_ins"], c["duration_s"]),
                         (T, "ok", 1, "Mac · Qwen3 4B", 900, 1, 5.0))
        self.assertEqual(c["app_build"], "1.0 (42)")
        d = self.store.call(T)
        self.assertEqual([s["name"] for s in d["spans"]], ["voice.call", "voice.turn", "chat default_model", "voice.speak"])
        self.assertEqual(d["transcript"][0]["text"], "Tell Magpie: Ship it. Send it?")
        self.assertIsNone(self.store.call("f" * 32))

    def test_rating_and_exports(self):
        self.assertTrue(self.store.rate(T, "good", ""))
        good = self.store.export("good")
        self.assertEqual(len(good), 1)
        self.assertEqual(good[0]["messages"], MSGS + REPLY)
        self.assertEqual((good[0]["kind"], good[0]["source"]), ("call-good", f"call:{T}"))
        self.assertEqual(self.store.export("bad"), [])
        self.store.rate(T, "bad", "it should have asked which thread")
        case = self.store.export("bad")[0]
        self.assertEqual(case["expect"], {"not": {"kind": "toThread", "target": "s1"}})
        self.assertEqual((case["turns"], case["note"], case["state"]["sessions"][0]["id"]),
                         (["tell magpie ship it"], "it should have asked which thread", "s1"))
        self.assertFalse(self.store.rate("f" * 32, "good", ""))

    def test_crash_links_by_trace_id(self):
        with open(os.path.join(telemetry_calls.DIAGNOSTICS, "x-crash.json"), "w") as f:
            json.dump({"kind": "crash", "trace_id": T, "text": "--- Crashed: signal 6 ---"}, f)
        self.assertEqual(self.store.calls()[0]["status"], "crashed")
        self.assertEqual(self.store.call(T)["crash_reports"][0]["match"], "trace_id")

    def test_open_call_without_root(self):
        self.store.ingest(export([dict(span("e000000000000001", "a1", "voice.turn", 0, 10, {}), traceId="1" * 32)]), {})
        self.assertEqual(self.store.call("1" * 32)["status"], "open")

    def test_open_copy_never_replaces_ended(self):
        self.store.ingest(export([span("a000000000000000", "", "voice.call", 0, 0, {"voice.brain.name": "late"})]), {})
        self.assertEqual(self.store.calls()[0]["status"], "ok")

    def test_raw_kept(self):
        self.assertEqual(len(self.store.raw()), 2)


class RedactTest(unittest.TestCase):
    def test_redacts(self):
        doc = export([span("a1", "", "voice.call", 0, 1, {"voice.user.text": "my key is sk-abcdefghijklmnopqrstuvwx ok",
                                                        "http.request.header.authorization": "x", "note": "tok3n-secret-value",
                                                        "gen_ai.usage.input_tokens": 812, "auth_token": "abc"})])
        a = _otel.attrs(_otel.redact(doc, extra=("tok3n-secret-value",))["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["attributes"])
        self.assertEqual(a["voice.user.text"], "my key is <redacted> ok")
        self.assertEqual(a["http.request.header.authorization"], "<redacted>")
        self.assertEqual(a["note"], "<redacted>")
        self.assertEqual((a["gen_ai.usage.input_tokens"], a["auth_token"]), (812, "<redacted>"))


class ForwardTest(unittest.TestCase):
    def test_only_local_or_tailnet(self):
        for url, ok in [("http://127.0.0.1:3000/x", True), ("http://localhost:4318/v1/traces", True),
                        ("http://100.101.1.2:4318", True), ("https://mac.tail1234.ts.net/x", True),
                        ("https://cloud.langfuse.com/api/public/otel", False), ("http://192.168.1.4:4318", False)]:
            self.assertEqual(telemetry_forward.allowed(url), ok, url)


if __name__ == "__main__":
    unittest.main()
