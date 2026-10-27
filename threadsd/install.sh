#!/bin/bash
# Install threadsd. Safe to re-run (keeps the token, restarts the daemon).
#   install.sh           daemon only
#   install.sh --hooks   also add the Claude Code hooks to ~/.claude/settings.json
#                        (backup first; existing hooks kept). Run that one yourself:
#                        settings.json is deny-listed for Claude's Edit/Write tools.
#
# Where it runs depends on cmux's socket mode (cmux Settings > Automation, or
# automation.socketControlMode in ~/.config/cmux/cmux.json):
#   cmuxOnly (default)  only processes started inside cmux may use cmux, and a launchd agent
#                       never is. So threadsd is started detached from this cmux terminal
#                       (start.sh). It survives the tab closing, but not a logout or reboot:
#                       run start.sh again then (roots do it on their status check).
#   password            the launchd agent (KeepAlive, starts at login) reads the password from
#                       ~/.config/threadsd/cmux-password (0600).
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
LABEL=com.cmux-threads.threadsd
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
TS=/Applications/Tailscale.app/Contents/MacOS/Tailscale
command -v tailscale >/dev/null 2>&1 && TS=$(command -v tailscale)
[ -x "$TS" ] || { echo "Tailscale is not installed. Install it from https://tailscale.com/download and sign in." >&2; exit 1; }
IP=$(/sbin/ifconfig | awk '/^utun/{u=1} /^[a-z]/ && !/^utun/{u=0} u && /inet 100\./{print $2; exit}')
[ -n "$IP" ] || { echo "Tailscale is installed but not up (no 100.x address). Open Tailscale and sign in, then re-run." >&2; exit 1; }
PY=$(command -v python3)
mkdir -p "$HOME/.local/share/threadsd/spool" "$HOME/Library/LaunchAgents"
chmod 700 "$HOME/.local/share/threadsd"
chmod +x "$DIR/hook.sh" "$DIR/threadsd.py" "$DIR/start.sh"
[ "${1:-}" = "--hooks" ] && python3 "$DIR/hooks.py" install "$DIR/hook.sh"

MODE=$(cmux capabilities 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("access_mode",""))' 2>/dev/null || true)
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
if [ "$MODE" = "cmuxOnly" ] && [ ! -s "$HOME/.config/threadsd/cmux-password" ]; then
  rm -f "$PLIST"
  echo "cmux socket mode is cmuxOnly: starting threadsd from this cmux terminal (no launchd agent)."
  "$DIR/start.sh"
else
  sed -e "s#@PYTHON@#$PY#" -e "s#@DIR@#$DIR#" -e "s#@HOME@#$HOME#g" "$DIR/$LABEL.plist.in" > "$PLIST"
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
  echo "threadsd installed as launchd agent $LABEL (cmux socket mode: ${MODE:-unknown})."
fi
for i in $(seq 1 20); do [ -s "$HOME/.config/threadsd/token" ] && break; sleep 0.5; done
echo "threadsd: http://$IP:7878  (token: ~/.config/threadsd/token, log: ~/.local/share/threadsd/threadsd.log)"
echo "check:    curl -H \"Authorization: Bearer \$(cat ~/.config/threadsd/token)\" http://$IP:7878/healthz"
