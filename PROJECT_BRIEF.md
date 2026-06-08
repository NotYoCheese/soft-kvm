# soft-kvm — Project Brief

## What this is

A small macOS-resident tool that flips **two Samsung ViewFinity S9 5K monitors**
between two host Macs with a single hotkey, by changing each monitor's **input
source** over the network.

- **Personal Mac (M1 Max)** drives both monitors via a CalDigit TS4 into each
  monitor's **Thunderbolt 4** input.
- **Work Mac (M5 Pro, clamshell)** drives both monitors via USB-C→DP→Mini DP into
  each monitor's **Mini DisplayPort** input.
- Realized workflow: **one hotkey switches both panels' video; the mouse follows
  via Logitech Easy-Switch.** This tool owns only the video-input switch.

## Hard constraints (already established — do NOT rediscover or re-litigate)

1. **The S9 does not support DDC/CI.** Every DDC-based approach is dead on arrival:
   `m1ddc`, `ddcutil`, BetterDisplay, MonitorControl, Lunar. Confirmed by
   BetterDisplay's own author.
2. **SmartThings PATs created after 30 Dec 2024 expire in 24 hours.** A hardcoded
   PAT is only acceptable for throwaway discovery (Phase 0). The durable solution
   must use the OAuth2 **authorization_code** flow with **refresh-token rotation**.
3. **SmartThings does not support the `client_credentials` grant.** Plan for: one-time
   interactive auth-code flow → store refresh token → exchange refresh token for
   access tokens at runtime → persist the rotated refresh token every time.
4. **Scope is video-input switching only.** Keyboard/mouse switching is hardware
   (Logitech Easy-Switch). Do not script peripheral switching.
5. **The S9 has no usable physical buttons** (rear jog nib only) and **no Source
   button on its remote.**

## Goals

- One command/hotkey switches **both** monitors to a named target (`work` | `personal`).
- **Idempotent**: read current input first; no-op if already on target.
- **Verified**: after issuing a switch, confirm the monitor actually changed (a cloud
  `200` means the command was *accepted*, not that the input switched).
- Durable auth that survives the 24h PAT expiry without manual daily re-tokening.
- Secrets never hardcoded or committed.
- Clean failure reporting when one monitor switches and the other doesn't.

## Non-goals

- No peripheral/USB switching. No DDC. Not a general-purpose SmartThings library.
- No always-on daemon required (fast one-shot CLI bound to a hotkey is the target).

## Approach — two candidate paths, with a decision gate

### Path B — SmartThings Cloud API (preferred *if* viable)
Deterministic: set input **by name**, read state back. Requires the OAuth machinery
above; cloud round-trip latency (~1–3s, fine for 1–2×/day).

### Path A — Local websocket toggle (fallback)
Samsung remote websocket API via `samsungtvws`. Pairing token does **not** expire, so
no auth maintenance. Weakness: sends *keypresses*, not state — `KEY_SOURCE` → navigate
→ `KEY_ENTER`, a brittle non-idempotent toggle that breaks if the input list/order
changes. Acceptable only as a two-input toggle if Path B is impossible.

### The gate (Phase 0 question)
**Does each S9 expose its Thunderbolt and Mini DisplayPort inputs as discrete,
settable sources via the SmartThings `samsungvd.mediaInputSource` capability?**
- **Yes** → build Path B.
- **No / empty / only generic source list** → build Path A and document in `FINDINGS.md`.

## Phased plan

### Phase 0 — Discovery & feasibility (throwaway 24h PAT)
1. User generates a temporary PAT with device scopes; paste as `SMARTTHINGS_PAT`.
2. Enumerate devices: `GET /v1/devices`. Capture the two S9 `deviceId`s and `label`s.
3. Read each device's status: `GET /v1/devices/{deviceId}/status`. Inspect
   `components.main` for `samsungvd.mediaInputSource` (and/or plain `mediaInputSource`).
   Record exact `supportedInputSources` and current `inputSource`.
4. Send one test command and **physically confirm the monitor switches.** Note latency.
5. **Resolve the gate.** Write `FINDINGS.md`: deviceIds, exact capability id, exact
   source-name strings for Thunderbolt vs Mini DP, current-state read shape, latency,
   Path A/B decision.
