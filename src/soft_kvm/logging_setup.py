"""structlog configuration.

Logs render to stderr so stdout stays clean for the discovery tables/JSON.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

_LEVELS = {
    "critical": logging.CRITICAL,
    "error": logging.ERROR,
    "warning": logging.WARNING,
    "info": logging.INFO,
    "debug": logging.DEBUG,
}


def configure_logging(level: str = "info") -> None:
    """Configure structlog for human-readable console output on stderr."""
    numeric_level = _LEVELS.get(level.lower(), logging.INFO)
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> Any:
    """Return a bound structlog logger.

    Typed as ``Any`` because structlog's bound-logger type is resolved dynamically
    from the configured ``wrapper_class``; pinning a concrete type here would lie.
    """
    return structlog.get_logger(name)
