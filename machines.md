# Several machines: one master, remote roots

There is only ever **one `◎ Master`**, on the user's main machine. Every other machine runs project roots only (each with `--remote-control` under its exact `⌂ Root · <project>` name) and never a master. If a second master exists, ask the user to retire it.

- **Each project lives on one machine.** If two machines ever need the same project, the session name gets a machine suffix: `⌂ Root · webshop @mac2`.
- **The master's table has a "where" column:** `local` (with `surface:N`, `workspace:W`) or `remote` (with the bridge session name).
- **Find roots:** `cmux tree --all` for local ones, plus `ListAgents` rows named `⌂ Root · <project>` that aren't in the local tree. A remote root that's missing from `ListAgents` isn't on Remote Control, so it can't be reached. Tell the user.
- **Message a remote root** with `SendMessage` to its name, with the same text you'd `cmux send`. There's no input-box check. A successful send only means delivered, not read, so ask for a one-line "ack" when confirmation matters. Write long briefs inline, since the remote root can't read your scratchpad.
- **Remote-root contract.** A remote root (one whose master is on another machine) reports by `SendMessage` to `◎ Master`:
  1. whenever a thread's mark changes to `?`, `✓` or `✗`, and
  2. whenever the master sends `status?`.
  The report has the root's `(a/t)`, one line per thread with its mark, and the exact text of every `?` question, in the answer-per-question format. Roots that the master can `read-screen` don't need to push.
- **Status across projects:** read local roots from the screen, as above. For remote roots, use their last pushed report and say how old it is, or send `status?` and wait for the reply. Never claim a remote status you haven't received.
- **Per-machine upkeep:** the master can't run `rc-recover.sh` or rename tabs on another machine. Each root runs `rc-recover.sh` (no flags: its own workspace) on every status check and owns its titles.
- **Create a root on another machine:** ask a running remote root (or another Remote Control session on that machine) to run the "Create a project root" commands there, in that machine's cmux window, with the same folder naming. Then add it to the table as remote.
- **Move a project between machines:** the old root sends the new root, in one message, each thread's session name, mark, whether it has uncommitted or unpushed work, and any open `?` question. The new root tracks them as remote threads until they finish. Close nothing on the old machine without the user's OK. After the handoff, route that project to the new root.

## Placeholders for other machines

On the master's machine, give each other machine one sidebar folder, `⇄ <machine> (remote)`, holding one placeholder workspace per remote root, named `⌂ Root · <project> @<machine>`. A placeholder is a plain shell that prints where the root really runs and how to reach it (through the ◎ Master, or the Claude app's Remote Control list). It runs nothing else.

```bash
W=$(cmux identify | jq -r .caller.window_ref)
cmux new-workspace --name "⌂ Root · <project> @<machine>" --cwd ~ --focus false --window "$W" \
  --command "printf '\n  ⌂ Root · <project> @<machine>\n  Placeholder: this root runs on <machine>. Reach it through the ◎ Master.\n\n'; exec zsh -i"
# first one: cmux workspace-group create --name "⇄ <machine> (remote)" --from workspace:W, then set-anchor to it and close the empty anchor workspace
# others:    cmux workspace-group add --group workspace_group:G --workspace workspace:W
```

Add a placeholder when a remote root starts, and remove it when the root is retired. When a project moves to another machine, the local root's old folder stays until the user retires it.
