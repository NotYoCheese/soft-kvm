"""soft-kvm CLI.

Phase 1 commands:
  * ``work`` / ``personal`` — switch both monitors to that target (idempotent + verified).
  * ``status``              — show each monitor's current input and mapped target.
  * ``toggle``              — read state and flip both monitors to the other target.

Phase 0 utilities (kept for diagnostics):
  * ``discover``    — enumerate devices, dump input capability + state, save fixtures.
  * ``test-switch`` — send ONE setInputSource to a device and verify it switched.

Global ``--dry-run`` reports what would change without sending any command.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, NoReturn
from urllib.parse import parse_qs, urlparse

import typer
from rich.console import Console
from rich.table import Table

from . import __version__, auth, discovery, switcher
from .client import build_client
from .commands import build_command_body
from .config import fixtures_dir, get_settings
from .errors import CredentialsUnavailableError, SoftKvmError
from .logging_setup import configure_logging
from .models import INPUT_SOURCE_CAPABILITIES, DeviceList
from .monitors import load_config

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="soft-kvm — switch two ViewFinity S9 monitors between two Macs (SmartThings).",
)
console = Console()
err_console = Console(stderr=True)


@dataclass
class AppState:
    """Global flags carried on the Typer context."""

    dry_run: bool = False


def _state(ctx: typer.Context) -> AppState:
    assert isinstance(ctx.obj, AppState)
    return ctx.obj


def _fail(exc: SoftKvmError) -> NoReturn:
    """Print an error and exit. Exit code 3 signals the launcher to retry under `op run`."""
    err_console.print(f"[bold red]Error:[/] {exc}")
    code = 3 if isinstance(exc, CredentialsUnavailableError) else 1
    raise typer.Exit(code=code) from exc


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"soft-kvm {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    ctx: typer.Context,
    version: Annotated[
        bool,
        typer.Option(
            "--version", callback=_version_callback, is_eager=True, help="Show version and exit."
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Report what would change; send no commands."),
    ] = False,
    log_level: Annotated[
        str, typer.Option("--log-level", help="Log level: debug|info|warning|error.")
    ] = "info",
) -> None:
    """soft-kvm — set both monitors' video input by name."""
    configure_logging(log_level)
    ctx.obj = AppState(dry_run=dry_run)


# --------------------------------------------------------------------------- #
# Phase 1 — core switch
# --------------------------------------------------------------------------- #


def _render_switch_summary(summary: switcher.SwitchSummary) -> None:
    verb = "Would switch to" if summary.dry_run else "Switch to"
    table = Table(title=f"{verb} [bold]{summary.target}[/]")
    table.add_column("Monitor")
    table.add_column("Before")
    table.add_column("Desired")
    table.add_column("Result")
    for result in summary.results:
        if result.already_on_target:
            outcome = "[green]already on target[/]"
        elif summary.dry_run:
            outcome = "[yellow]would set[/]"
        elif result.verified:
            outcome = "[bold green]switched ✓[/]"
        else:
            outcome = "[bold red]FAILED[/]"
        table.add_row(result.name, str(result.before), result.desired_source, outcome)
    console.print(table)
    if summary.partial_failure:
        err_console.print(
            "[bold red]Partial failure:[/] some monitors reached the target and others "
            "did not (see table). The unreached panel may be offline or the source name wrong."
        )
    elif not summary.dry_run and not summary.ok:
        err_console.print("[bold red]No monitor reached the target.[/]")


def _run_switch(ctx: typer.Context, target: str) -> None:
    state = _state(ctx)
    try:
        config = load_config()
        with build_client(get_settings()) as client:
            summary = switcher.switch(client, config, target, dry_run=state.dry_run)
    except SoftKvmError as exc:
        _fail(exc)
    _render_switch_summary(summary)
    if not summary.ok:
        raise typer.Exit(code=1)


@app.command()
def work(ctx: typer.Context) -> None:
    """Switch both monitors to the work Mac (Mini DisplayPort)."""
    _run_switch(ctx, "work")


@app.command()
def personal(ctx: typer.Context) -> None:
    """Switch both monitors to the personal Mac (Thunderbolt / USB-C)."""
    _run_switch(ctx, "personal")


def _render_status(statuses: list[switcher.MonitorStatus]) -> None:
    table = Table(title="Monitor status")
    table.add_column("Monitor")
    table.add_column("Current source")
    table.add_column("Target")
    for monitor in statuses:
        target = monitor.target or "[dim]unknown[/]"
        current = monitor.current if monitor.current is not None else "[dim]unavailable[/]"
        table.add_row(monitor.name, current, target)
    console.print(table)
    overall = switcher.current_target(statuses) or "mixed / unknown"
    console.print(f"Overall: [bold]{overall}[/]")


