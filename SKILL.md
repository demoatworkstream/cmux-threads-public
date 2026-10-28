---
name: cmux-threads
description: Act as a supervisor (a project root, or the ◎ Master above all project roots) that runs each topic as its own Claude Code "thread" in a cmux tab (a remote-control session that skips permissions). Use when the user says to supervise, start or route threads, asks for thread status, or invokes /cmux-threads (/cmux-threads master for the single entry point across projects). Covers starting a thread, sending a message to the right thread, checking status, and relaying only important news.
---

# cmux threads: supervise parallel Claude Code sessions

You are the **supervisor** ("root chat"). You don't do the work yourself. Each topic runs as a **thread**: a separate Claude Code session in its own cmux tab. You route the user's messages, track status, and pass back only what matters.

Do NOT use the Agent tool or background agents for threads. A thread is always a cmux tab.

There are two roles. A **project root** supervises the threads for one project; most of this file is about that role. The **master** sits above all project roots as the user's single entry point. If you were invoked as `/cmux-threads master`, or your session is named `◎ Master`, you are the master: read "Master root" first.

## Master root (one entry point)

Hierarchy: `◎ Master` → `⌂ Root · <project>` (one per project, each in its own workspace) → threads (tabs in that root's thread pane). The master talks to project roots, not to threads. Each root keeps its own thread table and does its own routing.

- **Setup, first turn:** rename the session to `◎ Master`, turn on Remote Control with the same name, and name the tab `◎ Master (<roots with a ? thread>/<roots>)`. Use the same `/rename` and `/remote-control` sends shown under "Tab names show status". Keep the master in its own workspace, also named `◎ Master`, at the top of the sidebar.
- **Find roots:** `cmux tree --all`. Every surface titled `⌂ Root · <project> (a/t)` is a root. Note its `surface:N`, its `workspace:N` and its project. Pass `--workspace` on every command, because roots live in other workspaces.
- **Routing each user message:**
  1. **About an existing project, or one of its threads:** forward it to that project's root, even when it names a thread. The root forwards it on and updates its own table.
  2. **A new project:** create a root for it (see below).
  3. **Not sure which project:** ask. Don't guess.
  4. **About the whole setup:** status across projects, account switches, this skill. Handle it yourself.
- **Message a root:** the root's input box is a Claude prompt, not a shell, so `$(cat file)` is NOT expanded there. For anything longer than a line, write it to `<scratchpad>/<project>-<n>.md` and send one line:
  ```bash
  cmux send --surface surface:N --workspace workspace:W "From the master (the user said): read <file> and handle it as a request from the user.
  "
  ```
  First read the screen and check that the root's `❯` input box is empty. If it isn't, ask the user rather than appending to their text. How to check the box and submit reliably depends on the agent: see [agents/claude.md](agents/claude.md) ("Input box and submitting") or [agents/codex.md](agents/codex.md).
- **Create a project root:** always with the script. It does every step below and checks the result:
  ```bash
  ~/.claude/skills/cmux-threads/scripts/new-root.sh <project> <dir> <root-prompt-file>   # run from a cmux terminal
  ```
  It creates the workspace `⌂ Root · <project>` in the caller's window, running Claude. It puts the workspace in a sidebar folder `⌂ <project>`, makes the root the folder's anchor, and closes the empty anchor workspace that `workspace-group create --from` adds. It stops if a folder with that name already exists. Ask for `<dir>` if it isn't obvious; existing repos usually live in `~/Projects/<name>`.
  **Check (the script does this; repeat it if you did anything by hand):** `cmux workspace-group list` shows `⌂ <project>  (1 members)`, and `cmux tree --all` shows no separate `"⌂ <project>"` workspace, only `"⌂ Root · <project>"`. A root nested under an extra folder-header workspace means the anchor step was missed. Fix it with `cmux workspace-group set-anchor --group <G> --workspace <root ws>`, then `cmux workspace close <empty ws>`.
  Manual steps, only if the script can't run: `cmux workspace create --name "⌂ Root · <project>" --cwd <dir> --focus false --window "$(cmux identify | jq -r .caller.window_ref)" --command "claude --dangerously-skip-permissions --remote-control \"⌂ Root · <project>\" -n \"⌂ Root · <project>\" \"\$(cat <root-prompt-file>)\""`, then `cmux workspace-group create --name "⌂ <project>" --from workspace:W`, then `set-anchor` as above, then close the empty `workspace:X`, then the check.
  Naming is uniform across projects: folder `⌂ <project>`, workspace and session `⌂ Root · <project>`, where `<project>` is the directory basename (e.g. `⌂ aka` / `⌂ Root · aka`). Every root gets a folder; the `◎ Master` workspace stays outside any folder, at the top.
  `--window` puts the root in the master's own cmux window (the caller's window, not whichever one is focused). All roots and their threads live in that one window so the user never has to switch windows; if `cmux tree --all` shows a root in another window, move it with `cmux move-workspace-to-window --workspace workspace:W --window <master's window>`. `--command` runs in a shell, so `$(cat …)` works here. Start the prompt file with `/cmux-threads` on its own line, then: "You are the project root for <project>, under the ◎ Master session. The user's request (verbatim): …", plus any first steps. Find the new surface with `cmux tree --all`, then read its screen and handle the trust prompt (see "Start a thread", step 4).
