"""UI adapter "turn-checks": Smart Turn's end-of-turn checks from voice calls, kept on this Mac.

POST /ui/turn-checks with one check -> ~/.local/share/threadsd/turn-checks/<name>.wav, plus one
  line in index.jsonl there (everything but the audio, with the client).
  {name, wav_base64, trace_id, n, turn_check, stage: "shadow"|"decision", p_complete?, partial,
   decision, silence_ms, dangling, model_ms, audio_ms, resumed, at, build}
  The Threads iOS app sends these after a call, only with Settings → Voice → "Wait when I pause
  mid-sentence" on, and deletes each from the phone once stored here. A WAV over 2 MB is
  refused; resending a name replaces its audio and adds a new index line (the newest wins).
GET /ui/turn-checks -> {"count": N, "dir": path, "recent": [the newest 50 index lines]}.
Nothing is sent anywhere else."""
import base64
import binascii
import json
import os
import re

from . import UIS, UI, register_ui  # noqa: F401
from ._common import DATA

DIR = os.path.join(DATA, "turn-checks")
MAX_WAV = 2 << 20


@register_ui
class TurnChecks(UI):
    name = "turn-checks"

    def _index(self):
        try:
            with open(os.path.join(DIR, "index.jsonl")) as fh:
                return [json.loads(line) for line in fh if line.strip()]
        except (OSError, ValueError):
            return []

    def render(self, snap):
        rows = self._index()
        return {"count": len(rows), "dir": DIR, "recent": rows[-50:][::-1]}

    def handle(self, state, body, client, send):
        name = body.get("name") or ""
        if not re.fullmatch(r"[\w.-]{1,160}", name) or name.startswith("."):
            return 400, {"error": "name must be letters, digits, '.', '_' or '-'"}
        try:
            wav = base64.b64decode(body.get("wav_base64") or "", validate=True)
        except (binascii.Error, ValueError):
            return 400, {"error": "wav_base64 isn't base64"}
        if not wav.startswith(b"RIFF") or wav[8:12] != b"WAVE":
            return 400, {"error": "not a WAV"}
        if len(wav) > MAX_WAV:
            return 413, {"error": "WAV too large"}
        os.makedirs(DIR, exist_ok=True)
        with open(os.path.join(DIR, name + ".wav"), "wb") as fh:
            fh.write(wav)
        meta = {k: v for k, v in body.items() if k != "wav_base64"}
        meta["wav"] = name + ".wav"
        meta["client"] = client
        with open(os.path.join(DIR, "index.jsonl"), "a") as fh:
            fh.write(json.dumps(meta, ensure_ascii=False) + "\n")
        return 200, {"stored": name + ".wav"}
