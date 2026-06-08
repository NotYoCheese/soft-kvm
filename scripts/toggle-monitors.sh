#!/usr/bin/env bash
# Toggle (or set) both ViewFinity S9 monitors between the personal and work Mac.
#
# Doubles as a Raycast Script Command AND a plain launcher for Karabiner-Elements,
# Stream Deck, or a terminal. With no argument it runs `toggle` (one key, both
# directions); pass `work` / `personal` / `status` to override.
#
# Raycast metadata (ignored when run directly):
# @raycast.schemaVersion 1
# @raycast.title Toggle Monitors
# @raycast.mode silent
# @raycast.packageName soft-kvm
# @raycast.icon 🖥️
# @raycast.description Flip both ViewFinity S9 monitors between the personal and work Mac.
# @raycast.author Mike Noe

set -euo pipefail

# Resolve the soft-kvm repo from this script's own location (scripts/ -> repo root),
# so it works regardless of the caller's working directory and stays path-portable.
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

# GUI launchers (Raycast/Stream Deck) start with a minimal PATH — make sure the
# 1Password CLI (Homebrew) is found. The .venv entry point is self-contained.
export PATH="/opt/homebrew/bin:${PATH}"

# `op run` resolves the op:// client credentials from 1Password into the environment;
# calling the installed entry point directly skips uv's per-invocation resolution.
exec op run --env-file .env -- .venv/bin/soft-kvm "${1:-toggle}"