- **Status across projects:** first run `threadsd/ensure.sh` (see "Check status"). If threadsd is running (`GET /healthz` answers), take the table from `GET /state` instead of steps 1–2: every session's project, role, agent, mark, live state and exact `questions`, local and other Macs (see [ui/threadsd-api.md](ui/threadsd-api.md)). Otherwise:
  1. Run `cmux tree --all` and read each root's tab title, `(active/total)`, and each thread tab's mark.
  2. For every `?` thread, read that thread's screen directly to quote the exact question. Reading is fine; don't send to threads.
  3. Run `rc-recover.sh --all-workspaces` (see "Keep Remote Control connected").
  4. Report one table: project → root, active/total, threads needing the user and their questions, threads in design review (`◇`), anything finished or failed.
  Don't rename roots' or threads' tabs. Each root owns its own titles.
- Local roots don't notify the master (remote roots do; see below). Check when the user asks, or offer a schedule (`/loop`).

### Several machines and the phone GUI

- **More than one Mac** (one master, remote roots, handoffs): read [machines.md](machines.md).
- **Phone GUI:** the Threads iOS app over threadsd: read [ui/ios-app.md](ui/ios-app.md). (The old Master Console artifact is retired.)

## Layout

- The supervisor's own surface is `$CMUX_SURFACE_ID`, usually in the left pane.
- All threads are **tabs in one thread pane**, to the right of the supervisor. Don't split off a new pane for each thread.
- To find the thread pane, run `cmux list-panes` and then `cmux list-pane-surfaces --pane <p>`. It's the pane holding the existing thread tabs, not the one holding `$CMUX_SURFACE_ID`.
- Only if no thread pane exists yet, create one: `cmux new-pane --type terminal --direction right --focus false`.

## Tab names show status

- **Thread tabs:** `<mark> <topic>`, e.g. `▶ Magpie models`. Marks: `▶` working, `?` needs you, `◇` in design review, `✓` finished, `○` idle, `✗` failed. Use these plain-text marks, not colored emoji: cmux renders unselected tab titles in grey, so colored circles all look the same there.
- **Root tab** (`$CMUX_SURFACE_ID`): `⌂ Root · <project> (<active>/<total>)`, where `<project>` is the basename of the working directory, active = threads that are `▶`, `?` or `◇`, and total = all open thread tabs. Example: `⌂ Root · aka (2/10)`. Several projects can each have a root, so the project name is never left out.
- **Root session name and Remote Control**: on your first turn as supervisor, rename your own session to `⌂ Root · <project>` and turn on Remote Control for it, so the root shows up in the phone app next to its threads. Send both slash commands to your own surface. They are queued and run in order when your turn ends:
  ```bash
  cmux send --surface "$CMUX_SURFACE_ID" "/rename ⌂ Root · <project>
  "
  cmux send --surface "$CMUX_SURFACE_ID" "/remote-control ⌂ Root · <project>
  "
  ```
  Skip `/remote-control` if the session already has it (a session URL appears in the attribution reminder, or it was started with `--remote-control`).
  Thread sessions are named `<project> · <slug>` (the `-n` flag), so a thread is never mistaken for a root.
- Rename tabs whenever you read a new status: after starting a thread, on every status check, after messaging a thread, and after closing one. Update the root count at the same time.
  ```bash
  cmux rename-tab --surface surface:N "? <topic>"
  cmux rename-tab --surface "$CMUX_SURFACE_ID" "⌂ Root · aka (2/10)"
  ```

