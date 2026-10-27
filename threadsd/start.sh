#!/bin/bash
# Start (or restart) threadsd detached, from a terminal inside cmux.
# Use this while cmux's socket mode is "cmuxOnly" (the default): cmux then only accepts
# processes started inside cmux, which a launchd agent never is. A process started here
# keeps cmux access after its tab closes. See SKILL.md "threadsd".
DIR="$(cd "$(dirname "$0")" && pwd)"
LOG="$HOME/.local/share/threadsd/threadsd.log"
mkdir -p "$(dirname "$LOG")"
[ -n "${CMUX_SURFACE_ID:-}" ] || echo "warning: not inside a cmux terminal; cmux may refuse this process" >&2
pkill -f "threadsd.py serve" 2>/dev/null && sleep 1  # any copy: they share the port
nohup python3 "$DIR/threadsd.py" serve >> "$LOG" 2>&1 < /dev/null &
disown
sleep 2
tail -1 "$LOG"
