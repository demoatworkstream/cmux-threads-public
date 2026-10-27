#!/usr/bin/env bash
# Create a project root the standard way, in one step:
#   workspace "⌂ Root · <project>" running Claude, in its own sidebar folder "⌂ <project>",
#   with the root as the folder's anchor and no leftover empty anchor workspace.
#
# Usage: new-root.sh <project> <dir> <root-prompt-file> [--window window:N]
#   <project>  the directory basename, e.g. aka
#   --window   defaults to the caller's window (the master's), not the focused one
#
# Run it from a cmux terminal (cmux's socket only accepts processes started inside cmux).
set -euo pipefail

usage() { echo "usage: new-root.sh <project> <dir> <root-prompt-file> [--window window:N]" >&2; exit 2; }
[[ $# -ge 3 ]] || usage
project="$1"; dir="$2"; prompt="$3"; shift 3
window=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --window) window="${2:?}"; shift 2 ;;
    *) usage ;;
  esac
done
[[ -f "$prompt" ]] || { echo "prompt file not found: $prompt" >&2; exit 1; }
command -v jq >/dev/null || { echo "needs jq" >&2; exit 1; }

name="⌂ Root · $project"
folder="⌂ $project"
[[ -n "$window" ]] || window="$(cmux identify | jq -r .caller.window_ref)"

if cmux workspace-group list --json | jq -e --arg n "$folder" '.groups[] | select(.name == $n)' >/dev/null; then
  echo "a folder named \"$folder\" already exists; not creating a second root" >&2; exit 1
fi

mkdir -p "$dir"

# 1. The root workspace. --command runs in a shell, so $(cat …) is expanded there.
out="$(cmux workspace create --name "$name" --cwd "$dir" --focus false --window "$window" \
  --command "claude --dangerously-skip-permissions --remote-control \"$name\" -n \"$name\" \"\$(cat $(printf '%q' "$prompt"))\"")"
ws="$(grep -oE 'workspace:[0-9]+' <<<"$out" | head -1)"
[[ -n "$ws" ]] || { echo "new-workspace didn't print a workspace ref: $out" >&2; exit 1; }
echo "root workspace: $ws"

# 2. Its folder. `create --from` also makes an empty anchor workspace, which becomes the header.
out="$(cmux workspace-group create --name "$folder" --from "$ws")"
group="$(grep -oE 'workspace_group:[0-9]+' <<<"$out" | head -1)"
[[ -n "$group" ]] || { echo "workspace-group create didn't print a group ref: $out" >&2; exit 1; }
echo "folder: $group"

# 3. Make the root the anchor, then close the empty one.
extra="$(cmux workspace-group list --json | jq -r --arg g "$group" --arg w "$ws" \
  '.groups[] | select(.ref == $g) | .member_workspace_refs[] | select(. != $w)')"
cmux workspace-group set-anchor --group "$group" --workspace "$ws" >/dev/null
for x in $extra; do
  surfaces="$(cmux tree --workspace "$x" --json | jq '[.windows[].workspaces[].panes[].surface_count] | add // 0')"
  if [[ "$surfaces" -le 1 ]]; then
    cmux workspace close "$x" >/dev/null && echo "closed empty anchor: $x"
  else
    echo "left $x open: it has $surfaces surfaces, check it by hand" >&2
  fi
done

# 4. Check: one folder with 1 member, and no stray workspace named like the folder.
line="$(cmux workspace-group list | grep -F "$group " || true)"
echo "check: $line"
[[ "$line" == *"(1 members)"* ]] || { echo "FAIL: expected (1 members)" >&2; exit 1; }
if cmux tree --all | grep -F "workspace " | grep -qF "\"$folder\""; then
  echo "FAIL: a separate \"$folder\" workspace still exists" >&2; exit 1
fi
echo "ok: $name in folder $folder. Next: read its screen and handle the trust prompt."
