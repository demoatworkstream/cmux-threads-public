# Agent: Codex

Read this when the user asks for a Codex thread, or when you message or check one.

| Adapter | |
|---|---|
| Launch | `codex --no-alt-screen --dangerously-bypass-approvals-and-sandbox -C <dir> … "$(cat <prompt-file>)"` (below) |
| Naming | no session name: the tab title `<mark> <topic>` is its only name |
| Status | the rollout JSONL (last `task_started` / `task_complete`), `~/.codex/state_5.sqlite`, cmux's `codex=` entry, the screen as tiebreaker. threadsd reads all of these, so `/state` shows Codex threads too |
| Submit | type, `enter`, confirm "Working". Never `escape` |
| Interrupt | `escape` while it shows "Working (… esc to interrupt)" (only when the user asks). `ctrl+c` twice quits Codex |
| Remote Control | no: not in `ListAgents` or the phone's Claude app. threadsd and the Threads app are the only remote path |
| Usage limits | the background watcher below; threadsd shows the thread as failed with "usage limit, retry at <time>" |

Record the agent type (`codex`) in your thread table when you start it.

If the user says to use Codex for a thread, launch the `codex` CLI in a new tab instead of `claude`. Never hand the work to an existing Codex session unless the user names that session.

```bash
cmux send --surface surface:N "codex --no-alt-screen --dangerously-bypass-approvals-and-sandbox -C <dir> [--add-dir <other-dir>] [--worktree] [-i <image>] \"\$(cat <prompt-file>)\"
"
```
- **Always pass `--no-alt-screen`.** Without it, Codex draws on the terminal's alternate screen and `cmux read-screen` comes back blank, so you can't see its status.
- **The trust prompt blocks a new folder.** The first time Codex runs in a directory, it stops at "Trust this folder? 1. Trust and continue". `--dangerously-bypass-approvals-and-sandbox` doesn't skip it, and your prompt waits behind it. After launching, read the screen. If the prompt is there and the folder is one the user asked you to work in, `cmux send-key --surface surface:N enter` trusts it (the choice is saved). Otherwise ask the user.
- `-C <dir>` sets the working directory. `--add-dir` gives it write access to other repos it needs. `--worktree` gives it a git worktree (codex picks the name). `-i` attaches images the user sent.
- Codex has no `-n` or `--remote-control` flag, so the tab title is its only name, and it can't be reached from the Claude phone app. Tell the user that.
- Status works the same way: read the screen for the `STATUS:` line. Codex shows "• Working (… esc to interrupt)" while busy, and only the `› Ask Codex to do anything` prompt when idle.
- If the screen is unclear, check these:
  - `cmux list-status --workspace <ws>`: cmux's Codex hooks set a `codex=` entry there, e.g. "Codex rate limit". It covers the whole workspace, not one tab.
  - `~/.codex/state_5.sqlite`: `select id, cwd, title, datetime(updated_at,'unixepoch','localtime'), rollout_path from threads order by updated_at desc limit 5`. A thread only appears here once the first prompt has been submitted. If there's no row, Codex is stuck before starting (trust prompt, login, or rate limit).
  - The thread's `rollout_path` JSONL: its last `task_complete` event holds `last_agent_message`, or an `error` (e.g. `usage_limit_exceeded`).
- **Asking the user:** same as Claude: `STATUS: needs you`, then questions in the quick-select form from the core (`(pick one)` / `(pick any)`, numbered options under the question). threadsd parses Codex messages the same way (`questions[].options`). Put this in the Codex prompt; Codex has no pop-up of its own to avoid.
- **Messaging Codex:** text from `cmux send` lands in Codex's input box as a paste, and the trailing newline doesn't submit it. Follow it with `cmux send-key --surface surface:N enter`, then confirm "Working" appears.
- **Usage limits:** if the user asks you to keep a Codex thread going through usage limits, run a background watcher with Bash `run_in_background`. Every minute it reads the tab. While the screen shows "Working (" it does nothing. On "You've hit your usage limit … try again at H:MM PM" it waits until that time plus 2 minutes, then sends a "continue where you left off" message and presses Enter. If the thread goes idle for any other reason, it exits so you're woken to report.
- To stop a Codex tab: `escape` at a startup prompt, or `ctrl+c` twice while it's working.
