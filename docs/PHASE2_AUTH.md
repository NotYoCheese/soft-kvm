# Phase 2 — Durable OAuth Auth Setup

Replaces the throwaway 24h PAT with an OAuth2 `authorization_code` flow plus
refresh-token rotation. Static client credentials live in **1Password**; the
rotating refresh token lives in the **macOS Keychain** (via `keyring`).

## Why the SmartThings CLI (not the web Workspace)

This tool needs an **OAuth-In app** (SmartThings' term for an API-access app) with a
**custom redirect URI** and device scopes. The web Developer Workspace's "Automation"
project type does **not** expose a redirect-URI field for this case, so the documented
path is the SmartThings **CLI** — `smartthings apps:create` creates an OAuth-In app
directly from prompts. Verified against the SmartThings docs + CLI 2.x, June 2026.

> ⚠️ **The redirect URI must be a public HTTPS URL you control — NOT `localhost`.**
> SmartThings' gateway returns `403 Forbidden` at `/oauth/authorize` for any `localhost`
> redirect URI. This guide uses `https://mikenoe.com`; substitute your own domain. The
> flow still just has you copy the `code` from the redirect URL's query string.

## 1. Install the SmartThings CLI

```bash
npm install -g @smartthings/cli
# (or via the Homebrew tap if you prefer)
smartthings --version
```

## 2. Create the OAuth-In app

```bash
smartthings apps:create
```

On first run the CLI opens a browser to log into your SmartThings account and
authorize the CLI itself — approve it. Then answer the prompts:

- **Display Name:** `soft-kvm`
- **Description:** `Switch ViewFinity S9 monitors between two Macs`
- **Icon Image URL / Target URL:** leave blank
- **Scopes:** select `r:devices:*` and `x:devices:*` (space to toggle, enter to confirm)
- **Redirect URIs:** add a public HTTPS URL you control, e.g. `https://mikenoe.com` (NOT localhost)

The CLI prints the **`OAuth Client Id`** and **`OAuth Client Secret`** once — copy both
now (the secret is not shown again).

## 3. Store the client credentials in 1Password

Create an item (e.g. *"SmartThings soft-kvm"*) with fields `client_id` and
`client_secret`, then reference them from `.env` and run via `op run` so they're
injected at runtime (never written to disk):

```dotenv
SMARTTHINGS_CLIENT_ID=op://Private/SmartThings soft-kvm/client_id
SMARTTHINGS_CLIENT_SECRET=op://Private/SmartThings soft-kvm/client_secret
```

(For dev only, you can put the literal values in `.env` instead.)

## 4. Authorize once

```bash
op run --env-file .env -- uv run soft-kvm auth init
# (or, if creds are literal in .env: uv run soft-kvm auth init)
```

It opens an authorize URL in your browser. Approve access; you'll be redirected to
`https://mikenoe.com/?code=…&state=…`. Copy the `code` from the **browser address bar**
(or paste the whole redirect URL) into the prompt. The refresh token is stored in the
macOS Keychain.

## 5. Use it

```bash
op run --env-file .env -- uv run soft-kvm work
op run --env-file .env -- uv run soft-kvm toggle
```

soft-kvm now uses OAuth automatically (the PAT remains a dev fallback). The access
token is cached **in the Keychain** for its ~24 h lifetime, so most commands reuse it
without a refresh — and without needing `op run`. A refresh (rotating + re-storing the
refresh token) happens only when the cached token expires or a `401` forces it.

## Maintenance

- Refresh tokens last ~30 days idle; daily use keeps them alive. If one expires,
  re-run `soft-kvm auth init`.
- `soft-kvm auth status` — show whether creds + a stored refresh token are present.
- `soft-kvm auth logout` — delete the stored refresh + access tokens from the Keychain.
