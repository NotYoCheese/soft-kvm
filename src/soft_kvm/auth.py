"""Phase 2 — durable OAuth2 auth (authorization_code + refresh-token rotation).

Flow (verified against the live SmartThings docs):
  * authorize: GET {authorize_url}?client_id&response_type=code&redirect_uri&scope&state
  * token:     POST {token_url}  (HTTP Basic client_id:client_secret)
                 grant_type=authorization_code  (one-time, via `auth init`)
                 grant_type=refresh_token       (at runtime)

Each token response carries a NEW refresh token (rotation), which we persist back to
the macOS Keychain every time. Access tokens are cached in memory for their stated
``expires_in`` (minus a safety margin); a 401 at the API also triggers a refresh
(handled in client.py).

Secrets split: static client_id/secret come from Settings (1Password-injected at
runtime); the rotating refresh token lives only in the Keychain via ``keyring``.
"""

from __future__ import annotations

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
    log.info("auth.exchanged_code", scope=tokens.scope, expires_in=tokens.expires_in)
    return tokens


# --------------------------------------------------------------------------- #
# Runtime access-token manager
# --------------------------------------------------------------------------- #


class TokenManager:
    """Provides access tokens, refreshing (and rotating the refresh token) as needed."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._access_token: str | None = None
        self._expires_at = 0.0  # time.monotonic() deadline

    def has_refresh_token(self) -> bool:
        return load_refresh_token() is not None

    def get_access_token(self, *, force_refresh: bool = False) -> str:
        """Return a valid access token, refreshing if expired or forced."""
        if not force_refresh and self._access_token and time.monotonic() < self._expires_at:
            return self._access_token
        return self._refresh()

    def _refresh(self) -> str:
        refresh_token = load_refresh_token()
        if refresh_token is None:
            raise ConfigError(
                "No stored SmartThings refresh token. Run `soft-kvm auth init` first."
            )
        tokens = _post_token(
            self._settings,
            {"grant_type": "refresh_token", "refresh_token": refresh_token},
        )
        # Rotation: SmartThings returns a fresh refresh token — persist it every time.
        store_refresh_token(tokens.refresh_token)
        self._access_token = tokens.access_token
        self._expires_at = time.monotonic() + max(0.0, tokens.expires_in - EXPIRY_SAFETY_MARGIN)
        log.info("auth.refreshed", expires_in=tokens.expires_in)
        return tokens.access_token
