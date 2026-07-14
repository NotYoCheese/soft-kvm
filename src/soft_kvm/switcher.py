"""Phase 1 core switch.

Set both monitors to a named target (``work`` | ``personal``), idempotently and
verified: read each monitor's current input, skip if already on target, otherwise
issue ``setInputSource`` and read the value back to confirm the panel actually
changed. A cloud ``200`` is "accepted," never "switched."

Power: an asleep panel can reject ``setInputSource`` with HTTP 409 ("invalid device
state") — observed after the driving Mac had been asleep for hours and both panels were
``switch: off``. NB: powering a panel off via the API does NOT by itself reproduce the
409 (that state still accepts input changes), so the exact trigger is a deeper standby
we can't force on demand. Mitigation, belt and braces: read the power state and wake a
panel that is off before changing its input, AND treat a 409 as "asleep" — power on and
retry the input change once — rather than as a hard failure.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, replace

import httpx

from .client import request_json
from .commands import build_command_body, build_switch_command, command_accepted
from .errors import ApiError
from .logging_setup import get_logger
from .models import extract_input_source, extract_power
from .monitors import KvmConfig, MonitorConfig

log = get_logger("switcher")

DEFAULT_POLL_INTERVAL = 0.5
DEFAULT_VERIFY_TIMEOUT = 8.0
DEFAULT_POWER_TIMEOUT = 10.0


@dataclass
class DeviceState:
    """A monitor's current input source and power state."""

    input_source: str | None
    power: str | None

    @property
    def is_off(self) -> bool:
        return self.power == "off"


def read_state(client: httpx.Client, device_id: str) -> DeviceState:
    """Read a monitor's current input source id and power state in one request."""
    status = request_json(client, "GET", f"/devices/{device_id}/status")
    state = extract_input_source(status)
    return DeviceState(
        input_source=state.current if state else None,
        power=extract_power(status),
    )


def read_current(client: httpx.Client, device_id: str) -> str | None:
    """Read a monitor's current input source id (``None`` if unavailable/offline)."""
    return read_state(client, device_id).input_source


def set_source(client: httpx.Client, device_id: str, capability: str, source: str) -> bool:
    """Issue ``setInputSource`` and return whether the cloud accepted it."""
    body = build_command_body(capability, source)
    response = request_json(client, "POST", f"/devices/{device_id}/commands", json=body)
    return command_accepted(response)


