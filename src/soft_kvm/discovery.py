"""Phase 0 discovery logic.

Read-only enumeration + a single, explicitly-confirmed test switch. All raw API
responses are saved to ``tests/fixtures/`` for the Phase 4 test suite. Presentation
lives in ``cli.py``; this module only does I/O and returns structured results.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from .client import request_json
from .commands import build_command_body, command_accepted
from .logging_setup import get_logger
from .models import (
    Device,
    DeviceList,
    InputSourceState,
    extract_input_source,
)

log = get_logger("discovery")


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return slug or "device"


def save_fixture(fixtures_path: Path, name: str, payload: Any) -> Path:
    """Write a payload as pretty JSON under the fixtures dir and return the path."""
    fixtures_path.mkdir(parents=True, exist_ok=True)
    out = fixtures_path / f"{name}.json"
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    log.info("fixture.saved", path=str(out))
    return out


def enumerate_devices(client: httpx.Client, fixtures_path: Path) -> DeviceList:
    """``GET /v1/devices`` — save raw response, warn on unhandled pagination, validate."""
    payload = request_json(client, "GET", "/devices")
    save_fixture(fixtures_path, "devices", payload)
    links = payload.get("_links")
    if isinstance(links, dict) and links.get("next"):
        log.warning(
            "devices.pagination_present",
            note="More than one page of devices exists; only the first page was captured.",
        )
    return DeviceList.model_validate(payload)


def get_status(
    client: httpx.Client, device_id: str, fixtures_path: Path, slug: str
) -> dict[str, Any]:
    """``GET /v1/devices/{id}/status`` — save raw response and return it."""
    payload = request_json(client, "GET", f"/devices/{device_id}/status")
    save_fixture(fixtures_path, f"status_{slug}", payload)
    return payload


def send_input_command(
    client: httpx.Client,
    device_id: str,
    capability: str,
    source: str,
    fixtures_path: Path,
    slug: str,
) -> dict[str, Any]:
    """``POST /v1/devices/{id}/commands`` — set input source, save request+response."""
    body = build_command_body(capability, source)
    response = request_json(client, "POST", f"/devices/{device_id}/commands", json=body)
    save_fixture(fixtures_path, f"command_{slug}", {"request": body, "response": response})
    return response


@dataclass
class DeviceFinding:
    """A monitor candidate and its extracted input-source state."""

    device: Device
    input_state: InputSourceState | None
    status_raw: dict[str, Any]


def run_discovery(
    client: httpx.Client, fixtures_path: Path
) -> tuple[DeviceList, list[DeviceFinding]]:
    """Enumerate devices, then fetch status for every monitor candidate."""
    devices = enumerate_devices(client, fixtures_path)
    findings: list[DeviceFinding] = []
    for device in devices.items:
        if not device.looks_like_monitor():
            continue
        raw = get_status(client, device.device_id, fixtures_path, _slug(device.display_name))
        findings.append(DeviceFinding(device, extract_input_source(raw), raw))
    log.info("discovery.complete", devices=len(devices.items), candidates=len(findings))
    return devices, findings


@dataclass
class SwitchResult:
    """Outcome of a single verified test switch."""

    device_id: str
    capability: str
    target: str
    before: str | None
    after: str | None
    accepted: bool
    already_on_target: bool
    changed: bool
    command_latency_s: float
    verify_latency_s: float | None


def switch_and_verify(
    client: httpx.Client,
    device_id: str,
    capability: str,
    source: str,
    fixtures_path: Path,
    *,
    poll_interval: float = 0.5,
    timeout: float = 12.0,
) -> SwitchResult:
    """Send one setInputSource command and poll status until it reflects the target.

    A cloud ``200`` means the command was *accepted*, not that the input switched,
    so we read the ``inputSource`` attribute back and time how long the real switch took.
    """
    slug = _slug(device_id)
    before_raw = get_status(client, device_id, fixtures_path, f"{slug}_before")
    before_state = extract_input_source(before_raw)
    before = before_state.current if before_state else None

    started = time.monotonic()
    response = send_input_command(client, device_id, capability, source, fixtures_path, slug)
    command_latency = time.monotonic() - started
    accepted = command_accepted(response)
    log.info("switch.command_sent", device_id=device_id, target=source, accepted=accepted)

    after_raw = before_raw
    after = before
    verify_latency: float | None = None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(poll_interval)
        after_raw = request_json(client, "GET", f"/devices/{device_id}/status")
        state = extract_input_source(after_raw)
        after = state.current if state else None
        if after == source:
            verify_latency = time.monotonic() - started
            break

    save_fixture(fixtures_path, f"status_{slug}_after", after_raw)
    return SwitchResult(
        device_id=device_id,
        capability=capability,
        target=source,
        before=before,
        after=after,
        accepted=accepted,
        already_on_target=before == source,
        changed=after == source and before != source,
        command_latency_s=command_latency,
        verify_latency_s=verify_latency,
    )
