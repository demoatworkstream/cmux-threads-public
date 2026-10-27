#!/bin/bash
# Claude Code hook for threadsd: spool the event payload and exit 0, always.
# No parsing and no daemon round-trip, so it never blocks or fails a session.
# Usage (settings.json): hook.sh <EventName>   (payload JSON on stdin)
d="$HOME/.local/share/threadsd/spool"
[ -d "$d" ] || mkdir -p "$d" 2>/dev/null || exit 0
f="$d/$(date +%s).$$.$RANDOM"
{
  printf '{"event":"%s","ts":%s,"surface":"%s","workspace":"%s","payload":' \
    "${1:-}" "$(date +%s)" "${CMUX_SURFACE_ID:-}" "${CMUX_WORKSPACE_ID:-}"
  p=$(cat)
  printf '%s}\n' "${p:-null}"
} > "$f.tmp" 2>/dev/null && mv "$f.tmp" "$f.json" 2>/dev/null
exit 0
