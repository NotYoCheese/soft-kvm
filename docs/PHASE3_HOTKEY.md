# Phase 3 — Hotkey Binding

Bind a single key to flip both monitors. `toggle` reads the current state and switches
to the *other* target, so one key serves both directions.

## The launcher

`scripts/toggle-monitors.sh` is the single entry point for any hotkey tool. It:

1. resolves the repo from its own location (no absolute paths baked in),
2. prepends `/opt/homebrew/bin` to `PATH` (GUI launchers start with a minimal `PATH`,
   and `op` lives there),
3. runs `op run --env-file .env -- .venv/bin/soft-kvm toggle`.

It calls the installed entry point (`.venv/bin/soft-kvm`) directly rather than
`uv run`, shaving the per-invocation resolution. Pass an argument to override:
`toggle-monitors.sh work` / `personal` / `status`.

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

A full verified two-monitor toggle is **~5 s warm** — and that's mostly the network,
not the CLI:

| Stage | ~Time |
|-------|-------|
| `op run` (warm) + CLI startup | ~0.8 s |
| OAuth token refresh | ~0.5 s |
| Two monitors: read → set → **verify** (sequential) | ~3–4 s |

The **first press after a while is slower** (~+3–4 s) as 1Password spins up its CLI
session and may ask for Touch ID. You'll *see* the monitors flip — that's the real
confirmation; the verify step just makes the CLI exit non-zero if one didn't take.

## 1Password prompts

`op run` asks for Touch ID only when the CLI session is locked; once unlocked it stays
unlocked for a while (configurable in **1Password → Settings → Developer**). So you
won't be prompted on every press. For a fully prompt-free hotkey you could use a
1Password **service-account** token or move the client credentials into the Keychain —
both trade some of the 1Password-managed safety, so only do that if the occasional
Touch ID is bothersome.

## Want it snappier?

The ~5 s is dominated by switching+verifying the two panels **sequentially**. Two
optional speedups (ask and I'll add them):

- **Parallelize the two monitors** (concurrent switch + verify) — roughly halves the
  switch time. Requires making the token refresh thread-safe (a single mutex-guarded
  refresh) so concurrent requests don't race the rotating refresh token.
- **Persist the access token in the Keychain** so most presses skip the ~0.5 s refresh
  (and, when the cached token is still valid, can even run without `op run`).