@app.command(name="status")
def status_cmd() -> None:
    """Show each monitor's current input source and the target it maps to."""
    try:
        config = load_config()
        with build_client(get_settings()) as client:
            statuses = switcher.status(client, config)
    except SoftKvmError as exc:
        _fail(exc)
    _render_status(statuses)


@app.command()
def toggle(ctx: typer.Context) -> None:
    """Read current state and flip both monitors to the other target."""
    state = _state(ctx)
    try:
        config = load_config()
        with build_client(get_settings()) as client:
            statuses = switcher.status(client, config)
            target = switcher.toggle_target(statuses)
            now = switcher.current_target(statuses) or "mixed/unknown"
            console.print(f"Current: [bold]{now}[/] → switching to [bold]{target}[/]")
            summary = switcher.switch(client, config, target, dry_run=state.dry_run)
    except SoftKvmError as exc:
        _fail(exc)
    _render_switch_summary(summary)
    if not summary.ok:
        raise typer.Exit(code=1)


# --------------------------------------------------------------------------- #
# Phase 2 — durable OAuth auth
# --------------------------------------------------------------------------- #

auth_app = typer.Typer(
    no_args_is_help=True,
    help="Manage durable SmartThings OAuth credentials (Phase 2).",
)
app.add_typer(auth_app, name="auth")


def _extract_code(pasted: str, expected_state: str) -> str:
    """Pull the `code` out of a pasted authorization code or full redirect URL."""
    pasted = pasted.strip()
    if "code=" not in pasted:
        return pasted  # user pasted the bare code
    query = urlparse(pasted).query or pasted
    params = parse_qs(query)
    codes = params.get("code")
    if not codes:
        raise SoftKvmError("Could not find a `code` parameter in the pasted value.")
    state_values = params.get("state")
    state = state_values[0] if state_values else None
    if state is not None and state != expected_state:
        raise SoftKvmError(
            "State mismatch — the pasted redirect doesn't match this auth attempt. Retry."
        )
    return codes[0]


@auth_app.command("init")
def auth_init() -> None:
    """One-time: authorize in the browser and store the refresh token in the Keychain."""
    settings = get_settings()
    try:
        settings.require_oauth()
    except SoftKvmError as exc:
        _fail(exc)

    state = secrets.token_urlsafe(16)
    url = auth.build_authorize_url(settings, state)
    console.print("[bold]1.[/] Open this URL in your browser and approve access:\n")
    console.print(f"   {url}\n")
    console.print(
        f"[bold]2.[/] You'll be redirected to {settings.smartthings_redirect_uri}?code=… "
        "(the page may fail to load — that's expected).\n"
        "[bold]3.[/] Copy the `code` value (or paste the whole redirect URL) below.\n"
    )
    typer.launch(url)

    pasted = typer.prompt("Paste code or redirect URL").strip()
    try:
        code = _extract_code(pasted, state)
        tokens = auth.exchange_code(settings, code)
    except SoftKvmError as exc:
        _fail(exc)

    console.print(
        f"[bold green]Authorized ✓[/] Refresh token stored in the macOS Keychain "
        f"(service '{auth.KEYRING_SERVICE}'). Scope: {tokens.scope or 'n/a'}."
    )


@auth_app.command("status")
def auth_status() -> None:
    """Show OAuth credentials, the refresh token, and the cached access token."""
    settings = get_settings()
    has_refresh = auth.load_refresh_token() is not None
    console.print(f"OAuth client credentials configured : {settings.has_oauth_credentials()}")
    console.print(f"Refresh token stored in Keychain    : {has_refresh}")
    cached = auth.load_access_token()
    if cached is None:
        console.print("Access token cached                 : no")
    else:
        minutes = int((cached[1] - time.time()) / 60)
        state = f"valid ~{minutes} min" if minutes > 0 else "expired (refreshes on next use)"
        console.print(f"Access token cached                 : yes ({state})")
    if not has_refresh:
        console.print("[yellow]Run `soft-kvm auth init` to authorize.[/]")


@auth_app.command("logout")
def auth_logout() -> None:
    """Delete the stored refresh + access tokens from the Keychain."""
    cleared = auth.clear_refresh_token()
    auth.clear_access_token()
    console.print("Cleared stored tokens." if cleared else "No refresh token was stored.")


# --------------------------------------------------------------------------- #
# Phase 0 — discovery utilities
# --------------------------------------------------------------------------- #


