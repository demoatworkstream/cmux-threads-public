"""threadsd plugin registries.

Three kinds of plugin, each a small class registered by name. The core (threadsd.py)
only iterates these registries; it never branches on an agent, source or transport name.

  AGENTS      agent adapters: discover sessions, status, submit, interrupt, launch, capabilities
  SOURCES     status sources: feed shared facts into the per-tick context (hooks, cmux, ...)
  TRANSPORTS  how text reaches a session (cmux-send, SendMessage-bridge, threadsd-http)
  UIS         views of the schema in a UI's own format (GET /ui/<name>)
  BRAINS      voice brains a phone can call (GET /voice/brains), e.g. the models served on this Mac
  TELEMETRY   where OpenTelemetry traces go (POST /v1/traces): the local call store, forwarders
  PARSERS     turn a needs-you message into questions with options (Question.options in /state)

To add one, drop a module in this package that defines the class with @register_*;
load_all() imports every module here. See EXTENDING.md in the skill.
"""
import importlib
import os
import pkgutil

AGENTS, SOURCES, TRANSPORTS, UIS, BRAINS, TELEMETRY, PARSERS = {}, {}, {}, {}, {}, {}, {}


def register_agent(cls):
    AGENTS[cls.name] = cls()
    return cls


def register_source(cls):
    SOURCES[cls.name] = cls()
    return cls


def register_transport(cls):
    TRANSPORTS[cls.name] = cls()
    return cls


def register_ui(cls):
    UIS[cls.name] = cls()
    return cls


def register_brain(cls):
    BRAINS[cls.name] = cls()
    return cls


class Agent:
    """Interface of an agent adapter."""
    name = ""
    label = ""
    process_names = ()  # executable basenames that identify this agent on a terminal's tty
    capabilities = {"submit": False, "interrupt": False, "remote_control": False,
                    "attachments": False, "usage_limit_retry": False, "prompts": False}
    # submit() details that mean "the input box is hidden for now" (a dialog is open, the
    # session is starting): the core queues the send and retries instead of failing it.
    retry_details = ("no Claude input box on screen", "no Codex composer on screen")
    launch = ""  # launch command template (documentation; roots run it)
    naming = ""
    status_doc = ""
    submit_doc = ""
    interrupt_doc = ""
    usage_limit_doc = ""

    def discover(self, ctx):
        """Return raw rows (dicts) for this agent's live sessions. Claim surfaces in ctx.claimed."""
        return []

    def refresh(self, ctx, row):
        """Fill row["last_message"], row["working"], etc. Called every tick; cache by file mtime."""

    def messages(self, row):
        """Chat history: [{"role", "text", "ts"}], tool rows skipped."""
        return []

    def submit(self, transport_ctx, row, text):
        """Deliver text into the session's input box and submit it. Returns (ok, detail)."""
        return False, "submit not supported"

    def interrupt(self, row):
        return False, "interrupt not supported"

    def note_delivered(self, row, text):
        """Called after a successful submit. Adapters whose status lags can show working at once."""

    def answer_prompt(self, row, prompt, answers):
        """Answer row["prompt"] (only when capabilities["prompts"]). Returns (ok, detail)."""
        return False, "prompts not supported"

    def describe(self):
        return {"label": self.label, "capabilities": self.capabilities, "launch": self.launch,
                "naming": self.naming, "status": self.status_doc, "submit": self.submit_doc,
                "interrupt": self.interrupt_doc, "usage_limit": self.usage_limit_doc}


class Source:
    """Interface of a status source. poll() runs every `interval` seconds and writes into ctx."""
    name = ""
    interval = 1.0

    def poll(self, ctx):
        pass


class Transport:
    """Interface of a transport: how text reaches a session row."""
    name = ""

    def deliver(self, state, row, text):
        return False, "not implemented"


class UI:
    """Interface of a UI adapter: render(snapshot) -> that UI's own document."""
    name = ""

    def render(self, snap):
        return snap

    def handle(self, state, body, client, send):
        """POST /ui/<name>: map the UI's own action document onto the API. send(body) is /send."""
        return 404, {"error": "this UI takes no actions"}


class Brain:
    """Interface of a voice-brain provider: list() -> the brains it knows, health checked."""
    name = ""

    def list(self):
        """[{id, name, url, model, api, up, latency_ms, last_checked, error?}], best first."""
        return []


def register_telemetry(cls):
    TELEMETRY[cls.name] = cls()
    return cls


def call_store():
    """The telemetry sink that answers reads (GET /voice/calls...), or None."""
    return next((t for t in TELEMETRY.values() if t.reads), None)


class Telemetry:
    """Interface of a telemetry sink. ingest() gets every OTLP/HTTP JSON trace export, already
    redacted. A sink with reads = True also serves the call history (only one is used)."""
    name = ""
    reads = False

    def ingest(self, doc, meta):
        """doc: an ExportTraceServiceRequest (OTLP JSON). meta: {client, received_at, snapshot()}."""

    def calls(self, limit=50):
        return []

    def call(self, trace_id):
        return None

    def rate(self, trace_id, rating, note):
        return False

    def export(self, rating, fmt):
        return []


def register_parser(cls):
    PARSERS[cls.name] = cls()
    return cls


class Parser:
    """Interface of a question parser. parse(body) gets the needs-you block of a message and
    returns [{text, options: [{n, label, detail}], multi, allow_other, parse}] or [] to pass.
    Parsers run by ascending priority; the first non-empty result wins."""
    name = ""
    priority = 50

    def parse(self, body):
        return []


def load_all():
    here = os.path.dirname(__file__)
    for m in sorted(pkgutil.iter_modules([here]), key=lambda m: m.name):
        if not m.name.startswith("_"):
            importlib.import_module(f"{__name__}.{m.name}")
