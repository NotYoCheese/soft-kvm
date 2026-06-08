# Phase 3 — Hotkey Binding

Bind a single key to flip both monitors. `toggle` reads the current state and switches
to the *other* target, so one key serves both directions.

## The launcher

`scripts/toggle-monitors.sh` is the single entry point for any hotkey tool. It:

1. resolves the repo from its own location (no absolute paths baked in),
2. prepends `/opt/homebrew/bin` to `PATH` (GUI launchers start with a minimal `PATH`,
   and `op` lives there),
3. runs `.venv/bin/soft-kvm toggle` **directly** — which reuses the access token cached
   in the Keychain, so the common path needs **no `op run` and no Touch ID**,
4. only if that exits `3` (cached token expired → a refresh is needed) does it retry
   under `op run --env-file .env -- …`, so 1Password supplies the client credentials for
   that one refresh.

Calling the installed entry point directly (not `uv run`) also shaves the per-invocation
resolution. Pass an argument to override: `toggle-monitors.sh work` / `personal` / `status`.

```bash
# Test it (read-only, won't switch anything):
scripts/toggle-monitors.sh status
```

## Raycast (recommended — lightest)

1. Raycast → **Settings → Extensions → Script Commands → Add Directories**, and add
   `<repo>/scripts` (this folder).
2. The **Toggle Monitors** command appears (from the `@raycast.*` headers in the script).
3. Select it and **Record Hotkey** (e.g. ⌃⌥⌘\). `@raycast.mode silent` means it runs
   with just a brief HUD — no window.

To bind separate keys for explicit directions, copy the script to
`work-monitors.sh` / `personal-monitors.sh`, change the final argument and the
`@raycast.title`, and assign hotkeys to each.

## Karabiner-Elements (alternative)

Add a complex modification that runs the launcher (adjust `from` to your key):

```json
{
  "description": "Toggle monitors (soft-kvm)",
  "manipulators": [
    {
      "type": "basic",
      "from": { "key_code": "f13" },
      "to": [
        { "shell_command": "/Users/mike/Developer/soft-kvm/scripts/toggle-monitors.sh" }
      ]
    }
  ]
}
```

Karabiner runs `shell_command` with a minimal environment; the script sets `PATH`
itself, so this works. (Karabiner is the one place an absolute path is unavoidable.)

## Stream Deck (alternative)

Add a button that executes `scripts/toggle-monitors.sh` (e.g. via the **System → Open**
action pointed at the script, or a "run shell command" plugin such as BarRaider's
Advanced Launcher).

## What to expect (latency)

A full verified two-monitor toggle is **~3–4 s warm**, almost all of it the network:

| Stage | ~Time |
|-------|-------|
| CLI startup | ~0.2 s |
| Two monitors: read → set → **verify** (sequential) | ~3–4 s |

Most presses use the cached access token, so they skip both `op run` and the token
refresh. **~Once a day** the cached token expires: that press exits 3, retries under
`op run` (≈ +0.6 s + a possible Touch ID + a ~0.5 s refresh), then proceeds. You'll
*see* the monitors flip — the verify step just makes the CLI exit non-zero if one didn't.

## 1Password prompts

Because the access token is cached in the Keychain (valid ~24 h), the hotkey only
touches 1Password when that token expires — **at most about once a day**, and even then
only if the CLI session has locked. Most presses involve no `op run` and no Touch ID at
all. You can stretch the session further under **1Password → Settings → Security →
Auto-lock**.

## Want it snappier?

The remaining ~3–4 s is switching + verifying the two panels **sequentially**. The one
optional speedup left (ask and I'll add it):

- **Parallelize the two monitors** (concurrent switch + verify) — roughly halves the
  switch time. Requires making the token refresh thread-safe (a single mutex-guarded
  refresh) so concurrent requests don't race the rotating refresh token.

(The access token is already Keychain-cached, so presses already skip the refresh and
usually `op run` entirely.)
