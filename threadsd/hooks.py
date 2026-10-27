#!/usr/bin/env python3
"""Add or remove the threadsd hooks in ~/.claude/settings.json, keeping every other hook.
Backs the file up to settings.json.bak-<timestamp> before writing.
Usage: hooks.py install|uninstall <path to hook.sh>"""
import json, os, shutil, sys, time

EVENTS = ["SessionStart", "UserPromptSubmit", "Stop", "Notification"]
TOOL_EVENTS = ["PreToolUse", "PostToolUse"]  # only for AskUserQuestion: the open dialog's questions
MARK = "threadsd/hook.sh"
path = os.path.expanduser("~/.claude/settings.json")
action, hook = sys.argv[1], os.path.abspath(sys.argv[2])

with open(path) as f:
    s = json.load(f)
hooks = s.setdefault("hooks", {})
before = json.dumps(s, sort_keys=True)
for ev in list(hooks):
    groups = []
    for g in hooks[ev]:
        g = dict(g, hooks=[h for h in g.get("hooks", []) if MARK not in h.get("command", "")])
        if g["hooks"]:
            groups.append(g)
    hooks[ev] = groups
    if not groups:
        del hooks[ev]
if action == "install":
    for ev in EVENTS:
        hooks.setdefault(ev, []).append(
            {"hooks": [{"type": "command", "command": f'"{hook}" {ev}', "timeout": 5}]})
    for ev in TOOL_EVENTS:
        hooks.setdefault(ev, []).append(
            {"matcher": "AskUserQuestion", "hooks": [{"type": "command", "command": f'"{hook}" {ev}', "timeout": 5}]})
if json.dumps(s, sort_keys=True) == before:
    print("settings.json: no change")
    sys.exit(0)
bak = f"{path}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
shutil.copy2(path, bak)
tmp = path + ".threadsd.tmp"
with open(tmp, "w") as f:
    json.dump(s, f, indent=2, ensure_ascii=False)
    f.write("\n")
os.replace(tmp, path)
print(f"settings.json: hooks {action}ed (backup: {bak})")
