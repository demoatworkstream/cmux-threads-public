"""OpenTelemetry helpers shared by the core and the telemetry plugins: read OTLP/HTTP JSON
(ExportTraceServiceRequest) and redact secrets before anything is stored or forwarded."""
import re

# Keys whose values are never kept, and values that look like credentials anywhere in a string.
SECRET_KEY = re.compile(r"(?i)(token(?!s)|authorization|password|passwd|secret|api[_-]?key|cookie)")
SECRET_VALUE = re.compile(r"(sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
                          r"xox[abprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|"
                          r"(?i:bearer|basic)\s+[A-Za-z0-9._~+/=-]{16,}|"
                          r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----)")
REDACTED = "<redacted>"


def redact_text(s, extra=()):
    s = SECRET_VALUE.sub(REDACTED, s)
    for x in extra:
        if x and len(x) >= 8:
            s = s.replace(x, REDACTED)
    return s


def redact(doc, extra=()):
    """A copy of an OTLP JSON document with secret-looking attribute values replaced. Ids,
    times and structure are untouched. `extra` are literal strings to hide (threadsd's token)."""
    def walk(v, key=""):
        if isinstance(v, dict):
            if "key" in v and "value" in v and SECRET_KEY.search(str(v["key"])):
                return {"key": v["key"], "value": {"stringValue": REDACTED}}
            return {k: walk(x, k) for k, x in v.items()}
        if isinstance(v, list):
            return [walk(x, key) for x in v]
        if isinstance(v, str) and key in ("stringValue", "name", "message"):
            return redact_text(v, extra)
        return v
    return walk(doc)


def value(v):
    """An OTLP AnyValue as plain Python."""
    if not isinstance(v, dict):
        return v
    for k in ("stringValue", "boolValue", "doubleValue"):
        if k in v:
            return v[k]
    if "intValue" in v:
        try:
            return int(v["intValue"])
        except (TypeError, ValueError):
            return v["intValue"]
    if "arrayValue" in v:
        return [value(x) for x in v["arrayValue"].get("values", [])]
    if "kvlistValue" in v:
        return attrs(v["kvlistValue"].get("values", []))
    if "bytesValue" in v:
        return v["bytesValue"]
    return None


def attrs(kvs):
    return {kv.get("key"): value(kv.get("value")) for kv in kvs or [] if isinstance(kv, dict)}


def spans(doc):
    """Flatten an ExportTraceServiceRequest: one dict per span, with its resource and scope."""
    out = []
    for rs in doc.get("resourceSpans") or []:
        res = attrs((rs.get("resource") or {}).get("attributes"))
        for ss in rs.get("scopeSpans") or []:
            scope = (ss.get("scope") or {}).get("name", "")
            for s in ss.get("spans") or []:
                if not s.get("traceId") or not s.get("spanId"):
                    continue
                out.append({
                    "trace_id": s["traceId"].lower(), "span_id": s["spanId"].lower(),
                    "parent_id": (s.get("parentSpanId") or "").lower() or None,
                    "name": s.get("name", ""), "kind": s.get("kind", 0),
                    "start_ns": int(s.get("startTimeUnixNano") or 0), "end_ns": int(s.get("endTimeUnixNano") or 0),
                    "status": (s.get("status") or {}).get("code", 0), "status_message": (s.get("status") or {}).get("message"),
                    "attrs": attrs(s.get("attributes")),
                    "events": [{"name": e.get("name"), "time_ns": int(e.get("timeUnixNano") or 0), "attrs": attrs(e.get("attributes"))}
                               for e in s.get("events") or []],
                    "resource": res, "scope": scope,
                })
    return out
