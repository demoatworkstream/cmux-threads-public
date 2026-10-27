"""Transport "SendMessage-bridge": for a session reachable only through Remote Control
(a remote root on a machine without threadsd). threadsd can't call SendMessage itself, so it
asks the local ◎ Master to forward the text word for word, delivered with cmux-send."""
from . import TRANSPORTS, Transport, register_transport


@register_transport
class SendMessageBridge(Transport):
    name = "sendmessage-bridge"

    def deliver(self, state, row, text):
        master = state.get(state.master_id) if state.master_id else None
        if not master:
            return False, "no ◎ Master session to bridge through"
        ask = (f'From threadsd (the user, from the phone): forward this word for word with SendMessage to '
               f'"{row["name"]}", then confirm the send:\n{text}')
        ok, detail = TRANSPORTS["cmux-send"].deliver(state, master, ask)
        return ok, ("handed to the ◎ Master for SendMessage: " + detail) if ok else detail
