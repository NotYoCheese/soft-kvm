"""Phase 1 core switch.

Set both monitors to a named target (``work`` | ``personal``), idempotently and
verified: read each monitor's current input, skip if already on target, otherwise
issue ``setInputSource`` and read the value back to confirm the panel actually
changed. A cloud ``200`` is "accepted," never "switched."

Auth note: this uses the PAT-backed client for now. Phase 2 swaps the token source
for the OAuth refresh-token flow without changing this module.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from .client import request_json
from .commands import build_command_body, command_accepted
from .logging_setup import get_logger
from .models import extract_input_source
from .monitors import KvmConfig, MonitorConfig

log = get_logger("switcher")

DEFAULT_POLL_INTERVAL = 0.5
DEFAULT_VERIFY_TIMEOUT = 8.0


def read_current(client: httpx.Client, device_id: str) -> str | None:
    """Read a monitor's current input source id (``None`` if unavailable/offline)."""
    status = request_json(client, "GET", f"/devices/{device_id}/status")
    state = extract_input_source(status)
    return state.current if state else None


def set_source(client: httpx.Client, device_id: str, capability: str, source: str) -> bool:
    """Issue ``setInputSource`` and return whether the cloud accepted it."""
    body = build_command_body(capability, source)
    response = request_json(client, "POST", f"/devices/{device_id}/commands", json=body)
    return command_accepted(response)


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

    @property
    def ok(self) -> bool:
        """True if the monitor ended up on the target (or was already there)."""
        return self.already_on_target or self.verified


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
    """Bring a single monitor to ``target``: read, skip-if-on-target, set, verify."""
    desired = monitor.source_for(target)
    before = read_current(client, monitor.device_id)
    log.info("switch.read", monitor=monitor.name, current=before, desired=desired)

    if before == desired:
        log.info("switch.skip", monitor=monitor.name, reason="already on target")
        return MonitorSwitch(
            name=monitor.name,
            device_id=monitor.device_id,
            target=target,
            desired_source=desired,
            before=before,
            after=before,
            accepted=True,
            already_on_target=True,
            verified=True,
        )

    if dry_run:
        log.info("switch.dry_run", monitor=monitor.name, would_set=desired)
        return MonitorSwitch(
            name=monitor.name,
            device_id=monitor.device_id,
            target=target,
            desired_source=desired,
            before=before,
            after=before,
            accepted=False,
            already_on_target=False,
            verified=False,
        )

    accepted = set_source(client, monitor.device_id, capability, desired)
    verified, after = verify_source(
        client, monitor.device_id, desired, poll_interval=poll_interval, timeout=timeout
    )
    log.info(
        "switch.result",
        monitor=monitor.name,
        accepted=accepted,
        verified=verified,
        after=after,
    )
    return MonitorSwitch(
        name=monitor.name,
        device_id=monitor.device_id,
        target=target,
        desired_source=desired,
        before=before,
        after=after,
        accepted=accepted,
        already_on_target=False,
        verified=verified,
    )


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

    Each monitor is attempted independently so one offline panel doesn't prevent the
    other from switching; partial failure is reported via the summary.
    """
    results = [
        switch_monitor(
            client,
            monitor,
            config.capability,
            target,
            dry_run=dry_run,
            poll_interval=poll_interval,
            timeout=timeout,
        )
        for monitor in config.monitors
    ]
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
    """A monitor's current input and which target (if any) it corresponds to."""

    name: str
    device_id: str
    current: str | None
    target: str | None


def status(client: httpx.Client, config: KvmConfig) -> list[MonitorStatus]:
    """Read every monitor's current input and map it back to a target name."""
    out: list[MonitorStatus] = []
    for monitor in config.monitors:
        current = read_current(client, monitor.device_id)
        out.append(
            MonitorStatus(
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