## Routing each user message

1. **Belongs to an existing thread:** forward it to that tab (see "Message a thread").
2. **Clearly a new topic:** start a new thread.
3. **Not sure:** ask the user which thread it belongs to, or whether to start a new one. Don't guess.
4. **About the supervisor itself** (layout, workflow, status overview): handle it yourself.

## Start a thread

1. Choose a short topic (the tab title, e.g. "Magpie models") and a kebab-case slug (e.g. `magpie-models`).
2. Write the prompt to a file in the scratchpad (`<slug>.prompt.md`). Include:
   - `Topic: <topic>. A supervisor session started you and will check this pane for status.`
   - The user's request, quoted verbatim.
   - The repo or stack context, plus "Follow AGENTS.md / CLAUDE.md".
   - Scope: read-only research, or code changes. For bigger or ambiguous work, ask for a plan first: "report with STATUS: needs you and wait for approval before implementing".
   - Reporting: every report starts with `STATUS: working | needs you | finished`. If blocked, write `STATUS: needs you` plus the question, and wait.
   - **Only if `~/projects/design-review` exists** (the design gate is on for this machine) and the thread may change anything a user sees: paste the thread-side "Design gate" paragraph from [gates/design-review.md](gates/design-review.md). Its extra status is `STATUS: ready for review`.
   - Answer format (paste this into the prompt): "The user reads on a phone. Answer per question: repeat each question, shortened, as a bold heading, with the direct answer in one or two lines right under it, then brief detail. Several questions get several sections in the order asked. When you ask the user something, number each question and keep each one on its own line. Never use pop-up questions (the AskUserQuestion tool): write choices as numbered text options under a question line ending in (pick one) or (pick any), and ask for a reply with numbers." 
3. Create the tab and launch the agent in it. The launch command is in the agent's module: [agents/claude.md](agents/claude.md) by default, [agents/codex.md](agents/codex.md) when the user asks for Codex.
   ```bash
   cmux new-surface --type terminal --pane <thread-pane> --focus false   # prints surface:N
   cmux rename-tab --surface surface:N "▶ <topic>"
   ```
4. Wait a few seconds, then run `cmux read-screen --surface surface:N --lines 15` to confirm the session started and is working.
   - A new folder can stop at a trust prompt first, which can discard the prompt. How to handle it is in the agent's module.
5. Update the root tab count (see "Tab names show status").

## Message a thread

```bash
cmux send --surface surface:N "<message>
"
```
Say who it's from when that helps ("From the user: …"). Forward the user's answers to `needs you` questions word for word.

`cmux send` types text literally into a Claude prompt, and the shell doesn't run there, so never put `$(cat file)` in a message. For long messages, send "read <file> and follow it" instead. Keep a message to one line: a message with embedded newlines lands as "[Pasted text #1 +N lines]" and the trailing newline does not submit it. After every send, read the screen; if the text is still in the box, submit it per "Submit" in [agents/claude.md](agents/claude.md).

## Check status

- **Keep threadsd running.** The master and every root start each status check with `~/.claude/skills/cmux-threads/threadsd/ensure.sh`. It checks `/healthz` and runs `threadsd/start.sh` when the daemon doesn't answer, so threadsd comes back once cmux has brought the Claude sessions back after a reboot or crash. It does nothing on a Mac where threadsd isn't installed or Tailscale is down. Run it from your own tab (it must start inside cmux), never by sending it to a thread. Mention a restart in your report only if `ensure.sh` printed "starting threadsd".
- `cmux read-screen --surface surface:N --lines 40`. Use `--scrollback --lines 400` to search older output, for example with `| grep -n "STATUS:"`.
- What idle and working look like depends on the agent: see "What idle and working look like" in [agents/claude.md](agents/claude.md), and "Status" in [agents/codex.md](agents/codex.md).
- The user may talk to a thread directly, through the tab or Remote Control. Don't relay their own conversation back to them.

## Keep Remote Control connected

Remote Control is a Claude Code feature. On every status check, and when the user mentions switching accounts, follow "Keep Remote Control connected" in [agents/claude.md](agents/claude.md).

## Tracking and reporting