def _render_discovery(
    devices: DeviceList,
    findings: list[discovery.DeviceFinding],
    fixtures: Path,
) -> None:
    table = Table(title=f"SmartThings devices ({len(devices.items)})")
    table.add_column("#", justify="right", style="dim")
    table.add_column("Label")
    table.add_column("deviceId")
    table.add_column("Input-source capability")
    for idx, device in enumerate(devices.items, start=1):
        cap = device.input_source_capability or "[dim]-[/dim]"
        table.add_row(str(idx), device.display_name, device.device_id, cap)
    console.print(table)

    if not findings:
        err_console.print(
            "[bold yellow]No monitor candidates found.[/] None of the devices expose "
            f"{INPUT_SOURCE_CAPABILITIES} and none matched the monitor name hints.\n"
            "Check the saved devices.json and tell me which devices are the S9s."
        )
        return

    summary: list[dict[str, object]] = []
    console.print()
    for finding in findings:
        dev = finding.device
        state = finding.input_state
        console.rule(f"[bold]{dev.display_name}[/]")
        console.print(f"  deviceId : {dev.device_id}")
        if state is None:
            console.print(
                "  [yellow]No input-source capability present on `main` in the status "
                "response[/] (device may be offline / driving Mac off)."
            )
            summary.append(
                {
                    "label": dev.display_name,
                    "deviceId": dev.device_id,
                    "capability": None,
                    "current": None,
                    "supported": [],
                }
            )
            continue
        console.print(f"  capability : {state.capability}")
        console.print(f"  current    : [bold cyan]{state.current}[/]")
        console.print("  supported  :")
        for src in state.supported:
            name = state.source_names.get(src)
            label = f' (name: "{name}")' if name else ""
            console.print(f'    - "{src}"{label}')
        summary.append(
            {
                "label": dev.display_name,
                "deviceId": dev.device_id,
                "capability": state.capability,
                "current": state.current,
                "supported": state.supported,
                "source_names": state.source_names,
            }
        )

    discovery.save_fixture(fixtures, "discovery_summary", summary)
    console.print("\nFixtures saved under tests/fixtures/.")


@app.command()
def discover() -> None:
    """Enumerate devices and dump each monitor's input capability + current state."""
    fixtures = fixtures_dir()
    try:
        with build_client(get_settings()) as client:
            devices, findings = discovery.run_discovery(client, fixtures)
    except SoftKvmError as exc:
        _fail(exc)
    _render_discovery(devices, findings, fixtures)


def _render_switch(result: discovery.SwitchResult) -> None:
    console.rule("[bold]Test switch result[/]")
    console.print(f"  deviceId       : {result.device_id}")
    console.print(f"  capability     : {result.capability}")
    console.print(f"  target source  : [bold]{result.target}[/]")
    console.print(f"  before         : {result.before}")
    console.print(f"  after          : [bold cyan]{result.after}[/]")
    console.print(f"  command accepted (HTTP 200): {result.accepted}")
    console.print(f"  command round-trip: {result.command_latency_s:.2f}s")
    if result.already_on_target:
        console.print("  [yellow]Was already on the target source (idempotent no-op).[/]")
    elif result.changed:
        latency = (
            f"{result.verify_latency_s:.2f}s" if result.verify_latency_s is not None else "n/a"
        )
        console.print(f"  [bold green]Switched. Verified change latency: {latency}[/]")
    else:
        console.print(
            "  [bold red]Command accepted but inputSource did NOT reach the target "
            "within the timeout.[/] The source name may be wrong, or the cloud state "
            "lags — check the monitor and the saved status_*_after.json fixture."
        )


@app.command(name="test-switch")
def test_switch(
    ctx: typer.Context,
    device_id: Annotated[str, typer.Option("--device-id", help="Target device id.")],
    source: Annotated[
        str,
        typer.Option("--source", help="Exact source id from supportedInputSourcesMap."),
    ],
    capability: Annotated[
        str, typer.Option(help="Input-source capability id.")
    ] = INPUT_SOURCE_CAPABILITIES[0],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the confirmation prompt.")] = False,
) -> None:
    """Send ONE setInputSource command to a real monitor and verify it switched."""
    fixtures = fixtures_dir()

    if _state(ctx).dry_run:
        console.print("[dim]Dry run — would POST this command body:[/]")
        console.print_json(data=build_command_body(capability, source))
        return

    if not yes:
        proceed = typer.confirm(
            f"Send setInputSource('{source}') to {device_id}? Watch the physical monitor."
        )
        if not proceed:
            raise typer.Exit(code=1)

    try:
        with build_client(get_settings()) as client:
            result = discovery.switch_and_verify(client, device_id, capability, source, fixtures)
    except SoftKvmError as exc:
        _fail(exc)

    _render_switch(result)
    if not result.changed and not result.already_on_target:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
