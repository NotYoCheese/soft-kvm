# Phase 0 — Discovery Findings

**Date:** 2026-06-08
**Method:** Throwaway SmartThings PAT → `GET /v1/devices`, `GET /v1/devices/{id}/status`.
Raw responses captured in `tests/fixtures/`.

## Gate decision

> **Does each S9 expose its inputs as discrete, settable sources via
> `samsungvd.mediaInputSource`?**

**YES → build Path B (SmartThings Cloud API).**

Both monitors report a `samsungvd.mediaInputSource` capability with two discrete,
named, settable input sources and a readable current value. This is exactly the
deterministic set-by-name + read-back behaviour Path B needs. The brittle Path A
websocket toggle is **not** required.

**Verified end-to-end** on the Left monitor: `setInputSource("Display Port")` changed
`inputSource` from `USB-C` → `Display Port` (confirmed by read-back), then
`setInputSource("USB-C")` restored it (confirmed). Issuing a command and reading the
result back works deterministically — Path B confirmed in practice, not just on paper.

## Devices

| Label                | deviceId                               | Input capability            |
|----------------------|----------------------------------------|-----------------------------|
| Left monitor         | `aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa` | `samsungvd.mediaInputSource` |
| Right monitor | `bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb` | `samsungvd.mediaInputSource` |

Both monitors are identical in capability shape and source names.

## Input-source capability shape (important)

The usable capability is **`samsungvd.mediaInputSource`**. Its attributes are **not**
shaped like the generic capability — note the differences, they bit us once already:

- **Supported sources** live in `supportedInputSourcesMap.value` as a list of
  `{id, name}` objects (NOT a flat `supportedInputSources` string list):

  ```json
  [
    {"id": "Display Port", "name": "PC"},
    {"id": "USB-C", "name": "PC"}
  ]
  ```

- **Current source**: `inputSource.value` → an `id` string (e.g. `"USB-C"`).
- The generic **`mediaInputSource`** capability is also present but **non-functional**
  here: `supportedInputSources.value` is `[]` and `inputSource.value` is `null`. Ignore it.

So the settable source `id`s are **`"USB-C"`** and **`"Display Port"`** (the `name`
field is just a label — both read `"PC"` — and is not the command argument).
`setInputSource` takes the **id** string.

### Current state at discovery time

Both monitors: `inputSource = "USB-C"` (consistent with the M1 Max / personal Mac
driving both panels via the CalDigit TS4 at the time of capture).

## Proposed target → source mapping (to confirm via test-switch)

| Target     | Driving Mac & path                                   | S9 source id     |
|------------|------------------------------------------------------|------------------|
| `personal` | M1 Max → CalDigit TS4 → Thunderbolt 4 (USB-C conn.)  | `"USB-C"`        |
| `work`     | M5 Pro (clamshell) → USB-C→DP → Mini DisplayPort     | `"Display Port"` |

Inference: the S9 reports its Thunderbolt/USB-C port as `"USB-C"` and its Mini
DisplayPort port as `"Display Port"`. **Unverified** — the test-switch will confirm
that selecting `"Display Port"` brings up the work Mac (or "no signal" if it's asleep).
The mapping is per-monitor in config in case the two panels ever differ; here they match.

## Command shape (for Phase 1)

```json
{
  "commands": [{
    "component": "main",
    "capability": "samsungvd.mediaInputSource",
    "command": "setInputSource",
    "arguments": ["USB-C"]
  }]
}
```
`POST /v1/devices/{deviceId}/commands`. Read back via
`GET /v1/devices/{deviceId}/status` → `components.main.samsungvd.mediaInputSource.inputSource.value`.

## Latency (measured on Left monitor)

| Measurement                                   | Observed |
|-----------------------------------------------|----------|
| Command POST round-trip (HTTP 200 accepted)   | ~0.39 s  |
| Command → verified input change (read-back)   | ~1.06 s  |
| Restore command → verified change             | ~1.13 s  |

So a verified switch settles in **~1 second**. Fast enough to feel near-instant from a
hotkey, and well within the "fine for 1–2×/day" expectation. (Poll interval was 0.5 s,
so the true change time is somewhere under the reported figure.)

### State-staleness note (matters for Phase 1)

The resting `inputSource.value` can be **stale**: at discovery it read `USB-C` with a
~5h-old timestamp, but by test time the panel had been changed to `Display Port`
externally (timestamp updated). Crucially, once a `setInputSource` command lands, the
read-back reflects the new value within ~1 s with a fresh timestamp. **Implication:**
`switch(target)` should still read-then-skip for idempotency, but treat a no-op decision
as advisory and rely on the post-command read-back (not the pre-command read) for the
authoritative verification.

## Saved fixtures

- `tests/fixtures/devices.json` — `GET /v1/devices`
- `tests/fixtures/status_left_monitor.json` — Left monitor status
- `tests/fixtures/status_right_monitor.json` — Right monitor status
- `tests/fixtures/discovery_summary.json` — derived per-monitor summary
- `tests/fixtures/command_aaaaaaaa_aaaa_4aaa_8aaa_aaaaaaaaaaaa.json` — `setInputSource` request + accepted response
- `tests/fixtures/status_aaaaaaaa_aaaa_4aaa_8aaa_aaaaaaaaaaaa_before.json` — Left monitor on `USB-C`
- `tests/fixtures/status_aaaaaaaa_aaaa_4aaa_8aaa_aaaaaaaaaaaa_after.json` — Left monitor on `Display Port`

## Notes / caveats

- **Auth:** discovery used a throwaway PAT. PATs expire 24h after creation (and a
  monitor-id was pasted by mistake during setup, which produced a well-formed-but-
  rejected `401`). The durable solution is the **Path B OAuth2 refresh-token flow
  (Phase 2)** — verify the live token endpoint/scopes before implementing.
- **Idempotency:** `inputSource.value` is readable, so `switch(target)` can no-op when
  already on target — good for Path B.
- **Verification:** a cloud `200` is "accepted," not "switched." Phase 1 must read the
  input back (the `samsungvd.mediaInputSource.inputSource.value`), as the test-switch does.
