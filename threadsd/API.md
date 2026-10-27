# threadsd API (schema_version 1)

The one API every UI uses (Threads iOS app, roots and the ◎ Master via curl).
This file is canonical; the supervisor's scratchpad copy mirrors it.

**Versioning.** Every response and every SSE event carries `"schema_version": 1`. Within a version, fields are only ever added, never renamed or removed. Clients must ignore fields they don't know.
**UIs key their controls on `session.capabilities`, never on `session.agent`.** `agent` and `agent_label` are labels only (for a badge). A new agent type then needs no UI change.

## Transport and auth

- Base: `http://<this Mac's Tailscale IP>:7878`. threadsd binds **only** to the Tailscale address (100.64.0.0/10 on the `utun` interface), never 0.0.0.0 or localhost. With no Tailscale address it doesn't listen, and it retries every 15 s.
- Every request needs `Authorization: Bearer <token>`. The token is in `~/.config/threadsd/token` (0600), generated on first run. SSE takes the same header.
- Every `POST /send` and `POST /interrupt` (and every Remote Control recovery) is appended to `~/.local/share/threadsd/sends.jsonl`: time, send_id, client (IP + User-Agent), target, session_id, surface, agent, machine, text, delivered_text, attachments, status/result, detail.
- Errors: `401 {"error":"unauthorized"}`, `404 {"error":"not found"}`, `400 {"error":"bad json"}`, `413` for bodies over 60 MB.

## GET /state -> 200

```json
{
  "schema_version": 1,
  "machine": "MacBook-Pro-80",
  "generated_at": "2026-10-25T18:00:00Z",
  "master": { "session_id": "…", "state": "working|idle|needs_you|finished|failed" },
  "sessions": [ Session, … ],
  "agents": { "claude": { "label": "Claude", "capabilities": {…} }, "codex": { "label": "Codex", "capabilities": {…} } }
}
```

`master` is null when no ◎ Master session is running.

```json
Session = {
  "id": "…",                    // see "Session ids" below
  "name": "aka · data-layer",   // Claude: session name (-n). Others: tab title without its mark
  "title": "Data layer (Mireye)",  // tab title without its mark (the topic); falls back to name
  "role": "master|root|thread", // ◎ = master, ⌂ Root = root, others = thread
  "project": "aka",             // master: "master"; thread: its root's project
  "machine": "MacBook-Pro-80",
  "agent": "claude|codex|unknown|<other>",   // label only
  "agent_label": "Claude",                   // label only, for the badge
  "capabilities": { "submit": true, "interrupt": true, "remote_control": true,
                    "attachments": true, "usage_limit_retry": false, "prompts": true },
  "remote_control": true,       // Remote Control is on right now (Claude: bridge session present)
  "transport": "cmux-send|threadsd-http|sendmessage-bridge",
  "cwd": "/Users/…", "surface": "surface:108", "workspace": "workspace:23",   // null when not in cmux
  "mark": "▶|?|✓|○|✗",          // the tab's mark; derived from state when the tab has none
  "state": "working|needs_you|idle|finished|failed",
  "last_message": "first 400 chars of the last assistant message",
  "last_message_at": "2026-10-25T17:58:02.120Z",   // when it was written (transcript time); null if the user spoke last
  "last_message_id": "<message id>",               // same ids as /sessions/{id}/messages
  "last_status": "Duplicate index line; removing one.",  // the last message's first meaningful line when it is NOT the
                                                         // question message (a later bookkeeping report); else null. Secondary text only.
  "questions": [ { "id": "<session id>:<n>:<hash>", "n": 1, "text": "Which sources should I add?",  // the question only, no options
                   "options": [ { "n": 1, "label": "Apple Notes", "detail": "macOS asks once" } ],  // [] = no choices: a plain answer box
                   "multi": true,            // several options may be picked (single when false)
                   "allow_other": true,      // the user may also answer in their own words
                   "parse": "marked|heuristic|null",  // marked = "(pick one)"/"(pick any)" convention; heuristic = guessed; null = no options
                   "source_message_id": "<message id>",     // same as message_id
                   "asked_at": "2026-10-25T17:40:11.000Z",   // time of the message it came from (transcript, not poll time)
                   "message_id": "<message id>",            // link to that message in /sessions/{id}/messages
                   "stale": false,                          // true once the user wrote to the thread (or it started working) after asked_at
                   "status": "open|answered|skipped" } ],
  "attention": "open|answered|skipped|null",  // the thread in Needs you: "open" = show it; null = nothing pending
  "prompt": null | Prompt,      // an open dialog that replaces the input box (see "Prompts")
  "root_id": "<id of its root session, or null>",
  "updated_at": "2026-10-25T18:00:00Z"   // the session's real last activity (transcript write, hook event, Codex rollout)
}
```

