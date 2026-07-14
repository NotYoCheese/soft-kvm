"""Shared SmartThings command helpers (used by discovery and the switcher)."""

from __future__ import annotations

from typing import Any


def build_command_body(capability: str, source: str) -> dict[str, Any]:
    """The ``setInputSource`` command body for a given capability + source id."""
    return {
        "commands": [
            {
                "component": "main",
                "capability": capability,
                "command": "setInputSource",
                "arguments": [source],
            }
        ]
    }


def build_switch_command(*, on: bool) -> dict[str, Any]:
    """The ``switch`` capability on/off command body.

    An asleep S9 can reject ``setInputSource`` with HTTP 409 ("invalid device state"),
    so the switcher wakes a panel that reports ``switch: off`` before changing its input
    (and retries once after a power-on if a 409 shows up anyway).
    """
    return {
        "commands": [
            {
                "component": "main",
                "capability": "switch",
                "command": "on" if on else "off",
                "arguments": [],
            }
        ]
    }


def command_accepted(response: dict[str, Any]) -> bool:
    """Whether the command POST was accepted by the cloud (NOT that the input changed).

    SmartThings replies with ``{"results": [{"status": "ACCEPTED"|"COMPLETED", ...}]}``.
    A cloud acceptance is not proof the input switched — callers must read it back.
    """
    results = response.get("results")
    if isinstance(results, list) and results:
        statuses = [r.get("status") for r in results if isinstance(r, dict)]
        if statuses:
            return all(status in {"ACCEPTED", "COMPLETED"} for status in statuses)
    # 2xx with an unexpected body shape: treat as accepted but the caller still verifies.
    return True
