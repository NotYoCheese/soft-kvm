"""Exceptions for soft-kvm."""

from __future__ import annotations


class SoftKvmError(Exception):
    """Base class for all soft-kvm errors that should be reported cleanly."""


class ConfigError(SoftKvmError):
    """Configuration or credentials missing/invalid."""


class CredentialsUnavailableError(ConfigError):
    """OAuth client credentials are needed (to refresh) but absent or unresolved.

    Raised when a token refresh is required but the client_id/secret aren't available
    — e.g. running without ``op run`` so the ``op://`` references are unresolved. The
    CLI maps this to exit code 3 so the launcher knows to retry under ``op run``. This
    is distinct from a missing refresh token (which needs ``auth init`` instead).
    """


class ApiError(SoftKvmError):
    """A SmartThings API call returned an unexpected status or body.

    The message includes the request, status code, and response body so an
    unexpected response is surfaced rather than papered over. ``status_code`` is
    set for HTTP failures (``None`` for transport/decode errors) so callers can
    react to specific statuses — e.g. 409 ``ConflictError`` meaning the panel is
    powered off and can't accept an input change.
    """

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