- **state**, in order: `working` if the agent is live-busy; `needs_you` on a pending permission prompt; `failed` on a usage limit (last_message `"usage limit, retry at <time>"`); else the last `STATUS:` line of the last message (`needs you` / `finished` / `failed`); else the tab mark (`?`, `✓`, `✗`); else `idle`. Note that `mark` can lag `state`: roots set marks when they look, while state is live.
- **questions**: from the latest assistant message whose last STATUS line is `needs you` and that actually asks something; `**` markup removed. A question parser plugin (`PARSERS`, `plugins/parser_numbered.py`) splits that block into questions and their options:
  - **Marked** (the skill's convention): a question line ending in `(pick one)` / `(pick any)` with numbered options under it, `**Label:** detail` each. `(pick one only)` / `(pick any only)` sets `allow_other: false`.
  - **Heuristic** for unmarked messages: a numbered list right under a paragraph that asks (`?`, or reply/pick/choose) is that question's options, unless its items are questions themselves. In the last numbered list, each item is a question and a numbered list nested under it is its options. A line ending in `:` asks only with a choice word (reply, pick, choose, options, one of, either, prefer), so a list of steps under "Then make one call:" isn't options; a last list whose items ask nothing becomes one question that keeps the steps. A short choice line ("Reply with a number:") gets the nearest bold heading in front of it. An option that is itself a question stays its own question; "(reply 2)" is dropped from labels. `multi` is guessed from "numbers", "any of", "e.g. "1, 3"" and the like.
  - Otherwise the last paragraph containing a `?` is the one question.
  One question with options is always ONE item. One-line bookkeeping reports ("STATUS: needs you — report saved") never replace it: they show up as `last_status`. A later `STATUS: finished`/`failed` closes the block. Questions stay listed, possibly `stale`, until they're answered or skipped, or the block is closed. `id` is stable while the question text is unchanged.
- **attention** drives Needs you: `open` while any question is `open` (or, for a `needs_you` thread with no parsed question, until it's skipped). Skipped state is stored in threadsd, so every device and the console see the same list. A skipped item comes back only when the thread asks a new question (new question ids).
- **Session ids.** Claude: the Claude session id. Codex: the Codex thread id (from its rollout file) once Codex has written one, before that `codex:<surface>`; when it switches you get `removed` for the old id and `session` for the new one. Any other tab with a status mark: `cmux:<workspace>:<surface>` (agent `unknown`, capabilities all false, read-only). Remote Control-only remote roots: `rc:<session name>`.
- Sessions from other machines (phase 3) appear with their own `machine` and `transport: "threadsd-http"`.

## GET /events -> text/event-stream

```
event: session    data: Session + schema_version          (on connect: one per current session; then whenever any field changes)
event: removed    data: {"id", "schema_version"}
event: message    data: {"session_id", "id", "role": "user|assistant", "text", "ts", "system", "origin", "schema_version"}   (◎ Master chat, new turns)
event: send       data: {"send_id", "status": "delivered|queued|failed|skipped|unskipped", "detail", "schema_version"}   (queued sends: one event per change)
event: prompt_answer  data: {"session_id", "prompt_id", "status": "answered|failed", "detail", "schema_version"}
event: artifact   data: {"change": "index", "at": <epoch>, "schema_version"}   (an artifact was added, re-published or pinned; re-fetch /artifacts)
```

A `: heartbeat` comment every 15 s, whether or not events flow. Reconnect on drop; the connect burst resyncs everything.

## GET /sessions/{id}/messages?limit=50&include_system=0 -> 200

`{"messages": [{"id", "role": "user|assistant", "text", "ts", "system": false, "origin": "user|session|agent|system"}], "schema_version": 1}`.
`system` rows are turns started automatically (a scheduled/cron prompt, and every reply in that turn) and compaction summaries. They're left out unless `include_system=1`. `origin: "session"` marks a message from another Claude session (a supervisor); those don't make questions stale. `message` SSE events carry the same `id`, `system` and `origin`. `id` may be `master`. Tool rows are skipped; consecutive assistant texts in one turn are merged; messages typed while the session was busy are included. Works for any agent with a log (Claude transcript, Codex rollout). 404 for unknown or remote sessions. limit is 1–500.

## POST /send -> 200 / 202 / 4xx

```json
{ "target": "<session id>" | "master", "text": "…", "question_id": "<optional>",
  "kind": "answer" (default) | "skip" | "skip_all" | "unskip" | "unskip_all",
  "choices": [1, 3], "other": "…",   // optional, kind answer: composed into text when text is empty
  "attachments": [ { "name": "photo.jpg", "mime": "image/jpeg", "data_base64": "…" } ] }
```

- Attachments are saved to `~/.local/share/threadsd/uploads/<send_id>/` and appended to the text as `Attached: <absolute path>` lines.
- With `question_id`, the text is prefixed `Answer to question <n> ("<question text>"): `.
- **Quick-select answers.** Send the composed reply as `text`: `"1, 3"` or `"1, 3 — plus: <typed text>"`. Or leave `text` empty and send `"choices": [1, 3]` and/or `"other": "…"`: threadsd composes the same string. When the reply starts with option numbers of that question, their labels are added, so the thread reads `Answer to question 1 ("Which sources should I add?"): 1, 3 (Apple Notes; Gmail) — plus: …`.
- Text over 3000 characters is saved as `uploads/<send_id>/message.md` and delivered as one line: its first line plus "read <path> for the full text".
- Delivery goes through the session's transport and the agent adapter's submit:
  - **claude** (cmux-send): make sure the box is in vim `-- INSERT --` mode (otherwise press `A` and confirm; typing into NORMAL mode runs the text as vim commands, e.g. "From …" arrives as "m …"); type; check the box holds exactly the text; `enter`; confirm the box emptied and the text was taken. Only if the text is still there and the session is idle: `escape`, ~1.5 s, `enter`. Never escape into a busy session.
  - **codex** (cmux-send): type, `enter`, confirm `Working`. Never `escape` (it interrupts a task).
  - **threadsd-http**: forwarded to the owning machine's threadsd.
  - **sendmessage-bridge**: handed to the local ◎ Master to forward with SendMessage.
- The box must be empty first (Claude's greyed-out suggestions count as empty). Otherwise exactly the typed text is removed again and the send fails with detail `"input box not empty"`.
- **kinds.** `answer` is the default (text, optionally with `question_id`; marks that question `answered` once delivered or queued). **`skip` means ignore/clear: nothing is ever sent to the thread.** `skip` + `question_id` clears one question, and `skip_all` clears the whole thread (all its questions). Both are stored in threadsd (response status `skipped`, detail "cleared from Needs you; nothing was sent to the thread") and show at once in /state. A skipped item comes back only when the thread asks a new question. `unskip` / `unskip_all` undo it (status `unskipped`). `hide*` / `unhide*` are accepted as aliases of `skip*` / `unskip*`.
- **Queued, never failed for a hidden box.** If the session has an open prompt (or queued sends ahead), or its input box is hidden, the send is held: HTTP 202 `{"status": "queued", "detail": "answer the question above first"}`. threadsd delivers it automatically once the box is back (oldest first, retrying on its own; the queue survives restarts), and publishes `event: send` with the same `send_id`: `queued` → `delivered` or `failed`. It fails only after 6 hours or if the session is gone.
- Response: `{"send_id", "status": "delivered|queued|failed|skipped|unskipped", "detail", "schema_version"}`. HTTP 200 when delivered (or skipped/unskipped), 202 when queued, 409 when delivery failed, 404 for an unknown target or question, 400 for bad input. Every status change is also published as an `event: send`.

## Prompts (dialogs that replace the input box)

When a session shows a menu instead of its input box (AskUserQuestion, a permission prompt, a confirm), `session.prompt` is set and `state` is `needs_you`:

```json
Prompt = { "id": "<tool_use id> | screen:<hash>", "kind": "ask_user_question|permission|confirm",
           "questions": [ { "text", "header", "multi_select": false,
                            "options": [ { "label": "Blue", "description": "…" } ] } ],
           "allows_other": true,          // a free-text "Other" answer is possible (AskUserQuestion)
           "opened_at": "…", "asked_at": "…", "message_id": "<message id or null>",
           "source": "hooks|transcript|screen" }
```

Sources, best first: the PreToolUse/PostToolUse hooks (when installed), the transcript (an AskUserQuestion call with no result yet, while the session file says it's waiting), the screen (permission/confirm menus). Only agents with `capabilities.prompts` report and answer prompts. While a prompt is open, `/send` queues (above).

### POST /prompt/answer -> 200 / 4xx

```json
{ "session": "<session id>" | "master", "prompt_id": "<prompt.id>",
  "answers": [ { "choices": ["Green"] }, { "choices": ["Banana", "Cherry"], "other_text": "Kiwi" } ] }
```

One answer per question, in order. Shorthand for one question: top-level `"choices": [...]` and/or `"other_text": "…"`. Choices are option labels. Single-select takes exactly one choice or an `other_text`; multi-select takes any number plus an optional `other_text`. threadsd presses the keys (arrows to each option by label, enter, enter to toggle in multi-select, "Type something" for Other, Submit, then the review screen's "Submit answers") and answers only after it saw the dialog close. Response `{"status": "answered|failed", "detail"}`; 409 `"prompt changed; reload /state"` if `prompt_id` is no longer the open one, 400 for an invalid answer. Also published as `event: prompt_answer` `{session_id, prompt_id, status, detail}`. Queued sends follow automatically.

## POST /interrupt -> 200 / 409

`{"target": "<session id>" | "master"}`. Only when `capabilities.interrupt`. Stops the current turn (Claude: escape, twice from INSERT mode; Codex: escape while Working). Claude puts the interrupted prompt back into the input box; threadsd clears it (only when it is exactly that prompt), so the next /send works. Fails with `"not working; nothing to interrupt"` when idle. Logged like a send.

## GET /agents -> 200

Each adapter's `label`, `capabilities`, `launch` command, `naming`, `status` source, `submit` and `interrupt` protocol, and `usage_limit` handling, plus the registered `sources`, `transports`, `uis` and `telemetry` sinks.

## GET /voice/brains -> 200

The voice brains a phone call can think with, from the `BRAINS` plugin registry (each provider lists its own; `brain_mac` reads `~/.config/threadsd/brains.json`). Same token and Tailscale-only rules as every route.

```json
{ "brains": [ { "id": "mac-35b", "name": "Mac · Qwen3.6 35B", "url": "http://100.x.y.z:8091",
                "model": "default_model", "api": "openai" | "ollama", "provider": "mac",
                "up": true, "latency_ms": 23, "last_checked": "2026-10-25T19:18:16Z",
                "error": "<only when down>" } ] }
```

- `url` is what the phone calls (OpenAI-compatible `/v1/chat/completions`, or Ollama's `/api/chat`), so it must be reachable over Tailscale. threadsd doesn't proxy calls.
- Health is checked by threadsd itself (`GET /v1/models`, or `/api/tags` for Ollama, 3 s timeout), at most every 15 s per brain; `up`, `latency_ms` and `last_checked` are that check. Down brains are still listed.
- Order is the providers' order, best first. An empty list is normal (no brains configured). A threadsd without this route answers 404; clients treat that as an empty list.
- `brains.json`: `[{"id", "name", "url", "model", "api"}]`; `id` and `name` default to `url`, `model` to `default_model`, `api` to `openai`.

## POST /ui/diagnostics, GET /ui/diagnostics

Crash and error reports from the apps (UI plugin `diagnostics`), stored on this Mac in `~/.local/share/threadsd/diagnostics/` (newest 500 kept; nothing is sent elsewhere).

- POST one report: `{"kind": "crash"|"metrickit"|"error", "app", "version", "build", "os", "device", "at", "text"?, "events"?, "area"?, "payload"?, "trace_id"?}` (`trace_id`: the voice call that was running, which links the report to that call in `/voice/calls`) → `200 {"stored": "<file>"}`; 400 for another kind, 413 over 4 MB. `crash` is the app's own record (stack and recent events, written as it died), `metrickit` Apple's crash report, `error` a non-fatal error with recent events.
- GET: `{"reports": [{file, kind, at, build, area, trace_id, first_line}]}`, newest first (50).

## POST /ui/turn-checks, GET /ui/turn-checks

Smart Turn's end-of-turn checks from voice calls (UI plugin `turn-checks`), for tuning and training the end-of-turn model. Stored on this Mac in `~/.local/share/threadsd/turn-checks/` (nothing is sent elsewhere). The iOS app sends them after a call, only with Settings → Voice → "Wait when I pause mid-sentence" on.

- POST one check: `{"name", "wav_base64", "trace_id", "n", "stage": "shadow"|"decision", "p_complete"?, "partial", "decision", "silence_ms", "dangling", "model_ms", "audio_ms", "resumed", "at", "build"}` → `200 {"stored": "<name>.wav"}`. The audio goes to `<name>.wav`, everything else to a line in `index.jsonl`. 400 for a name with anything but letters, digits, `.`, `_`, `-` or for audio that isn't a WAV; 413 over 2 MB.
- GET: `{"count", "dir", "recent": [the newest 50 index lines]}`.

## Voice calls (OpenTelemetry)

Every Threads voice call is one OpenTelemetry trace. The app sends it to threadsd; threadsd keeps it on this Mac. Sinks come from the `TELEMETRY` plugin registry: `calls` (the store below, the only one that answers reads) and `forward` (off unless configured). Nothing leaves the user's machines.

### POST /v1/traces -> 200

Standard OTLP/HTTP with the **JSON** encoding (`Content-Type: application/json`, optional `Content-Encoding: gzip`): an `ExportTraceServiceRequest`. Protobuf answers `415`. Same token and Tailscale-only rules as every route. Response `{"partialSuccess": {}}`.

- **Redacted once, before any sink sees it:** attributes whose key looks like a credential (`token`, `authorization`, `password`, `secret`, `api_key`, `cookie`) and values that look like one (`sk-…`, `ghp_…`, `xox…`, `Bearer …`, AWS keys, private-key blocks, threadsd's own token) become `<redacted>`.
- **`calls`** keeps every export as received (redacted, compressed) in `~/.local/share/threadsd/voice-traces.db` table `otlp`, so the history can be replayed into any OTel backend; indexes the spans; saves the `/state` snapshot when a call is first seen (for eval cases); keeps the newest 2000 calls. A span that arrives again replaces the stored one, except that an open copy never replaces an ended one.
- **`forward`** also POSTs each export to the backends in `~/.config/threadsd/telemetry.json`: `{"forward": [{"name": "langfuse", "url": "http://127.0.0.1:3000/api/public/otel/v1/traces", "headers": {"Authorization": "Basic …"}}]}`. Only localhost, `100.64.0.0/10` or `*.ts.net` URLs are accepted. Moving to self-hosted Langfuse (or Jaeger, an OTel Collector) is this file, not a code change.

**The app's spans** (names and attributes the store reads; GenAI semantic conventions where they fit):

| span | parent | attributes |
|---|---|---|
| `voice.call` | none | `voice.brain.name`, `voice.brain.provider` (on-device, mac), `gen_ai.request.model`, `voice.hands_free`, `voice.barge_in_allowed`, `voice.tts.voice`, `voice.call.ttfw_ms` (call open to first spoken word), `voice.call.turns`, `voice.call.end_reason`. Sent while still open, so a call shows up before it ends. |
| `voice.listen` | call | `voice.listen.end` (silence, manual, error); events `voice.mic_started` (`voice.asr.engine`), `voice.silence` |
| `voice.turn` | call | `voice.turn.n`, `voice.user.text`, `voice.user.input` (speech, typed), `voice.reply.text`, `voice.reply.via`, `voice.brain.route` (rules, model), `voice.ttfw_ms` (end of the user's speech to first spoken word), `voice.action.{kind,target,question,text}` (the proposed send; kind is answer, toThread, toMaster, delegate) |
| `voice.asr` | turn | `voice.asr.text`, `voice.asr.engine`, `voice.asr.confidence` (when the engine has one) |
| `voice.brain` | turn | the decision: `voice.reply.*`, `voice.brain.route`, `voice.action.*` |
| `chat <model>` | brain | `gen_ai.operation.name` chat, `gen_ai.provider.name`, `gen_ai.request.model`, `server.address`, `gen_ai.input.messages` and `gen_ai.tool.definitions` (JSON, OpenAI chat format, exactly as sent), `gen_ai.output.messages` (the reply, tool-call arguments as objects), `gen_ai.usage.input_tokens`/`output_tokens`, `gen_ai.response.finish_reasons`, `threads.trainable` |
| `execute_tool <name>` | brain or chat | `gen_ai.tool.name`, `gen_ai.tool.call.arguments`, `gen_ai.tool.call.result` |
| `voice.action.send` | brain | `voice.action.*`, `voice.action.result` (delivered, queued, failed), `voice.action.detail` |
| `voice.speak` | turn (reply) or call (greeting, news) | `voice.speak.text`, `voice.speak.kind`, `voice.tts.voice`, `voice.speak.interrupted`; events `voice.barge_in`, `voice.barge_in_heard` (`voice.level`, `voice.echo_level`) |
| `ui.<element>` | call | **What the screen showed** (no OTel convention covers UI, so these are ours): `threads.ui.element` (e.g. `confirm_card`), `threads.ui.title`, `threads.ui.text`, `threads.ui.question`, `threads.ui.actions` (button labels), `threads.ui.hint`, `threads.ui.turn` (the turn that showed it), `voice.action.*`, and when it went away `threads.ui.outcome` (`tapped Send`, `tapped Cancel`, `said: <words>`, `replaced`, `call ended`); event `ui.tap` (`threads.ui.control`). The span lasts as long as it was on screen. One-off notices are events on `voice.call`: `ui.problem`, `ui.brain_label` (`threads.ui.text`). |

Spans with `voice.transcript.role` (user, assistant, ui) and `voice.transcript.text` make the transcript; `ui` lines are what appeared on screen. Failures set span status ERROR with the message, plus an `exception` event. Resource: `service.name` threads-ios, `service.version`, `threads.app.build`, `device.model.identifier`, `os.name`, `os.version`. A crash or error report sent to `/ui/diagnostics` during a call carries that call's `trace_id`.

### GET /voice/calls?limit=50 -> 200

`{"calls": [Call]}`, newest first (limit 1–500).

```json
Call = { "trace_id": "0af7…319c", "started_at": "…Z", "ended_at": "…Z" | null, "duration_s": 72.4,
         "status": "ok|error|open|crashed",   // open: still going, or cut off (the app died); crashed: a crash report names it
         "title": "first thing the user said", "turns": 4, "brain": "Mac · Qwen3 4B",
         "ttfw_ms_p50": 900, "errors": 0, "barge_ins": 1, "crashes": 0,
         "app_build": "0.1.0 (12)", "device": "iPhone18,1", "os": "iOS 27.0",
         "rating": "good|bad|null", "note": "…" | null }
```

### GET /voice/calls/{trace_id} -> 200 / 404

`{"call": Call + {"resource", "spans": [{span_id, parent_id, name, start_ms, duration_ms, status: "ok|error|open", status_message, attrs, events: [{name, at_ms, attrs}]}], "transcript": [{role: "user|assistant|ui", text, at, via, interrupted, outcome}], "crash_reports": [{file, kind, at, area, match: "trace_id|time", first_line}]}}`. Times are ms from the call's start. Crash reports are the `/ui/diagnostics` files that carry this `trace_id`, or (MetricKit, which can't carry one) a crash on the same build within a minute of the call.

### POST /voice/calls/{trace_id}/rating -> 200 / 404

`{"rating": "good" | "bad" | null, "note": "what it should have done"}`. `null` clears it. Response `{"rated": true}`.

### GET /voice/calls/export?rating=good|bad&format=jsonl -> 200

Rated calls as training data for the brain (`brain/train/calls.py` in the Threads app repo writes them to `brain/data/seeds/`). `format=jsonl` returns one row per line; otherwise `{"rating", "examples": [...]}`.

- `good`: one row per model request of each good call, exactly as sent plus the reply: `{"messages": [system, …, assistant], "tools": [...], "kind": "call-good", "source": "call:<trace_id>", "span_id", "model"}` (the brain's `train.jsonl` shape). Requests marked `threads.trainable: false` (Apple's on-device model, which runs its tools inside the call) and failed requests are left out.
- `bad`: one eval case per turn of each bad call: `{"id", "cat": "call-bad", "turns": [every utterance up to this one], "expect": {"not": {"kind", "target"?, "speech_contains"?}}, "got", "note", "state", "source"}`. The brain passes if it doesn't repeat what it did (`threads-brain eval`'s `expect.not`).

## Artifacts (the Artifact Shelf index)

The claude.ai Artifacts made from Claude Code sessions on this Mac, for the iPhone Artifacts tab. One data source: the Artifact Shelf's SQLite index (`~/Library/Application Support/Artifact Shelf/index.sqlite`). threadsd shells out to the `artifact_shelf` CLI (the same Swift indexer the Mac app uses — not re-parsed here), keeps it fresh, and adds `thread`. Pins written here show up in the Mac Shelf. Needs Artifact Shelf installed (`artifact_shelf` on PATH); if it isn't, these return 503 and the daemon logs it once.

### GET /artifacts?q=&project=&account=&pinned=1&limit=200 -> 200

```json
{ "artifacts": [ Artifact, ... ],
  "projects": [ {"name", "count"}, ... ],     // facets over all artifacts, most first
  "accounts": [ {"email", "label", "color", "store_id"}, ... ],
  "schema_version": 1 }
```

`q` matches title, project, session title, workspace, account, id and kind (all terms must match). `project`/`account` are exact. `pinned=1` limits to pinned. Sort: pinned first, then `last_edited` desc. `store_id` is the account's WKWebsiteDataStore id in the Mac app; its cookies are per-device, so a client signs in once on its own device (the id is only for keying).

```json
Artifact = {
  "id": "L2xiuuqc…",                 // the URL key, stable; same as the Mac Shelf
  "url": "https://claude.ai/artifact/…",
  "title", "icon" (one-word icon) , "kind" (type name or null),
  "account": "email" | null, "account_source": "probe|handoff|manual" | null,
  "project": "threads-mobile" | null, "cwd": "/Users/…" | null,
  "session_id": "<Claude Code session>" | null, "session_title": "cmux-threads · voice-mode" | null,
  "workspace": {"id", "title"} | null,        // cmux workspace (id is a UUID; title is the root's tab title)
  "thread": {"id", "name"} | null,            // the live threadsd session this was made in (matched on session_id)
  "version": "<latest>" | null,
  "first_seen", "last_edited",                // ISO 8601; last_edited = latest publish
  "pinned": bool,
  "local_path": "/…html" | null,              // the file it was published from, if known
  "thumbnail": null                           // v1: none; fall back to the type icon
}
```

Artifacts are private to the account that published them (`account`). A client opens one in a web view signed in to that account (one persistent store per account, sign-in once). When `account` is null, try each signed-in store.

### GET /artifacts/{id} -> 200 / 404

The `Artifact` plus `"sightings": [{source: "transcript|handoff", date, session_id, session_title, cwd, branch, version, account, workspace}]` (every time it was seen, newest first).

### POST /artifacts/{id}/pin -> 200 / 404

`{"pinned": true|false}` → the updated `Artifact`. Pinning only touches the Shelf index (it syncs to the Mac app); it changes no thread.

## POST /rc-recover -> 200

`{"dry_run": true}` optional. Runs each adapter's upkeep now (Claude: resend `/remote-control <name>` where the latest Remote Control notice is the disconnect and the box is empty). It also runs every 120 s.

## GET /healthz -> 200

`{"ok": true, "version": "1.1.0", "cmux_error": null | "<why cmux refused>", "schema_version": 1}`. Auth required.

## Phase 3: several Macs

Each Mac runs its own threadsd. On the aggregating Mac, `~/.config/threadsd/peers.json`:

```json
[ { "name": "webshop-mac", "url": "http://100.x.y.z:7878", "token": "<that Mac's token>" },
  { "name": "mac2", "sendmessage": ["⌂ Root · webshop @mac2"] } ]
```

Peers with a `url` are polled every 5 s and their sessions merged in (`transport: "threadsd-http"`, so /send is forwarded). `sendmessage` names become Remote Control-only rows routed through the ◎ Master.
