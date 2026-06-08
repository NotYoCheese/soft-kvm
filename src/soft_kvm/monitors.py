"""Monitor configuration (non-secret): deviceIds + target -> source mapping.

Loaded from a committed TOML file (``config/monitors.toml`` by default, overridable
via the ``SOFT_KVM_CONFIG`` env var). Validated into pydantic models so a malformed
or incomplete config fails loudly with an actionable message.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .config import project_root
from .errors import ConfigError
from .models import INPUT_SOURCE_CAPABILITIES

# The named targets this tool switches between.
TARGETS: tuple[str, ...] = ("personal", "work")


class MonitorConfig(BaseModel):
    """One monitor: its deviceId and the source id for each target."""

    model_config = ConfigDict(extra="ignore")

    name: str
    device_id: str
    sources: dict[str, str]

    @field_validator("sources")
    @classmethod
    def _has_all_targets(cls, value: dict[str, str]) -> dict[str, str]:
        missing = [target for target in TARGETS if target not in value]
        if missing:
            raise ValueError(f"missing source mapping for target(s): {', '.join(missing)}")
        return value

    def source_for(self, target: str) -> str:
        """Return the source id this monitor uses for ``target``."""
        try:
            return self.sources[target]
        except KeyError as exc:
            raise ConfigError(
                f"monitor {self.name!r} has no source mapping for target {target!r}"
            ) from exc

    def target_for(self, source: str | None) -> str | None:
        """Reverse lookup: which target a given current source corresponds to (if any)."""
        if source is None:
            return None
        return next((target for target, src in self.sources.items() if src == source), None)


class KvmConfig(BaseModel):
    """The full configuration: capability id + the monitors to drive."""

    model_config = ConfigDict(extra="ignore")

    capability: str = INPUT_SOURCE_CAPABILITIES[0]
    monitors: list[MonitorConfig] = Field(default_factory=list)

    @field_validator("monitors")
    @classmethod
    def _non_empty(cls, value: list[MonitorConfig]) -> list[MonitorConfig]:
        if not value:
            raise ValueError("no monitors configured")
        return value


def config_path() -> Path:
    """Resolve the monitor config path (``SOFT_KVM_CONFIG`` overrides the default)."""
    override = os.environ.get("SOFT_KVM_CONFIG")
    if override:
        return Path(override)
    return project_root() / "config" / "monitors.toml"


def load_config(path: Path | None = None) -> KvmConfig:
    """Load and validate the monitor config, raising :class:`ConfigError` on any problem."""
    resolved = path or config_path()
    if not resolved.is_file():
        raise ConfigError(
            f"monitor config not found at {resolved}. "
            "Copy config/monitors.example.toml to config/monitors.toml and fill it in."
        )
    try:
        data = tomllib.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"failed to read monitor config {resolved}: {exc}") from exc
    try:
        return KvmConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"invalid monitor config {resolved}:\n{exc}") from exc