def power_on(
    client: httpx.Client,
    device_id: str,
    *,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    timeout: float = DEFAULT_POWER_TIMEOUT,
) -> bool:
    """Turn a panel on and poll until it reports ``on``. Returns whether it confirmed."""
    request_json(
        client, "POST", f"/devices/{device_id}/commands", json=build_switch_command(on=True)
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(poll_interval)
        if read_state(client, device_id).power == "on":
            return True
    return False


def verify_source(
    client: httpx.Client,
    device_id: str,
    source: str,
    *,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    timeout: float = DEFAULT_VERIFY_TIMEOUT,
) -> tuple[bool, str | None]:
    """Poll the read-back until it reports ``source``. Returns (verified, last_seen)."""
    current: str | None = None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(poll_interval)
        current = read_current(client, device_id)
        if current == source:
            return True, current
    return False, current


@dataclass
class MonitorSwitch:
    """Result of attempting to bring one monitor to a target."""

    name: str
    device_id: str
    target: str
    desired_source: str
    before: str | None
    after: str | None
    accepted: bool
    already_on_target: bool
    verified: bool
    was_off: bool = False
    powered_on: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        """True if the monitor ended up on the target (or was already there) without error."""
        return self.error is None and (self.already_on_target or self.verified)


def switch_monitor(
    client: httpx.Client,
    monitor: MonitorConfig,
    capability: str,
    target: str,
    *,
    dry_run: bool = False,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    timeout: float = DEFAULT_VERIFY_TIMEOUT,
) -> MonitorSwitch:
    """Bring a single monitor to ``target``: read, wake if asleep, set, verify.

    If the panel reports ``switch: off`` we power it on before touching the input; and if
    a 409 ("invalid device state" — an asleep panel) comes back anyway, we power on and
    retry the input change once. Errors are captured on the result, not raised, so one
    monitor can't abort the other.
    """
    desired = monitor.source_for(target)
    state = read_state(client, monitor.device_id)
    before = state.input_source
    was_off = state.is_off
    log.info(
        "switch.read",
        monitor=monitor.name,
        current=before,
        desired=desired,
        power=state.power,
    )

    # Baseline outcome (nothing changed); each return refines it via dataclasses.replace.
    base = MonitorSwitch(
        name=monitor.name,
        device_id=monitor.device_id,
        target=target,
        desired_source=desired,
        before=before,
        after=before,
        accepted=False,
        already_on_target=False,
        verified=False,
        was_off=was_off,
    )

    if dry_run:
        log.info(
            "switch.dry_run",
            monitor=monitor.name,
            would_power_on=was_off,
            would_set=None if before == desired else desired,
        )
        on_target = before == desired
        return replace(base, already_on_target=on_target, verified=on_target, accepted=on_target)

    # A sleeping panel must be woken before it will accept an input change.
    powered_on = False
    if was_off:
        log.info("switch.power_on", monitor=monitor.name, reason="panel is off (standby)")
        powered_on = power_on(client, monitor.device_id, poll_interval=poll_interval)
        if not powered_on:
            log.warning("switch.power_on_unconfirmed", monitor=monitor.name)

    if before == desired:
        log.info("switch.skip", monitor=monitor.name, reason="already on target")
        return replace(
            base, accepted=True, already_on_target=True, verified=True, powered_on=powered_on
        )

    try:
        accepted = set_source(client, monitor.device_id, capability, desired)
    except ApiError as exc:
        # 409 "invalid device state" = the panel is asleep. If we haven't already woken it
        # (the cloud's power state can lag reality), do so now and retry the input once.
        if exc.status_code != 409 or powered_on:
            log.warning("switch.failed", monitor=monitor.name, error=str(exc).splitlines()[0])
            return replace(base, error=str(exc), powered_on=powered_on)
        log.info("switch.power_on", monitor=monitor.name, reason="409 — panel asleep")
        powered_on = power_on(client, monitor.device_id, poll_interval=poll_interval)
        try:
            accepted = set_source(client, monitor.device_id, capability, desired)
        except ApiError as retry_exc:
            log.warning("switch.failed", monitor=monitor.name, error=str(retry_exc).splitlines()[0])
            return replace(base, error=str(retry_exc), powered_on=powered_on)

    verified, after = verify_source(
        client, monitor.device_id, desired, poll_interval=poll_interval, timeout=timeout
    )
    log.info(
        "switch.result",
        monitor=monitor.name,
        accepted=accepted,
        verified=verified,
        after=after,
        powered_on=powered_on,
    )
    return replace(base, after=after, accepted=accepted, verified=verified, powered_on=powered_on)


@dataclass
class SwitchSummary:
    """Aggregate result of switching all monitors to a target."""

    target: str
    dry_run: bool
    results: list[MonitorSwitch]

    @property
    def ok(self) -> bool:
        """Dry runs are always ok; otherwise every monitor must have reached the target."""
        return self.dry_run or all(result.ok for result in self.results)

    @property
    def partial_failure(self) -> bool:
        """True when some monitors reached the target and others did not."""
        if self.dry_run:
            return False
        oks = [result.ok for result in self.results]
        return any(oks) and not all(oks)


def switch(
    client: httpx.Client,
    config: KvmConfig,
    target: str,
    *,
    dry_run: bool = False,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    timeout: float = DEFAULT_VERIFY_TIMEOUT,
) -> SwitchSummary:
    """Switch every configured monitor to ``target``.

    Each monitor is attempted independently — an API failure on one is captured on that
    monitor's result rather than raised, so it can't prevent the other from switching.
    Partial failure is reported via the summary (and a non-zero exit by the CLI).
    """
    results: list[MonitorSwitch] = []
    for monitor in config.monitors:
        try:
            results.append(
                switch_monitor(
                    client,
                    monitor,
                    config.capability,
                    target,
                    dry_run=dry_run,
                    poll_interval=poll_interval,
                    timeout=timeout,
                )
            )
        except ApiError as exc:
            log.warning("switch.monitor_failed", monitor=monitor.name, error=str(exc))
            results.append(
                MonitorSwitch(
                    name=monitor.name,
                    device_id=monitor.device_id,
                    target=target,
                    desired_source=monitor.source_for(target),
                    before=None,
                    after=None,
                    accepted=False,
                    already_on_target=False,
                    verified=False,
                    error=str(exc),
                )
            )
    summary = SwitchSummary(target=target, dry_run=dry_run, results=results)
    log.info(
        "switch.summary",
        target=target,
        dry_run=dry_run,
        ok=summary.ok,
        partial_failure=summary.partial_failure,
    )
    return summary


@dataclass
class MonitorStatus:
    """A monitor's current input, power state, and which target (if any) it maps to."""

    name: str
    device_id: str
    current: str | None
    target: str | None
    power: str | None = None


def status(client: httpx.Client, config: KvmConfig) -> list[MonitorStatus]:
    """Read every monitor's current input + power and map the input back to a target."""
    out: list[MonitorStatus] = []
    for monitor in config.monitors:
        state = read_state(client, monitor.device_id)
        current = state.input_source
        out.append(
            MonitorStatus(
                power=state.power,
                name=monitor.name,
                device_id=monitor.device_id,
                current=current,
                target=monitor.target_for(current),
            )
        )
    return out


def current_target(statuses: list[MonitorStatus]) -> str | None:
    """The single target all monitors share, or ``None`` if mixed/unknown."""
    targets = {monitor.target for monitor in statuses}
    if len(targets) == 1:
        return targets.pop()
    return None


def toggle_target(statuses: list[MonitorStatus]) -> str:
    """Pick the target to flip to.

    ``personal`` -> ``work``; ``work`` -> ``personal``; anything mixed/unknown falls
    back to ``personal`` (the home Mac this tool runs on).
    """
    return "work" if current_target(statuses) == "personal" else "personal"
