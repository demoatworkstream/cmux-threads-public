"""Artifacts source: serves the Artifact Shelf index (claude.ai/artifact pages made in
Claude Code) for the iPhone Artifacts tab, via GET/POST /artifacts.

One data source: the Mac Shelf's SQLite index. We don't re-parse transcripts here; we
shell out to the `artifact_shelf` CLI (the same Swift indexer the Mac app uses), so the
list, pins and schema stay identical across the Mac app and the phone. Pins written here
show up in the Mac Shelf. `thread` is the one field the CLI can't fill — we add it by
matching each artifact's Claude session id to a live threadsd session.

Served by threadsd.py:
  GET  /artifacts?q=&project=&account=&pinned=1&limit=200
  GET  /artifacts/{id}
  POST /artifacts/{id}/pin   {"pinned": true|false}
and an SSE `artifact` event when the index changes (watch_index()).
"""
import json
import os
import re
import subprocess
import time

from ._common import log

# An artifact id is a claude.ai URL key: URL-safe base64-ish, never option-like.
# Reject anything else so a crafted id (e.g. "-x") can't reach the CLI as a flag.
_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")

_CLI_CANDIDATES = [
    os.environ.get("ARTIFACT_SHELF_CLI", ""),
    os.path.expanduser("~/.local/bin/artifact_shelf"),
    os.path.expanduser("~/Applications/Artifact Shelf.app/Contents/MacOS/artifact-open"),
    "/Applications/Artifact Shelf.app/Contents/MacOS/artifact-open",
]
_INDEX = os.path.expanduser("~/Library/Application Support/Artifact Shelf/index.sqlite")


def cli_path():
    for p in _CLI_CANDIDATES:
        if p and os.path.exists(p):
            return p
    return None


def available():
    return cli_path() is not None


def _run(args, timeout=20):
    """Run the CLI and parse its JSON stdout. Returns (ok, value_or_error)."""
    cli = cli_path()
    if not cli:
        return False, "artifact_shelf CLI not found (install Artifact Shelf)"
    try:
        p = subprocess.run([cli, *args], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "artifact_shelf timed out"
    except Exception as e:  # noqa: BLE001
        return False, f"artifact_shelf failed: {e}"
    if p.returncode != 0:
        return False, (p.stderr or p.stdout or "artifact_shelf error").strip()[:300]
    try:
        return True, json.loads(p.stdout or "null")
    except json.JSONDecodeError:
        return False, "artifact_shelf returned non-JSON"


def _thread_for(artifact, sessions_by_id):
    """{id, name} of the threadsd session this artifact was made in, or None.

    The reliable key is the Claude Code session id: the Shelf records it per artifact, and
    a Claude thread's threadsd id IS that session id. Workspace ids don't line up (the Shelf
    stores a cmux UUID, threadsd a `workspace:N` ref), so we don't match on those.
    """
    sid = artifact.get("session_id")
    s = sessions_by_id.get(sid) if sid else None
    if not s:
        return None
    return {"id": s["id"], "name": s.get("name") or s.get("title") or sid}


def _enrich(payload, sessions):
    by_id = {s["id"]: s for s in sessions}
    for a in payload.get("artifacts", [payload] if "id" in payload else []):
        a["thread"] = _thread_for(a, by_id)
    if "id" in payload and "artifacts" not in payload:   # a single artifact (detail/pin)
        payload["thread"] = _thread_for(payload, by_id)
    return payload


def list_artifacts(query, sessions):
    """GET /artifacts. `query` is the parsed query string (dict of lists)."""
    args = ["artifacts"]
    for key, flag in (("q", "--q"), ("project", "--project"), ("account", "--account"), ("limit", "--limit")):
        v = (query.get(key) or [None])[0]
        if v:
            args += [flag, v]
    if (query.get("pinned") or [None])[0] in ("1", "true", "yes"):
        args.append("--pinned")
    ok, val = _run(args)
    if not ok:
        return 503, {"error": val}
    return 200, _enrich(val, sessions)


def get_artifact(aid, sessions):
    if not _ID_RE.match(aid):
        return 404, {"error": "bad id"}
    ok, val = _run(["artifact", "--", aid])
    if not ok:
        return 404, {"error": val}
    return 200, _enrich(val, sessions)


def pin_artifact(aid, pinned, sessions):
    if not _ID_RE.match(aid):
        return 404, {"error": "bad id"}
    ok, val = _run(["pin" if pinned else "unpin", "--", aid])
    if not ok:
        return 404, {"error": val}
    return 200, _enrich(val, sessions)


def index_mtime():
    try:
        return os.path.getmtime(_INDEX)
    except OSError:
        return 0.0


def watch_index(hub, stop, interval=8.0):
    """Keep the index fresh and emit an SSE `artifact` event when it changes.

    Runs the CLI's incremental scan (cheap; only changed transcripts), then publishes if the
    index file moved. Meant to run in a daemon thread. Safe if the CLI is missing (no-op).
    """
    last = index_mtime()
    while not stop.is_set():
        stop.wait(interval)
        if stop.is_set():
            break
        if available():
            _run(["scan"], timeout=60)
        m = index_mtime()
        if m and m != last:
            last = m
            try:
                hub.publish("artifact", {"change": "index", "at": time.time()})
            except Exception as e:  # noqa: BLE001
                log("artifact watch publish failed:", str(e)[:120])
