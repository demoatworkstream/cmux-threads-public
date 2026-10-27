#!/usr/bin/env bash
# Re-enable Remote Control on every Claude session in cmux whose recent screen shows
# "Remote Control disconnected" (e.g. after switching the signed-in claude.ai account).
# Usage: rc-recover.sh [--dry-run] [--all-workspaces]   (default: caller's workspace only)
dry=0; all=0
for a in "$@"; do case $a in --dry-run) dry=1;; --all-workspaces) all=1;; esac; done
tree=$(cmux tree --all)
here_ws=$(echo "$tree" | awk '/workspace workspace:/{w=$3} /◀ here/{print w}')
echo "$tree" | awk '/workspace workspace:/{w=$3} /surface surface:[0-9]+ \[terminal\]/{for(i=1;i<=NF;i++) if($i ~ /^surface:/) print $i, w}' |
while read -r s ws; do
  [ $all -eq 0 ] && [ "$ws" != "$here_ws" ] && continue
  out=$(cmux read-screen --surface "$s" --workspace "$ws" --lines 150 2>/dev/null) || continue
  # Only act if the disconnect notice is the latest Remote Control event on screen.
  last=$(echo "$out" | grep -E 'Remote Control (disconnected|active|connected)|/remote-control is active' | tail -1)
  echo "$last" | grep -q 'Remote Control disconnected' || continue
  name=$(echo "$out" | grep -E '^─.* ─$' | tail -1 | sed -E 's/^─+ (.*) ─$/\1/')
  # Text left in the input box after the ❯ (ignoring spaces, incl. non-breaking).
  typed=$(echo "$out" | grep '^❯' | tail -1 | sed -e 's/^❯//' -e 's/\xc2\xa0//g' | tr -d '[:space:]')
  if [ -n "$typed" ]; then
    echo "SKIP $s $ws [$name]: input box not empty"; continue
  fi
  echo "RECOVER $s $ws [$name]"
  [ $dry -eq 1 ] && continue
  if [ -n "$name" ]; then cmux send --surface "$s" --workspace "$ws" "/remote-control $name
" >/dev/null
  else cmux send --surface "$s" --workspace "$ws" "/remote-control
" >/dev/null; fi
done
