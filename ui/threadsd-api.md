# threadsd: live state and send, one API for every UI

Read this to install or run threadsd, or when you're a root or the ◎ Master and want status without reading screens.

threadsd is a small daemon on each Mac (`threadsd/` in this skill, Python stdlib). It knows every agent session in cmux (Claude Code, Codex) and serves it over Tailscale. The full contract is [threadsd/API.md](../threadsd/API.md). Every UI uses it: the Threads iOS app, and you, with curl.

## Changes

- **2026-10-25 · Artifacts (`GET/POST /artifacts`).** The claude.ai Artifacts made from Claude Code on this Mac, for the iPhone Artifacts tab. One data source: the Artifact Shelf's SQLite index; threadsd shells out to the `artifact_shelf` CLI (the same Swift indexer, not re-parsed), keeps it fresh, and adds `thread` {id,name} by matching each artifact's Claude session id to a live session. `GET /artifacts?q=&project=&account=&pinned=1` lists (with `projects`/`accounts` facets), `GET /artifacts/<id>` adds sightings, `POST /artifacts/<id>/pin {"pinned":bool}` toggles the pin (syncs to the Mac Shelf). An SSE `artifact` event fires when the index changes. A new `source_artifacts` plugin; the only core change is the three routes and a watcher thread. Needs Artifact Shelf installed (`artifact_shelf` on PATH), else 503. Additive. Details in [threadsd/API.md](../threadsd/API.md#artifacts-the-artifact-shelf-index).

- **2026-10-25 · Voice call traces (OpenTelemetry).** `POST /v1/traces` takes OTLP/HTTP JSON: one trace per voice call from the iOS app (listen, speech to text, brain and each model request with GenAI attributes, tool runs, sends, spoken replies, barge-ins, timings, errors). It is redacted, then stored on this Mac in `~/.local/share/threadsd/voice-traces.db` with the raw OTLP kept. Read it: `GET /voice/calls` (list), `GET /voice/calls/<trace_id>` (timeline, transcript and the crash reports of that call); rate it: `POST /voice/calls/<trace_id>/rating`; training data: `GET /voice/calls/export?rating=good|bad`. Sinks are a new `TELEMETRY` plugin registry (`calls`, and `forward` to a self-hosted Langfuse or any OTel backend on this Mac or the tailnet, configured in `~/.config/threadsd/telemetry.json`); no core change per sink. Additive. Details in [threadsd/API.md](../threadsd/API.md#voice-calls-opentelemetry).
  - **What the screen showed is in the trace too:** each confirm card as displayed (title, text, buttons) as a `ui.confirm_card` span, with how it went away (`threads.ui.outcome`: tapped Send, said: …, replaced). Transcript lines with role `ui`.
  - **Read all my voice calls** (Master or a root): `curl -s -H "Authorization: Bearer $T" $B/voice/calls | jq '.calls[] | {trace_id, started_at, status, title, turns, brain, rating}'`, then `$B/voice/calls/<trace_id> | jq '.call.transcript'` for what was said, `.call.spans` for the timeline.

- **2026-10-25 · `/ui/diagnostics`.** Crash and error reports from the iOS app (its recent events and the stack at a crash, Apple's MetricKit reports, non-fatal errors), stored in `~/.local/share/threadsd/diagnostics/`. A UI plugin: no core change. Read them with `ls -t ~/.local/share/threadsd/diagnostics | head` or `GET /ui/diagnostics`.
- **2026-10-25 · `GET /voice/brains`.** Lists the voice brains a phone call can use, with health checked by threadsd (`up`, `latency_ms`, `last_checked`). Brains come from a new `BRAINS` plugin registry; the `mac` provider reads `~/.config/threadsd/brains.json`. Additive; older clients ignore it, and newer clients treat a 404 as no brains. Details in [threadsd/API.md](../threadsd/API.md#get-voicebrains---200).

## Install and run

```bash
~/.claude/skills/cmux-threads/threadsd/install.sh          # from a cmux terminal
```

- Needs Tailscale installed and signed in: threadsd listens only on this Mac's `100.x` address, port 7878, never on localhost or the LAN. Without Tailscale, install.sh stops and says so.
- The token is created on first run in `~/.config/threadsd/token` (0600). Every request needs `Authorization: Bearer <token>`.
- Logs: `~/.local/share/threadsd/threadsd.log`. Every send: `~/.local/share/threadsd/sends.jsonl`.
- **Where it runs depends on cmux's socket mode.** In the default mode (`cmuxOnly`), cmux accepts only processes started inside cmux, which a launchd agent never is. So install.sh starts threadsd detached from your cmux terminal (`threadsd/start.sh`). It survives the tab closing, but not a logout or reboot. If `/healthz` doesn't answer, run `threadsd/start.sh` again from any cmux terminal; `threadsd/ensure.sh` does that check and start in one step, and the master and every root run it on each status check (SKILL.md "Check status"). start.sh doesn't restart threadsd after a crash by itself: the next status check does. If the user switches cmux to password mode (cmux Settings > Automation) and saves the password in `~/.config/threadsd/cmux-password` (0600), install.sh installs the launchd agent `com.cmux-threads.threadsd` instead (starts at login, restarts on crash). Changing cmux's socket mode is the user's decision; don't do it yourself.
- **Hooks (optional, faster and adds permission prompts):** `threadsd/install.sh --hooks` adds `SessionStart`, `UserPromptSubmit`, `Stop` and `Notification` hooks to `~/.claude/settings.json`. It backs the file up to `settings.json.bak-<timestamp>` first and keeps every existing hook. The hook (`threadsd/hook.sh`) only writes a spool file and exits 0, so it never blocks or fails a session. New sessions pick the hooks up at start; running ones as they reload settings. Settings.json is deny-listed for Claude's file tools, so **the user runs this one**: ask them to type `! ~/.claude/skills/cmux-threads/threadsd/install.sh --hooks`. Without hooks threadsd still works: busy/idle comes from `~/.claude/sessions/<pid>.json`, and messages from the transcripts.
- Stop or remove: `threadsd/uninstall.sh` (it also removes the hooks; the user runs it for the same reason).

## Use /state instead of reading screens

```bash
T=$(cat ~/.config/threadsd/token); B=http://$(ifconfig | awk '/inet 100\./{print $2; exit}'):7878
curl -s -H "Authorization: Bearer $T" $B/state | jq '.sessions[] | {project, role, agent, mark, state, title, surface, questions: [.questions[].text]}'
curl -s -H "Authorization: Bearer $T" $B/state | jq '.sessions[] | select(.state=="needs_you") | {project, title, questions}'
```

- Each session has `role` (master/root/thread), `project`, `agent`, `surface`/`workspace`, the tab `mark`, a live `state` (working, needs_you, idle, finished, failed), the first 400 characters of `last_message`, `questions` (already split for `needs_you`: one item per question, its numbered choices in `options`), and `root_id`.
- **Status across projects (master) and Check status (root):** use `/state` for the table, and quote `questions[].text` as the exact questions. Read a screen only when /state lacks something (e.g. a question longer than the snippet: `GET /sessions/<id>/messages?limit=1`), or when threadsd isn't running.
- `state` is live; `mark` is what the root last set. Rename tabs from `state` as before. Roots still own their titles.
- **Sending:** `POST /send {"target": "<session id>" | "master", "text": "…"}` does the reliable submit for the session's agent (Claude: INSERT check, enter, escape only when idle; Codex: enter, never escape), refuses a non-empty box, and logs the send. The answer is `delivered` only after it saw the box empty and the session working.
- **Remote Control recovery** runs inside threadsd every 120 s (the same rules as `scripts/rc-recover.sh`, which still works by hand). Force a pass: `POST /rc-recover` (`{"dry_run": true}` to preview).
- **Questions carry their time and state:** `asked_at` (when the thread wrote it), `message_id` (the message in `/sessions/<id>/messages`), `stale` (the user wrote to the thread since), and `status` (`open`, `answered`, `skipped`). `attention: "open"` is the Needs you list. `last_status` is a later bookkeeping line, kept apart from the question. `updated_at` is the session's real last activity.
- **Quick-select.** A question with choices has `options` (`[{n, label, detail}]`), `multi`, `allow_other` and `parse` (`marked` or `heuristic`); `options: []` means a plain answer. Answer with `POST /send {"target", "question_id", "text": "1, 3"}` (or `"1, 3 — plus: <text>"`, or `"choices": [1, 3], "other": "…"`). Ask your own questions in the form from "Quick-select questions" in the core so they parse as `marked`.
- **Skip = ignore/clear.** `POST /send {"target", "kind": "skip", "question_id"}` (or `"kind": "skip_all"` for a whole thread) clears it from Needs you on every device. **Nothing is sent to the thread.** It comes back only if the thread asks a new question. Undo: `"unskip"` / `"unskip_all"`. The user decides what to skip; don't skip on their behalf.
- **Dialogs:** if a session shows an AskUserQuestion, permission or confirm menu, `/state` has it as `prompt` (with options). Answer with `POST /prompt/answer`. Sends to that session are queued ("answer the question above first") and delivered automatically afterwards.
- **Chat history** (`/sessions/<id>/messages`) leaves out system rows (scheduled/cron turns and their replies) unless `include_system=1`.
- `GET /events` is a live SSE stream of the same session objects, for UIs.
