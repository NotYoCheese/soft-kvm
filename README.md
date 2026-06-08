# soft-kvm

Flip two **Samsung ViewFinity S9 5K** monitors between two Macs with one command,
by changing each monitor's **input source** over the network (SmartThings). The
mouse/keyboard follow via Logitech Easy-Switch — this tool owns only the video switch.

> **Status: Phases 0–2 complete (Path B / SmartThings Cloud API).**
> `work` / `personal` / `status` / `toggle` are implemented, idempotent, and verified
> (each switch is read back). Durable **OAuth** auth (authorization_code + refresh-token
> rotation, Keychain-stored) is working — see `docs/PHASE2_AUTH.md`. Remaining: Phase 3
> (hotkey binding) and Phase 4 (test suite). See `PROJECT_BRIEF.md` for the full plan,
> `docs/FINDINGS.md` for Phase 0 results, and `CLAUDE.md` for conventions.

## Why this exists

The S9 has **no DDC/CI** (so `m1ddc`/BetterDisplay/Lunar/etc. can't switch it),
**no Source button** on its remote, and no usable physical buttons. Switching its
input manually is painful by design. SmartThings is the remaining control surface.

## Setup

Requires [`uv`](https://docs.astral.sh/uv/) and macOS.

```bash
uv sync
```

Authentication uses **OAuth** (one-time `soft-kvm auth init`, then automatic refresh) —
see **`docs/PHASE2_AUTH.md`** for the one-time setup. A throwaway `SMARTTHINGS_PAT` in
`.env` still works as a dev/discovery fallback.

## Usage

With OAuth configured, run commands through 1Password so the client credentials resolve:
`op run --env-file .env -- uv run soft-kvm …` (shown bare below for brevity).

```bash
uv run soft-kvm status          # show each monitor's current input + target
uv run soft-kvm work            # switch both monitors to the work Mac (Mini DP)
uv run soft-kvm personal        # switch both to the personal Mac (Thunderbolt/USB-C)
uv run soft-kvm toggle          # read state and flip to the other target
uv run soft-kvm --dry-run work  # report what would change; send nothing
```

Each switch is idempotent (skips a monitor already on target) and verified (the new
input is read back). If one monitor switches and the other doesn't, both states are
reported and the command exits non-zero. Monitor deviceIds and the
`target → source` mapping live in `config/monitors.toml` — copy it from
`config/monitors.example.toml` (it's gitignored, since it holds your deviceIds).

## Phase 0 — discovery

1. Generate a **throwaway** SmartThings Personal Access Token at
   <https://account.smartthings.com/tokens> with device list/see/control scopes.
   (PATs created after 30 Dec 2024 expire in 24h — that's fine, this is discovery.)
2. Copy `.env.example` to `.env` and paste the token as `SMARTTHINGS_PAT`.
   `.env` is gitignored.
3. Enumerate devices and dump each monitor's input capability + current state:

   ```bash
   uv run soft-kvm discover
   ```

4. Send one test switch to a real monitor and confirm it changes (watch the panel):

   ```bash
   uv run soft-kvm test-switch --device-id <deviceId> --source "<exact source name>"
   # add --dry-run to print the command body without sending
   ```

Raw API responses are saved under `tests/fixtures/` as recorded mocks for later
tests. The Path A/B decision is written to `docs/FINDINGS.md` after discovery.

## Development

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest
```
