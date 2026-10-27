#!/bin/bash
# Stop threadsd (launchd agent or start.sh instance) and remove its hooks. Keeps the token
# and data (~/.config/threadsd, ~/.local/share/threadsd); delete those by hand if wanted.
# Removing the hooks edits ~/.claude/settings.json (backup first): run this yourself.
set -uo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
LABEL=com.cmux-threads.threadsd
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null && echo "launchd agent stopped"
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
pkill -f "threadsd.py serve" && echo "threadsd stopped"
python3 "$DIR/hooks.py" uninstall "$DIR/hook.sh"
