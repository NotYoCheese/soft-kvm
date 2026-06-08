#!/usr/bin/env bash
# Toggle (or set) both ViewFinity S9 monitors between the personal and work Mac.
#
# Doubles as a Raycast Script Command AND a plain launcher for Karabiner-Elements,
# Stream Deck, or a terminal. With no argument it runs `toggle` (one key, both
# directions); pass `work` / `personal` / `status` to override.
#
# Fast path: calls the CLI directly, which reuses the access token cached in the
# Keychain — no `op run`, no Touch ID. Only when that token has expired (≈ once/day)
# does the CLI exit 3, and we retry under `op run` so 1Password supplies the client
# credentials for one refresh (one Touch ID).
#
# Raycast metadata (ignored when run directly):
# @raycast.schemaVersion 1
# @raycast.title Toggle Monitors
# @raycast.mode silent
# @raycast.packageName soft-kvm
# @raycast.icon 🖥️
# @raycast.description Flip both ViewFinity S9 monitors between the personal and work Mac.
# @raycast.author Mike Noe

set -uo pipefail

# Resolve the soft-kvm repo from this script's own location (scripts/ -> repo root),
# so it works regardless of the caller's working directory and stays path-portable.
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

# GUI launchers (Raycast/Stream Deck) start with a minimal PATH — make sure the
# 1Password CLI (Homebrew) is found. The .venv entry point is self-contained.
export PATH="/opt/homebrew/bin:${PATH}"

cmd="${1:-toggle}"

# Fast path: cached access token in the Keychain → no op run, no Touch ID.
.venv/bin/soft-kvm "$cmd"
status=$?

# Exit code 3 = a refresh is needed but the op:// client creds weren't resolved.
# Retry under `op run` so 1Password supplies them (one Touch ID, ~once/day).
if [ "$status" -eq 3 ]; then
  op run --env-file .env -- .venv/bin/soft-kvm "$cmd"
  status=$?
fi

exit "$status"
