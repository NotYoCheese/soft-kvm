"""Exceptions for soft-kvm."""

from __future__ import annotations


class SoftKvmError(Exception):
    """Base class for all soft-kvm errors that should be reported cleanly."""


class ConfigError(SoftKvmError):
    """Configuration or credentials missing/invalid."""


class ApiError(SoftKvmError):
    """A SmartThings API call returned an unexpected status or body.

    The message includes the request, status code, and response body so an
    unexpected response is surfaced rather than papered over.
    """
