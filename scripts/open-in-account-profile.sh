#!/usr/bin/env bash
# Open a URL in the Chrome profile signed in to the same account as Claude Code.
#
# Claude Code opens artifacts with "$BROWSER <url>" (or `open <url>`), which lands in
# whichever Chrome profile was used last. Artifacts are private to the account that
# published them, so the wrong profile shows "Page not found". This picks the Chrome
# profile whose Google account matches ~/.claude.json's oauthAccount.emailAddress.
#
# Enable: set BROWSER to this script in ~/.claude/settings.json "env" (see ui/artifacts.md).
# Test:   open-in-account-profile.sh --dry-run https://claude.ai/artifact/x
set -euo pipefail

dry=0
if [[ "${1:-}" == "--dry-run" ]]; then dry=1; shift; fi
url="${1:?usage: open-in-account-profile.sh [--dry-run] <url>}"

profile="$(/usr/bin/python3 - "$url" <<'EOF' 2>/dev/null || true
import json, os, sys
from urllib.parse import urlparse
host = urlparse(sys.argv[1]).hostname or ""
if not (host == "claude.ai" or host.endswith(".claude.ai")):
    sys.exit(0)  # not a Claude page: no profile, use the default opener
cfg = os.environ.get("CLAUDE_CONFIG_DIR")
cj = os.path.join(cfg, ".claude.json") if cfg else os.path.expanduser("~/.claude.json")
email = json.load(open(cj)).get("oauthAccount", {}).get("emailAddress", "").lower()
state = os.path.expanduser("~/Library/Application Support/Google/Chrome/Local State")
cache = json.load(open(state))["profile"]["info_cache"]
for d, info in cache.items():
    if email and info.get("user_name", "").lower() == email:
        print(d)
        break
EOF
)"

if [[ -n "$profile" ]]; then
  cmd=(open -na "Google Chrome" --args "--profile-directory=$profile" "$url")
else
  cmd=(open "$url")
fi
if (( dry )); then printf '%q ' "${cmd[@]}"; echo; else "${cmd[@]}"; fi
