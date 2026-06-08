# CLAUDE.md — soft-kvm

Project guidance for Claude Code. See `PROJECT_BRIEF.md` for the full plan and rationale.

## What this is

A macOS-resident one-shot CLI that flips **two Samsung ViewFinity S9 5K monitors**
between two host Macs (`personal` = M1 Max via CalDigit TS4 → Thunderbolt 4;
`work` = M5 Pro clamshell via USB-C→DP → Mini DisplayPort) by changing each
monitor's **input source** over the network. Mouse/keyboard follow via Logitech
Easy-Switch in hardware — **this tool owns only the video-input switch.**

## Hard constraints — settled, do NOT re-litigate

1. **The S9 has no DDC/CI.** `m1ddc`, `ddcutil`, BetterDisplay, MonitorControl, Lunar
   are all dead on arrival. Do not attempt any DDC approach.
2. **SmartThings PATs created after 30 Dec 2024 expire in 24h.** A hardcoded PAT is
   only acceptable for throwaway Phase 0 discovery. Durable auth = OAuth2
   authorization_code flow with **refresh-token rotation** (Phase 2).
3. **SmartThings does not support `client_credentials`.** No clean machine-to-machine
   token. Plan: one-time interactive auth-code → store refresh token → exchange for
   access tokens → persist the rotated refresh token on every refresh.
4. **Scope = video-input switching only.** No peripheral/USB scripting.
5. **The S9 has no usable buttons and no Source button on its remote.** Manual
   switching is painful by design — that's the reason this tool exists.

## Current status

**Gate resolved → Path B. Phases 0–2 complete and live-verified. Remaining: Phase 3
(hotkey binding) and Phase 4 (pytest suite against the fixtures).**

Phase 1 commands (idempotent + verified):
- `uv run soft-kvm status` — read each monitor's current input + mapped target.
- `uv run soft-kvm work` / `personal` — switch both monitors to that target.
- `uv run soft-kvm toggle` — read state, flip to the other target.
- `uv run soft-kvm --dry-run <cmd>` — global; report what would change, send nothing.

Phase 0 utilities (still present, for diagnostics):
- `uv run soft-kvm discover` — enumerate devices, dump capability + state, save fixtures.
- `uv run soft-kvm test-switch --device-id <id> --source "<id>"` — confirmation-gated
  single `setInputSource` with read-back verification.

Discovery results (the gate decision, deviceIds, source ids, latency) are in
`docs/FINDINGS.md`. Monitor config (deviceIds + `target → source` map) lives in the
gitignored `config/monitors.toml` (copy from `config/monitors.example.toml`; it holds
your real deviceIds so it's not committed); source ids are `"USB-C"` (personal/Thunderbolt) and
`"Display Port"` (work/Mini DP). NB: supported sources come from
`samsungvd.mediaInputSource.supportedInputSourcesMap` (`{id, name}`), not the generic
`supportedInputSources` (empty/dead on these panels).

### Phase 2 — durable auth (done, live-verified)

OAuth2 authorization_code + refresh-token rotation, in `auth.py`. One-time
`soft-kvm auth init` (manual code paste); `TokenManager` refreshes access tokens and
persists the rotated refresh token to the Keychain; `client.build_client` prefers OAuth
(refresh-on-401) and falls back to the PAT. Setup guide: `docs/PHASE2_AUTH.md`.

Hard-won specifics (don't relearn):
- Endpoints: authorize `https://api.smartthings.com/oauth/authorize`, token
  `https://api.smartthings.com/oauth/token` (NO `/v1/`). Token auth = HTTP Basic.
- **Redirect URI MUST be public HTTPS — SmartThings 403s `localhost`.** This deployment
  uses `https://mikenoe.com` (code is read from its address-bar query string).
- App is an OAuth-In / API_ONLY app made via `smartthings apps:create` (the web
  Workspace can't set a redirect URI). client_id/secret in 1Password (`op run`).
- Access token lifetime ~24h (`expires_in≈86399`); refresh tokens rotate every refresh.
- Run normal commands via `op run --env-file .env -- uv run soft-kvm <cmd>` so the
  `op://` client creds resolve (a guard errors clearly if they don't).

## Tech stack & conventions

- **Python 3.12+** (pinned 3.13), managed with **uv**. Always `uv add` / `uv run`;
  never bare `pip`, never a hand-rolled venv — uv owns `.venv`. Run uv commands with
  `VIRTUAL_ENV` unset (a different project's venv may be active in the shell).
- **src layout**, import package `soft_kvm`, distribution `soft-kvm`.
- Deps: `httpx`, `structlog`, `keyring`, `typer`, `pydantic` + `pydantic-settings`.
  Dev: `pytest`, `pytest-httpx`, `ruff`, `mypy`.
- **Type hints everywhere; mypy strict must stay clean. ruff for lint + format.**
- **Validate API responses into pydantic models**, don't index raw dicts — fail
  loudly on shape changes. (Status is capability-keyed/dynamic, so we locate the
  input-source slice defensively then validate it into a model.)
- macOS only, zsh. No cross-platform abstraction.
- Logs via structlog to **stderr**; human output (rich tables) to stdout.

## Secrets handling

- **Static secrets** (`SMARTTHINGS_CLIENT_ID`/`_SECRET`): 1Password, injected at
  runtime via `op run` / `op://` references (read-only is fine).
- **Rotating secret** (OAuth refresh token): macOS **Keychain** via `keyring`,
  because it changes on every refresh and `op://` injection is read-only.
- `.env` is gitignored. Never commit tokens. The throwaway Phase 0 PAT lives only
  in a local `.env` or the shell environment.
- **Fixtures contain no secrets** (the PAT is in a request header, never in
  responses) and are committed for the Phase 4 test suite.

## How to work on this

- **Verify current SmartThings API details against the live developer docs before
  implementing auth or capabilities** (esp. the OAuth token endpoint/scopes in
  Phase 2). The brief encodes decisions, not necessarily current URLs/IDs.
- **No DDC** — settled, dead.
- **Trace the full request/response path; surface unexpected responses**, don't
  paper over them.
- **Idempotent and verified:** an accepted command (HTTP 200) is NOT a completed
  switch. Always read the input back.
- **Capture real API responses as fixtures during Phase 0;** tests mock against
  those, never hit the live API.
- **Stop at the Phase 0 gate and ask** before choosing Path A vs B. Present what
  `supportedInputSources` returned with a recommendation and the tradeoff.
- When multiple valid implementations exist, give 2–3 options with tradeoffs.

## Commands

```bash
# All uv commands: prefix `env -u VIRTUAL_ENV` if another venv is active in the shell.
uv run soft-kvm discover                 # Phase 0 read-only discovery
uv run soft-kvm test-switch --dry-run --device-id X --source "Y"
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest
```
