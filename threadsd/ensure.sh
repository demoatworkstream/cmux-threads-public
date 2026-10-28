#!/bin/bash
# Make sure threadsd is running: start it with start.sh if /healthz doesn't answer.
# The master and every root run this on each status check (SKILL.md "Check status"),
# so threadsd comes back once cmux has brought the Claude sessions back after a reboot or crash.
# Does nothing when threadsd isn't installed on this Mac or Tailscale is down.
DIR="$(cd "$(dirname "$0")" && pwd)"
python3 "$DIR/threadsd.py" health
case $? in
  0|2|3) exit 0 ;;
esac
LOCK="$HOME/.local/share/threadsd/ensure.lock"
mkdir -p "$(dirname "$LOCK")"
find "$LOCK" -maxdepth 0 -mmin +2 -exec rmdir {} \; 2>/dev/null  # a stale lock from a killed run
mkdir "$LOCK" 2>/dev/null || { echo "another session is starting threadsd"; exit 0; }
trap 'rmdir "$LOCK"' EXIT
echo "starting threadsd"
"$DIR/start.sh"
sleep 2
python3 "$DIR/threadsd.py" health
