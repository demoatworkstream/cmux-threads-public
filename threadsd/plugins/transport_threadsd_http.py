"""Transport "threadsd-http": forward a send to the threadsd on the machine that owns the
session (sessions merged in by the remote-machine source). That daemon does the typing."""
import json
import urllib.request

from . import Transport, register_transport


@register_transport
class ThreadsdHttp(Transport):
    name = "threadsd-http"

    def deliver(self, state, row, text):
        peer = row.get("_peer") or {}
        if not peer.get("url"):
            return False, "no peer for this machine"
        body = json.dumps({"target": row["id"], "text": text}).encode()
        req = urllib.request.Request(peer["url"].rstrip("/") + "/send", data=body, method="POST",
                                     headers={"Authorization": f"Bearer {peer['token']}",
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                resp = json.load(r)
        except urllib.error.HTTPError as e:
            try:
                resp = json.load(e)
            except Exception:
                return False, f"peer HTTP {e.code}"
        except Exception as e:
            return False, f"peer unreachable: {e!r}"[:200]
        return resp.get("status") == "delivered", f"{row.get('machine')}: {resp.get('detail')}"
