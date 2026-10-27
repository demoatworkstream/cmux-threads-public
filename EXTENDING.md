# Extending cmux-threads

The skill and threadsd are modular: a core plus one module per agent, machine setup and UI. Each item below is a checklist. If you need to touch the core for one of these, the design is wrong; fix the interface instead.

## Add an agent type (e.g. a new CLI agent)

- [ ] `agents/<type>.md` in the skill, self-contained, with the adapter table (launch, naming, status, submit, interrupt, Remote Control, usage limits) and how to launch, message and check it by hand.
- [ ] One line for it under "Modules" in `SKILL.md`, saying when to read it.
- [ ] `threadsd/plugins/agent_<type>.py`: a class with `@register_agent` implementing `plugins.Agent`:
  - `name`, `label`, `process_names` (the executable on the tab's tty), `capabilities`
  - `discover(ctx)` (rows for its sessions; add each surface to `ctx.claimed`), `refresh(ctx, row)` (`working`, `last_message`, optional `needs_permission` / `usage_limit`), `messages(row)`, `submit(row, text)`, `interrupt(row)`, optional `upkeep(state, dry)`
  - the doc strings `launch`, `naming`, `status_doc`, `submit_doc`, `interrupt_doc`, `usage_limit_doc` (served by `GET /agents`)
- [ ] Test against a scratch tab you create and close: `/state` shows it with the right state, `/send` delivers, `/interrupt` stops it. Never test in the user's sessions.
- [ ] No UI change: UIs key on `capabilities`, and `agent_label` is the badge.

## Add a status source (e.g. another tool's database)

- [ ] `threadsd/plugins/source_<name>.py`: a class with `@register_source` implementing `plugins.Source`: `name`, `interval`, `poll(ctx)`, which writes facts into `ctx` (and only there).
- [ ] The adapter that needs the facts reads them from `ctx` in `discover` / `refresh`.
- [ ] Note it in that agent's module (its Status row).

## Add a machine

- [ ] Install the skill and threadsd on it (`threadsd/install.sh`; see [ui/threadsd-api.md](ui/threadsd-api.md)). It runs project roots only, never a second master ([machines.md](machines.md)).
- [ ] On the master's Mac, add it to `~/.config/threadsd/peers.json`: `{"name", "url": "http://<its 100.x>:7878", "token": "<its token>"}`. Its sessions then appear in `/state` with their own `machine`, and `/send` is forwarded to it (transport `threadsd-http`).
- [ ] No threadsd there? Add `{"name", "sendmessage": ["⌂ Root · <project> @<machine>"]}` instead. Sends go through the ◎ Master by SendMessage (transport `sendmessage-bridge`).
- [ ] Add a transport (`plugins/transport_<name>.py`, `@register_transport`, `deliver(state, row, text)`) only for a new way of reaching sessions.

## Add a telemetry sink (e.g. another OpenTelemetry backend)

- [ ] For an OTLP/HTTP backend on this Mac or the tailnet (self-hosted Langfuse, Jaeger, an OTel Collector), no code: add it to `~/.config/threadsd/telemetry.json` (`forward`).
- [ ] Otherwise `threadsd/plugins/telemetry_<name>.py`: a class with `@register_telemetry` implementing `plugins.Telemetry`: `name`, `ingest(doc, meta)` (an OTLP JSON export, already redacted; `meta` has `client`, `received_at`, `snapshot()`). Set `reads = True` only for a store that answers `/voice/calls` (one is used).
- [ ] Never send traces off the user's machines.

## Add a question parser (e.g. another way threads mark choices)

- [ ] `threadsd/plugins/parser_<name>.py`: a class with `@register_parser` implementing `plugins.Parser`: `name`, `priority` (lower runs first; `numbered` is 10), `parse(body)` → `[{text, options: [{n, label, detail}], multi, allow_other, parse}]`, or `[]` to let the next one try. `body` is the message's needs-you block.
- [ ] A test with a real message in `threadsd/tests/test_questions.py`.
- [ ] If threads should write the new form, document it next to "Quick-select questions" in `SKILL.md`.

## Add a UI

- [ ] Use only the threadsd API ([threadsd/API.md](threadsd/API.md)): render from `/state` + `/events`, act through `/send` and `/interrupt`. Ignore unknown fields; check `schema_version`.
- [ ] Key controls on `session.capabilities`, never on `session.agent`.
- [ ] If the UI needs its own document format (a UI-specific document shape), add a view: `threadsd/plugins/ui_<name>.py` with `@register_ui` and `render(snapshot)`, served at `GET /ui/<name>`. Keep it a thin mapping over the schema.
- [ ] `ui/<name>.md` in the skill describing its contract (not its internals), plus one line under "Modules" in `SKILL.md`.