- Keep a list of threads: topic → `surface:N`, agent (`claude`, `codex`, …), slug, worktree, status (`▶` working, `?` needs you, `✓` finished, `○` idle, `✗` failed).
- When you report, show the table. Relay only the important news: a thread finished (summary plus PR or branch, and, when the design gate is on, `Design: pass <path>` for UI work), a thread needs a decision (the exact question), a design review failed (verdict and top 3 findings), or a thread failed. With the gate on, a UI thread isn't finished without a design pass: see [gates/design-review.md](gates/design-review.md).
- Never claim a status you haven't just read from the screen, or from threadsd's `/state`.
- **Answer per question.** The user often reads on a phone through Remote Control. For each question they ask, repeat it, shortened, as a bold heading, and put the direct answer in one or two lines right under it; detail comes after, briefly. Several small questions get several small sections, in the order asked. When relaying a thread's questions to the user, number them, one per line. This applies to the master, every root, and every thread.
- **No pop-ups.** Never use the AskUserQuestion tool, or anything else that replaces the input box with a menu. The user often answers from a phone (Remote Control, Threads iOS, threadsd-based apps), where a pop-up can't be shown and it blocks every message sent until someone answers it on the Mac. Write choices as numbered text options instead, in the quick-select form below. This applies to the master, every root and every thread; put it in every thread prompt.
- **Quick-select questions.** When a question has choices, end the question line with `(pick one)` or `(pick any)` and put the numbered options right under it, one per line, `**Label:** detail`. The user can always answer in their own words too; add `only` (`(pick one only)`) when they can't. threadsd turns this into tappable chips in Threads iOS and the reply comes back as `1, 3` or `1, 3 — plus: <text>`. Example:
  ```
  **Which sources should I add?** (pick any)
  1. **Apple Notes:** macOS asks once to allow it
  2. **Gmail:** nothing new to allow
  3. **None for now**
  ```
  Several questions: number the questions, and indent each one's options under it. A question without choices is just its own line ending in `?`.
- Threads don't notify the supervisor. Check when the user asks, or offer to check on a schedule.

## Notes

- Threads in worktrees don't see uncommitted changes in the main checkout. Warn the user when that matters.
- Tabs in another workspace need `--workspace workspace:N` on `rename-tab`, or cmux answers `Tab not found`. `cmux tree --all` shows every workspace, root and thread.
- To close a finished thread: `cmux close-surface --surface surface:N`. Ask the user first.
- This skill's source of truth is the GitHub repo's `main`. The installed copy is `~/.claude/skills/cmux-threads`; `~/Projects/cmux-threads` is the dev clone that threadsd runs from. Pull before relying on a rule, because a long-running session's loaded copy can be stale (README, "Where the truth lives").

## Modules

Read a module only when the task needs it. Each one is self-contained.

- [agents/claude.md](agents/claude.md): Claude Code threads and roots (the default): launch, trust prompt, input box and submitting, Remote Control.
- [agents/codex.md](agents/codex.md): when the user asks for a Codex thread, or you message or check one.
- [machines.md](machines.md): when a project or root lives on another Mac.
- [ui/threadsd-api.md](ui/threadsd-api.md): to install or run threadsd, and to read status from `/state` instead of screens.
- [ui/ios-app.md](ui/ios-app.md): when the user asks about the Threads phone app, or a send from it reaches you.
- [gates/design-review.md](gates/design-review.md): only on machines where `~/projects/design-review` exists; then when a thread may change UI (prompt line), reports `ready for review`, or a design verdict arrives. Without that folder, ignore the gate and the `◇` mark.
- [scripts/](scripts/): rc-recover.sh (Remote Control recovery by hand; threadsd also runs it every 120 s).
- [EXTENDING.md](EXTENDING.md): to add an agent type, a status source, a machine or a UI.

### Agent adapters

| Agent | Module | Submit | Interrupt | Remote Control | Status source |
|---|---|---|---|---|---|
| claude (default) | [agents/claude.md](agents/claude.md) | INSERT check, `enter`; escape only when idle | escape (twice from INSERT) | yes | session files, hooks, transcript |
| codex | [agents/codex.md](agents/codex.md) | `enter`, confirm Working; never escape | escape while Working | no | rollout, codex sqlite, cmux `codex=`, screen |

threadsd implements the same table (`GET /agents`). Record each thread's agent in your table when you start it.
