# Agent: Claude Code

The default agent for threads, roots and the master. Read this when you launch, message or check a Claude Code session.

| Adapter | |
|---|---|
| Launch | `claude --dangerously-skip-permissions --remote-control "<name>" -n "<name>" [-w <slug>] "$(cat <prompt-file>)"` |
| Naming | session `<project> · <slug>` (roots `⌂ Root · <project>`, master `◎ Master`); tab `<mark> <topic>` |
| Status | threadsd: `~/.claude/sessions/<pid>.json` (busy/idle) + hooks + the transcript. By hand: the screen |
| Submit | INSERT check, type, `enter`; escape only when idle (below) |
| Interrupt | escape while working (twice from INSERT) |
| Remote Control | yes (`--remote-control`, `/remote-control <name>`) |
| Usage limits | none to handle |

## Launch

```bash
cmux new-surface --type terminal --pane <thread-pane> --focus false   # prints surface:N
cmux rename-tab --surface surface:N "▶ <topic>"
cmux send --surface surface:N "cd <repo> && claude --dangerously-skip-permissions --remote-control \"<project> · <slug>\" -n \"<project> · <slug>\" [-w <slug>] \"\$(cat <prompt-file>)\"
"
```
- Add `-w <slug>` (its own git worktree) when the thread will change code. Leave it off for read-only research.
- The text sent must end with a newline, or the command doesn't run.

## Trust prompt in a new folder

- **Claude's trust prompt in a new folder:** the first time `claude` runs in a directory it shows "Is this a project you created or one you trust?" with `❯ No, exit` selected. `--dangerously-skip-permissions` doesn't skip it, and it **discards the initial prompt argument**. If the user asked you to work in that folder: `cmux send-key --surface surface:N down`, then confirm `❯ Yes, I trust this folder` is selected, then `enter`. After that, send the brief again as one line ("Read <prompt-file> and follow it exactly…"), press `return`, and check for the spinner. A message sitting in the input box wasn't submitted, so press `return` again.

## Input box and submitting

If threadsd is running, prefer `POST /send` (see [ui/threadsd-api.md](../ui/threadsd-api.md)): it does all of the following and logs the send. By hand:

1. **Box empty?** Read the screen and check that the `❯` input box is empty. Careful: Claude Code shows a greyed-out prompt suggestion in an empty box (e.g. a likely reply such as "I connected my iphone, install now"), and `read-screen` returns it as plain text. Text that reads like a predicted reply to the session's last message is probably a suggestion, not something the user typed. Typing replaces a suggestion, so it's safe to send. If you can't tell, ask, and never tell the user they left unsent text unless you're sure.
2. **INSERT mode before typing.** Sessions run in vim mode. Check the status line for `-- INSERT --`. If it's missing, the box is in NORMAL mode, and typed text runs as vim commands: "From the supervisor" arrives as "m the supervisor" (`F` `r` = find "r", `o` = open a line and enter INSERT), "Read …" loses "Rea", "Supervisor" loses "S". The box then shows `-- INSERT --`, which hides the cause. A previous escape-then-enter submit leaves the box in NORMAL, so the next send hits this. Fix: `cmux send --surface surface:N --workspace workspace:W -- "A"` (append; `i` would insert before the last character), then confirm `-- INSERT --` shows, then type.
3. **Type literally.** `cmux send` turns `\n`, `\r` and `\t` in the text into keys, even after a backslash. For text containing a backslash, split it after each backslash into separate `cmux send` calls. For a newline inside a message, use `cmux send-key … shift+enter`, never `\n`.
4. **Submit.** In INSERT mode, one `cmux send-key --surface surface:N --workspace workspace:W enter` usually submits. Read the screen: the box is empty and a spinner shows (or, if the session was busy, the message is queued above the box). If the text is still in the box: only when the session is idle (no spinner), send `escape`, wait about 1.5 s, check that `-- INSERT --` is gone (send `escape` again if it isn't), then `enter`. Never escape a busy session: from NORMAL mode, escape interrupts its turn. If it's busy, just press `enter` again, or wait until it's idle. Never send escape to Codex at all: Codex submits with `enter` (see [agents/codex.md](codex.md)).
5. Read the screen after each try until the box is empty and a spinner shows. Never report a message as delivered until you have seen that.
6. **Suggestions aren't messages.** An idle box can show Claude's greyed next-prompt suggestion (e.g. "close the scratch tabs"). It isn't the user's text: `enter` doesn't send it, and you can type over it. Never "submit" it on the user's behalf.
7. **Never keep surface flags in one shell variable.** In zsh, `T="--surface surface:N --workspace workspace:W"; cmux send $T "…"` passes one unsplit word, cmux ignores it, and the text lands in your own surface. Write the flags out in each call.

**Interrupt** (only when the user asks): escape while it's working. From `-- INSERT --` the first escape only switches to NORMAL, so it takes two. Claude then puts the interrupted prompt back into the box: clear it before the next send, or that send finds the box "not empty".

## Asking the user

Claude Code has AskUserQuestion; never use it (see "No pop-ups" in the core). Ask in text with the quick-select form from the core instead: a question line ending in `(pick one)` / `(pick any)`, numbered `**Label:** detail` options under it, after `STATUS: needs you`. threadsd parses it into `questions[].options` and the Threads app shows chips. Answers arrive as `Answer to question 1 ("…"): 1, 3 (Apple Notes; Gmail)`, optionally with ` — plus: <the user's own words>`.

## Dialogs that replace the input box

AskUserQuestion menus, permission prompts and confirms replace the `❯` box until someone answers on the Mac. Typing then goes nowhere, and every send fails. Don't open them yourself (see "No pop-ups" in the core). When another session shows one:

- **With threadsd:** `/state` shows it as `session.prompt` (questions, options, multi-select), and `state` is `needs_you`. Answer it with `POST /prompt/answer`. threadsd presses the keys and confirms the dialog closed. Sends made meanwhile are queued and delivered once the box is back, so they never fail because of the dialog. Details: [ui/threadsd-api.md](../ui/threadsd-api.md).
- **By hand:** each question is a tab (`← ☐ Color ☐ Fruits ✔ Submit →`) with numbered options. Move with `up`/`down` and read the screen to check the `❯` cursor row before acting. Single choice: `enter` on the option (it moves on to the next question). Multi-select: `enter` toggles a row (`space` does not), then go down to the `Submit` row and `enter`. Other: go to "Type something", type the text, `enter`. With several questions, finish on the review screen's "1. Submit answers". `Esc` cancels the whole dialog: avoid it. Permission prompts: `enter` on "1. Yes" (or the option the user chose).

## What idle and working look like

- A finished, idle session shows a "done <time>" line above an empty `❯` prompt. A working one shows a spinner ("Processing…", "thinking").

## Keep Remote Control connected

When the user switches the signed-in claude.ai account or organization, every session prints `Remote Control disconnected — signed-in claude.ai account or organization changed on this machine — run /remote-control …` and drops off the phone app. The fix is to send `/remote-control <session name>` to it again.

- On every status check, and when the user mentions switching accounts, run the bundled script. It finds sessions whose latest Remote Control notice is the disconnect, skips any with text typed in the input box, and resends `/remote-control <name>`, taking the name from the session's prompt border:
  ```bash
  ~/.claude/skills/cmux-threads/scripts/rc-recover.sh --dry-run          # this workspace: root + its threads
  ~/.claude/skills/cmux-threads/scripts/rc-recover.sh                    # do it
  ~/.claude/skills/cmux-threads/scripts/rc-recover.sh --all-workspaces   # every root and thread in cmux
  ```
  Include your own surface: the command queues and runs when your turn ends.
- Success shows `/remote-control is active · … https://claude.ai/code/session_…`. Confirm with `read-screen` and report which sessions you reconnected.
