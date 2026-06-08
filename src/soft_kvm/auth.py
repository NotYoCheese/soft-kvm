"""Phase 2 — durable OAuth2 auth (authorization_code + refresh-token rotation).

Flow (verified against the live SmartThings docs):
  * authorize: GET {authorize_url}?client_id&response_type=code&redirect_uri&scope&state
  * token:     POST {token_url}  (HTTP Basic client_id:client_secret)
                 grant_type=authorization_code  (one-time, via `auth init`)
                 grant_type=refresh_token       (at runtime)

Each token response carries a NEW refresh token (rotation), which we persist back to
the macOS Keychain every time. The access token (valid ~24h) is also cached in the
Keychain with its expiry, so a fresh process can reuse it WITHOUT a refresh — and thus
without needing the client credentials (no ``op run``). A refresh happens only when the
cached access token is expired/missing, or on a 401 (handled in client.py).

Secrets split: static client_id/secret come from Settings (1Password-injected at
runtime); the rotating refresh token lives only in the Keychain via ``keyring``.
"""

from __future__ import annotations

import json
import time
import urllib.parse

import httpx
import keyring
import keyring.errors
from pydantic import BaseModel, ConfigDict

from .config import Settings
from .errors import ApiError, ConfigError
from .logging_setup import get_logger

log = get_logger("auth")

KEYRING_SERVICE = "soft-kvm"
KEYRING_USERNAME = "smartthings-refresh-token"
KEYRING_ACCESS_USERNAME = "smartthings-access-token"
# Refresh the access token this many seconds before its stated expiry.
EXPIRY_SAFETY_MARGIN = 30.0
_TOKEN_TIMEOUT = 15.0


class TokenResponse(BaseModel):
    """OAuth token endpoint response."""

    model_config = ConfigDict(extra="ignore")

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = 0
    scope: str | None = None


# --------------------------------------------------------------------------- #
# Keychain-backed refresh-token store
# --------------------------------------------------------------------------- #


def store_refresh_token(token: str) -> None:
    keyring.set_password(KEYRING_SERVICE, KEYRING_USERNAME, token)


def load_refresh_token() -> str | None:
    return keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME)


def clear_refresh_token() -> bool:
    """Delete the stored refresh token. Returns True if one was present."""
    try:
        keyring.delete_password(KEYRING_SERVICE, KEYRING_USERNAME)
    except keyring.errors.PasswordDeleteError:
        return False
    return True


def store_access_token(token: str, expires_at: float) -> None:
    """Cache the access token + its absolute expiry (epoch seconds) in the Keychain."""
    keyring.set_password(
        KEYRING_SERVICE,
        KEYRING_ACCESS_USERNAME,
        json.dumps({"access_token": token, "expires_at": expires_at}),
    )


def load_access_token() -> tuple[str, float] | None:
    """Return the cached ``(access_token, expires_at)``, or None if absent/corrupt."""
    raw = keyring.get_password(KEYRING_SERVICE, KEYRING_ACCESS_USERNAME)
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return str(data["access_token"]), float(data["expires_at"])
    except (ValueError, KeyError, TypeError):
        return None


def clear_access_token() -> bool:
    """Delete the cached access token. Returns True if one was present."""
    try:
        keyring.delete_password(KEYRING_SERVICE, KEYRING_ACCESS_USERNAME)
    except keyring.errors.PasswordDeleteError:
        return False
    return True


# --------------------------------------------------------------------------- #
# OAuth requests
# --------------------------------------------------------------------------- #


def build_authorize_url(settings: Settings, state: str) -> str:
    """Build the authorization URL the user opens in a browser."""
    client_id, _ = settings.require_oauth()
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": settings.smartthings_redirect_uri,
        "scope": settings.smartthings_scopes,
        "state": state,
    }
    return f"{settings.smartthings_oauth_authorize_url}?{urllib.parse.urlencode(params)}"


def _post_token(settings: Settings, data: dict[str, str]) -> TokenResponse:
    client_id, client_secret = settings.require_oauth()
    try:
        response = httpx.post(
            settings.smartthings_oauth_token_url,
            auth=(client_id, client_secret.get_secret_value()),
            data=data,
            headers={"Accept": "application/json"},
            timeout=_TOKEN_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise ApiError(f"OAuth token request failed: {exc!r}") from exc
    if not response.is_success:
        raise ApiError(
            f"OAuth token endpoint -> HTTP {response.status_code}\n{response.text[:1000]}"
        )
    return TokenResponse.model_validate(response.json())


def exchange_code(settings: Settings, code: str) -> TokenResponse:
    """Exchange a one-time authorization code for tokens; store the refresh token."""
    tokens = _post_token(
        settings,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": settings.smartthings_redirect_uri,
        },
    )
    store_refresh_token(tokens.refresh_token)
    store_access_token(tokens.access_token, time.time() + tokens.expires_in)
    log.info("auth.exchanged_code", scope=tokens.scope, expires_in=tokens.expires_in)
    return tokens


# --------------------------------------------------------------------------- #
# Runtime access-token manager
# --------------------------------------------------------------------------- #


class TokenManager:
    """Provides access tokens, reusing the Keychain-cached one and refreshing only as needed.

    The access token (and its expiry) is cached in the Keychain, so a fresh process can
    reuse a still-valid token WITHOUT a refresh — meaning the common path needs neither
    the client credentials nor ``op run``. A refresh (which needs the client credentials)
    happens only when the cached token is expired/missing or ``force_refresh`` is set.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._access_token: str | None = None
        self._expires_at = 0.0  # absolute epoch seconds

    def has_refresh_token(self) -> bool:
        return load_refresh_token() is not None

    def has_cached_access_token(self) -> bool:
        return load_access_token() is not None

    def get_access_token(self, *, force_refresh: bool = False) -> str:
        """Return a valid access token, refreshing only if expired/missing or forced."""
        if not force_refresh:
            cached = self._cached_token()
            if cached is not None:
                return cached
        return self._refresh()

    def _cached_token(self) -> str | None:
        """A still-valid access token from memory or the Keychain, else None."""
        now = time.time()
        if self._access_token and now < self._expires_at - EXPIRY_SAFETY_MARGIN:
            return self._access_token
        stored = load_access_token()
        if stored is not None and now < stored[1] - EXPIRY_SAFETY_MARGIN:
            self._access_token, self._expires_at = stored
            return stored[0]
        return None

    def _refresh(self) -> str:
        refresh_token = load_refresh_token()
        if refresh_token is None:
            raise ConfigError(
                "No stored SmartThings refresh token. Run `soft-kvm auth init` first."
            )
        # _post_token -> Settings.require_oauth raises CredentialsUnavailableError when the
        # client credentials aren't resolved (e.g. running without `op run`); the launcher
        # uses that exit code to retry under `op run`.
        tokens = _post_token(
            self._settings,
            {"grant_type": "refresh_token", "refresh_token": refresh_token},
        )
        # Rotation: SmartThings returns a fresh refresh token — persist it every time.
        store_refresh_token(tokens.refresh_token)
        self._expires_at = time.time() + tokens.expires_in
        self._access_token = tokens.access_token
        store_access_token(tokens.access_token, self._expires_at)
        log.info("auth.refreshed", expires_in=tokens.expires_in)
        return tokens.access_token
