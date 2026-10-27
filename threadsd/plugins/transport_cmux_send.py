"""Transport "cmux-send": type into a local cmux surface, using the row's agent adapter
for the submit protocol (Claude: INSERT check + enter; Codex: enter, never escape)."""
import threading

from . import AGENTS, Transport, register_transport

LOCK = threading.Lock()  # one delivery at a time: keystrokes from two sends must never interleave


@register_transport
class CmuxSend(Transport):
    name = "cmux-send"

    def deliver(self, state, row, text):
        if not row.get("surface"):
            return False, "session has no cmux surface"
        agent = AGENTS.get(row.get("agent"))
        if not agent or not agent.capabilities.get("submit"):
            return False, f"agent {row.get('agent')!r} has no submit adapter"
        with LOCK:
            ok, detail = agent.submit(row, text)
        if ok:
            agent.note_delivered(row, text)
        return ok, detail
