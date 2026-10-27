"""cmux CLI primitives: read a screen, type literally, press keys, parse input boxes."""
import os
import re
import time

from ._common import CONF, run

CMUX = next((p for p in ["/Applications/cmux.app/Contents/Resources/bin/cmux"] if os.path.exists(p)), "cmux")
# cmux's default socket mode ("cmuxOnly") admits only processes started inside cmux. A launchd
# agent isn't, so it needs cmux in "password" mode with this file holding the password.
CMUX_PASSWORD_PATH = os.path.join(CONF, "cmux-password")


def _env():
    try:
        with open(CMUX_PASSWORD_PATH) as f:
            pw = f.read().strip()
    except OSError:
        return None
    return dict(os.environ, CMUX_SOCKET_PASSWORD=pw) if pw else None


def cmux(*args, timeout=10):
    return run([CMUX, *args], timeout=timeout, env=_env())


def read_screen(surface, ws, lines=60):
    rc, out, _ = cmux("read-screen", "--surface", surface, "--workspace", ws, "--lines", str(lines))
    return out if rc == 0 else None


def key(surface, ws, k):
    cmux("send-key", "--surface", surface, "--workspace", ws, k)


def send_raw(surface, ws, s):
    cmux("send", "--surface", surface, "--workspace", ws, "--", s)


def type_text(surface, ws, text):
    """Type text literally. cmux send turns \\n, \\r, \\t into keys, so split after every
    backslash (an escape never spans two sends) and use shift+enter for newlines."""
    for li, line in enumerate(text.split("\n")):
        if li:
            key(surface, ws, "shift+enter")
        for chunk in re.findall(r"[^\\]*\\|[^\\]+", line.replace("\t", "  ")):
            send_raw(surface, ws, chunk)


def status_line(screen):
    lines = [l for l in (screen or "").rstrip().split("\n") if l.strip()]
    return lines[-1] if lines else ""


def is_border(line):
    return line.startswith("─")


def _box_start(lines):
    for i in range(len(lines) - 1, 0, -1):
        if lines[i].startswith("❯") and is_border(lines[i - 1]):
            return i
    return None


def parse_box(screen):
    """Text in Claude's input box (the ❯ line between two ─ borders), or None if no box."""
    lines = (screen or "").split("\n")
    i = _box_start(lines)
    if i is None:
        return None
    out = []
    for l in lines[i:]:
        if is_border(l):
            break
        out.append(l)
    out[0] = out[0][1:]
    return "\n".join(out)


def above_box(screen):
    lines = (screen or "").split("\n")
    i = _box_start(lines)
    return "\n".join(lines[: i - 1]) if i else (screen or "")


SPINNER = re.compile(r"^\s*[✻✳✶✢✽·*⏺●◐◓◑◒]\s+\S.*…", re.M)


def spinner_on(screen):
    a = above_box(screen)
    return "esc to interrupt" in a or bool(SPINNER.search("\n".join(a.split("\n")[-6:])))