6. **Save raw JSON responses as test fixtures** for Phase 4.
7. **Stop and report.** Do not proceed without confirmation.

### Phase 1 — Core switch
- Mapping: `target (work|personal) → input-source-name`, per monitor (they may differ).
- `switch(target)`: loop both monitors, read current, skip if already on target, set, verify.
- CLI: `soft-kvm work|personal|status|toggle`, global `--dry-run`. structlog throughout.

### Phase 2 — Durable auth (Path B only)
- `soft-kvm auth init`: one-time interactive authorization_code flow.
- `TokenManager`: load refresh token from Keychain → POST token endpoint → cache access
  token in memory → **persist the rotated refresh token back to Keychain every refresh.**
- **Verify current OAuth endpoints/flow against live SmartThings docs before implementing.**

### Phase 3 — Hotkey integration
- Fast cold-start. Document binding via Raycast (lightest) / Karabiner / Stream Deck.
- `toggle` reads state and flips so one key serves both directions.

### Phase 4 — Hardening & tests
- pytest with Phase 0 fixtures as recorded mocks (no live API in unit tests).
- Handle: device offline, `401` (refresh once + retry), `429` (backoff), timeout, and
  **partial failure** (one switched, one didn't — report both, exit non-zero).
- Edge: monitor asleep/unreachable; command accepted but input didn't change
  (verify-then-retry-once).

## Tech stack & conventions

- **Python 3.12+**, **uv** (`uv init/add/run` — never bare pip, never hand-rolled venv).
- **src layout**, package `soft-kvm`.
- Deps: `httpx`, `structlog`, `keyring`, `typer`, `pydantic`/`pydantic-settings`.
  Dev: `pytest`, `pytest-httpx`, `ruff`, `mypy`.
- Type hints everywhere; mypy clean. ruff lint+format. Validate responses into pydantic
  models. macOS/zsh only.
- Non-secret config (deviceIds, IPs for Path A, target→source mapping) in a committed
  config file; secrets stay out.

## Secrets handling

- **Static** (`SMARTTHINGS_CLIENT_ID`/`_SECRET`): 1Password, injected via `op run`/`op://`.
- **Rotating** (OAuth refresh token): macOS Keychain via `keyring` (changes every refresh;
  `op://` is read-only). 1Password for what never changes, Keychain for what rotates.
- `.gitignore` any `.env`; never commit tokens.

## Key technical reference (verify against current SmartThings docs before coding)

**Command body** (capability id + source name come from Phase 0):
```json
{
  "commands": [{
    "component": "main",
    "capability": "samsungvd.mediaInputSource",
    "command": "setInputSource",
    "arguments": ["<exact source name from supportedInputSources>"]
  }]
}
```
`POST https://api.smartthings.com/v1/devices/{deviceId}/commands` with
`Authorization: Bearer <access_token>`.

**Read state**: `GET /v1/devices/{deviceId}/status` →
`components.main.samsungvd.mediaInputSource.inputSource.value`.

**Token refresh** (confirm endpoint/params against live docs):
```
POST <SmartThings OAuth token endpoint>
  auth: (client_id, client_secret)   # HTTP Basic
  body: grant_type=refresh_token & refresh_token=<current>
→ returns a NEW access_token AND a NEW refresh_token (rotated — persist it)
```
Refresh tokens expire after ~30 days idle; daily use keeps them alive. If expired,
`soft-kvm auth init` re-runs the one-time flow.

## How Claude Code should work on this

- **Verify current API details against the live SmartThings developer docs** before
  implementing auth or capabilities.
- **Do not pursue DDC approaches** — settled, dead.
- **Trace the full request/response path**; surface unexpected responses.
- **Capture real API responses as fixtures** during Phase 0; tests mock against those.
- **Idempotent and verified**: never treat an accepted command as a completed switch.
- **Stop at the Phase 0 gate and ask** before choosing Path A vs B.
- When multiple valid implementations exist, give 2–3 options with tradeoffs.
