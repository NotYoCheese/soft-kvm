"""Thin HTTP client for the SmartThings REST API.

``build_client`` prefers Phase 2 OAuth (access token via the refresh-token flow,
with refresh-on-401 retry) when credentials + a stored refresh token are present,
and falls back to the Phase 0/1 throwaway PAT otherwise. ``request_json`` surfaces
unexpected responses loudly rather than papering over them.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

import httpx

from .auth import TokenManager
from .config import Settings
from .errors import ApiError, ConfigError

DEFAULT_TIMEOUT = 15.0

# Actionable hints for the failure modes the brief calls out (Phase 4 will turn
# these into retry/backoff behaviour; for discovery we just report clearly).
_STATUS_HINTS: dict[int, str] = {
    401: (
        "Unauthorized — the PAT is missing, malformed, or expired. "
        "PATs created after 30 Dec 2024 expire 24h after creation."
    ),
    403: (
        "Forbidden — the PAT is valid but lacks the required scopes. "
        "Need at least: list/see devices (r:devices:*) and control (x:devices:*)."
    ),
    429: "Rate limited — back off before retrying.",
}


class _OAuthAuth(httpx.Auth):
    """httpx auth flow backed by a TokenManager, with refresh-on-401-retry.

    A 401 means the cached access token expired or was revoked; we force a refresh
    (which also rotates and persists the refresh token) and retry the request once.
    """

    def __init__(self, token_manager: TokenManager) -> None:
        self._tm = token_manager

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response]:
        request.headers["Authorization"] = f"Bearer {self._tm.get_access_token()}"
        response = yield request
        if response.status_code == 401:
            request.headers["Authorization"] = (
                f"Bearer {self._tm.get_access_token(force_refresh=True)}"
            )
            yield request


def build_client(settings: Settings, *, timeout: float = DEFAULT_TIMEOUT) -> httpx.Client:
    """Build an httpx client bound to the SmartThings API.

    Prefers Phase 2 OAuth (when client credentials are configured AND a refresh token
    has been stored via ``soft-kvm auth init``); otherwise falls back to the Phase 0/1
    PAT. Raises :class:`ConfigError` if neither is available.
    """
    if settings.has_oauth_credentials():
        token_manager = TokenManager(settings)
        if token_manager.has_refresh_token():
            return httpx.Client(
                base_url=settings.smartthings_api_base,
                auth=_OAuthAuth(token_manager),
                headers={"Accept": "application/json"},
                timeout=timeout,
            )

    pat = settings.smartthings_pat
    if pat is not None and pat.get_secret_value():
        return httpx.Client(
            base_url=settings.smartthings_api_base,
            headers={
                "Authorization": f"Bearer {pat.get_secret_value()}",
                "Accept": "application/json",
            },
            timeout=timeout,
        )

    raise ConfigError(
        "No SmartThings credentials available. Either run `soft-kvm auth init` "
        "(durable OAuth) or set SMARTTHINGS_PAT (throwaway, for dev/discovery)."
    )


def _format_http_error(resp: httpx.Response) -> str:
    hint = _STATUS_HINTS.get(resp.status_code, "")
    body = resp.text[:1000]
    suffix = f"\nHint: {hint}" if hint else ""
    return f"{resp.request.method} {resp.request.url} -> HTTP {resp.status_code}\n{body}{suffix}"


def request_json(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    json: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Perform a request and return the JSON object, surfacing failures loudly.

    Raises :class:`ApiError` on transport errors, non-2xx responses (including the
    response body), non-JSON bodies, or JSON that isn't an object.
    """
    try:
        resp = client.request(method, url, json=json)
    except httpx.HTTPError as exc:
        raise ApiError(f"{method} {url} request failed: {exc!r}") from exc

    if not resp.is_success:
        raise ApiError(_format_http_error(resp))

    try:
        data = resp.json()
    except ValueError as exc:
        raise ApiError(
            f"{method} {url} returned a non-JSON body "
            f"(HTTP {resp.status_code}): {resp.text[:500]!r}"
        ) from exc

    if not isinstance(data, dict):
        raise ApiError(f"{method} {url} returned JSON that is not an object: {type(data).__name__}")
    return data
