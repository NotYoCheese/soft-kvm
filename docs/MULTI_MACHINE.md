# Running soft-kvm from more than one place

soft-kvm is just a SmartThings cloud client, so it runs from any machine with internet
+ credentials. Putting it on **both** Macs (each with its own hotkey) plus a **phone
fallback** means you can always reclaim the monitors — including the case where the Mac
currently updating/rebooting has grabbed the panels and the machine you want has no
visible screen.

## The recovery model (why a hotkey on each Mac is enough)

A Raycast hotkey fires **blind** — it needs no terminal and no visible screen. So when a
machine grabs the monitors:

1. **Easy-Switch** the keyboard to the machine you want (physical button — always works).
2. Press that machine's soft-kvm **hotkey** (from muscle memory).
3. The monitors flip to that machine → now you can see it.

This works no matter what the other machine is doing, because the switch is a cloud
command; the currently-displayed input is irrelevant to sending it.

**The gap a hotkey can't cover:** right after a reboot (updates), a Mac sits at the login
screen with Raycast not yet running, so *its* hotkey is dead for that window. You recover
using the *other* Mac's hotkey — or, if both are unavailable, the **phone** (below), which
needs no Mac at all.

---

## 1. Second Mac (the work Mac)

Same steps as the first machine, with one important auth caveat.

### ⚠️ Auth caveat: refresh-token rotation

Each machine keeps its own refresh + access tokens in its own Keychain. Every refresh
**rotates** the refresh token (the old one dies). Therefore:

- **Never copy one machine's tokens to the other** — the first to refresh (~once/day)
  invalidates the other, and they ping-pong into re-auth.
- **Register a _separate_ OAuth app per machine.** SmartThings doesn't document whether it
  keeps multiple refresh tokens alive per app, so a second `auth init` against the *same*
  app *might* silently kill the first machine's token. A second app (own client_id/secret)
  gives each machine a fully independent token chain — guaranteed no interference. It's a
  2-minute `smartthings apps:create` (see `docs/PHASE2_AUTH.md`).

### Steps

```bash
git clone https://github.com/NotYoCheese/soft-kvm && cd soft-kvm && uv sync
```

1. **Copy `config/monitors.toml`** from the first Mac (it's gitignored — same deviceIds
   and `target → source` map work everywhere).
2. **Register a second OAuth-In app** via `smartthings apps:create` (redirect URI
   `https://mikenoe.com`, scopes `r:devices:* x:devices:*`) — see `docs/PHASE2_AUTH.md`.
3. Store its `client_id`/`client_secret` in a **new 1Password item**; point `.env` at it
   with `op://` references.
4. Authorize once: `op run --env-file .env -- uv run soft-kvm auth init`.
5. Add `scripts/` to Raycast and bind a hotkey (see `docs/PHASE3_HOTKEY.md`).

Everything else — Keychain access-token caching, the 409/wake-panel fix, the launcher —
comes along for free. (The work Mac is Apple Silicon, so the launcher's `/opt/homebrew/bin`
PATH line is correct.)

---

## 2. iPhone fallback (no Mac required)

Because it's all SmartThings cloud, your phone can flip the monitors with neither Mac
involved — the true "locked out of both" escape hatch. Do it all in the **SmartThings
app** (it manages its own auth; no tokens to expire, unlike an iOS Shortcut).

Monitors appear as **"Left monitor"** and **"Office Right Monitor"**; inputs are
**`USB-C`** (personal) and **`Display Port`** (work).

### Method A — Guaranteed (device card, zero setup)

The reliable escape hatch — it drives the exact capability soft-kvm uses.

1. SmartThings → tap **Left monitor**.
2. If it's **off**, power it **on** first (an asleep panel rejects input changes — the 409).
3. Set the **input/source** (often under **⋯ / Settings**) to **USB-C** or **Display Port**.
4. Repeat for **Office Right Monitor**.

### Method B — One-tap Scenes (nicer; set up once)

1. SmartThings → **Routines** tab → **＋** → **Scene** (a "manually run" routine).
2. Name it **"Monitors → Personal"**; add action **Control devices** → both monitors →
   **Power = On**, **Input source = USB-C**; **Save**.
3. Repeat for **"Monitors → Work"** with **Input source = Display Port**.
4. Reach them fast: add the SmartThings **Scenes widget** to the Home/Lock Screen; and/or
   add each scene in the Apple **Shortcuts** app (if a "Run scene" action is exposed) for
   Siri / the Action button.

> **Unknown to verify once:** whether the scene editor exposes **Input source** for the S9.
> If it doesn't, Method A is the reliable path. **Power caveat:** a *deep*-standby panel can
> still 409 even with "Power = On" in the scene; if a scene half-fires, use Method A on the
> affected panel.

---

## 3. Recovery playbook

| Situation | Fix |
|-----------|-----|
| Home Mac updated and grabbed the monitors; you want the work Mac | Easy-Switch keyboard → work Mac → press work Mac's hotkey |
| Work Mac grabbed them; you want the home Mac | Easy-Switch → home Mac → press home Mac's hotkey |
| The Mac you want is mid-reboot (hotkey not up yet) | Use the *other* Mac's hotkey, or the phone |
| Both Macs unavailable / away from the desk | Phone: SmartThings scene (Method B) or device card (Method A) |
| A panel is asleep and won't switch | soft-kvm wakes it automatically; on the phone, power it on first |

---

## Related, but separate: "phantom" displays

Switching the monitors *away* from a Mac changes the panel's **input**, not the **cable** —
so that Mac may keep treating the S9s as connected displays (windows/cursor stranded on
invisible screens). That's a macOS display-management problem, not something soft-kvm does.
The fix is **BetterDisplay's soft-disconnect** (a macOS-side feature, distinct from the DDC
input-switching the S9 can't do). Not set up yet — see the chat discussion / ask to add a
companion script.
