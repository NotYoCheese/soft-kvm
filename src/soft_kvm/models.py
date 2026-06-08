"""Pydantic models for the SmartThings responses we care about.

Per the project brief: validate responses into models rather than indexing raw
dicts, so a shape change fails loudly. The device *status* response is keyed by
capability id (dynamic), so we locate the input-source capability defensively and
then validate just that slice into :class:`InputSourceState`.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# Capability ids that expose a settable input source, in preference order.
# The S9 is expected to use the Samsung-specific one; plain mediaInputSource is
# a fallback we check in case the vendor capability is absent.
INPUT_SOURCE_CAPABILITIES: tuple[str, ...] = (
    "samsungvd.mediaInputSource",
    "mediaInputSource",
)

# Substrings that hint a device is one of the monitors, used only as a fallback
# when capability-based detection finds nothing.
MONITOR_NAME_HINTS: tuple[str, ...] = ("s9", "viewfinity", "monitor", "display")


class Capability(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    version: int | None = None


class Component(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    capabilities: list[Capability] = Field(default_factory=list)


class Device(BaseModel):
    """A single entry from ``GET /v1/devices``."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    device_id: str = Field(alias="deviceId")
    name: str | None = None
    label: str | None = None
    device_type_name: str | None = Field(default=None, alias="deviceTypeName")
    components: list[Component] = Field(default_factory=list)

    @property
    def capability_ids(self) -> set[str]:
        return {cap.id for comp in self.components for cap in comp.capabilities}

    @property
    def display_name(self) -> str:
        return self.label or self.name or self.device_id

    @property
    def input_source_capability(self) -> str | None:
        """The input-source capability this device exposes, if any."""
        ids = self.capability_ids
        return next((c for c in INPUT_SOURCE_CAPABILITIES if c in ids), None)

    def looks_like_monitor(self) -> bool:
        """True if this device exposes an input-source capability or its name hints a monitor."""
        if self.input_source_capability is not None:
            return True
        haystack = f"{self.label or ''} {self.name or ''}".lower()
        return any(hint in haystack for hint in MONITOR_NAME_HINTS)


class DeviceList(BaseModel):
    """``GET /v1/devices`` response."""

    model_config = ConfigDict(extra="ignore")

    items: list[Device] = Field(default_factory=list)


class InputSourceState(BaseModel):
    """The input-source slice extracted from a device status response.

    ``supported`` holds the source *id* strings (what ``setInputSource`` takes).
    ``source_names`` maps each id to its human label when the device provides one
    (the ``samsungvd.mediaInputSource`` capability reports ``{id, name}`` pairs via
    ``supportedInputSourcesMap``; the plain ``mediaInputSource`` capability reports
    a flat string list with no names).
    """

    capability: str
    current: str | None = None
    supported: list[str] = Field(default_factory=list)
    source_names: dict[str, str] = Field(default_factory=dict)


def _attr_value(block: dict[str, Any], attribute: str) -> Any:
    attr = block.get(attribute)
    return attr.get("value") if isinstance(attr, dict) else None


def extract_input_source(status: dict[str, Any]) -> InputSourceState | None:
    """Pull the input-source state out of a ``GET /v1/devices/{id}/status`` body.

    Handles both attribute shapes: ``supportedInputSourcesMap`` (list of
    ``{id, name}``, used by ``samsungvd.mediaInputSource``) and ``supportedInputSources``
    (flat string list, used by the generic ``mediaInputSource``). Returns ``None`` if
    no known input-source capability carries usable data on ``main``.
    """
    main = status.get("components", {}).get("main", {})
    if not isinstance(main, dict):
        return None
    for capability in INPUT_SOURCE_CAPABILITIES:
        block = main.get(capability)
        if not isinstance(block, dict):
            continue

        current = _attr_value(block, "inputSource")
        supported: list[str] = []
        source_names: dict[str, str] = {}

        mapped = _attr_value(block, "supportedInputSourcesMap")
        if isinstance(mapped, list) and mapped:
            for entry in mapped:
                if isinstance(entry, dict) and entry.get("id") is not None:
                    source_id = str(entry["id"])
                    supported.append(source_id)
                    if entry.get("name") is not None:
                        source_names[source_id] = str(entry["name"])
        else:
            flat = _attr_value(block, "supportedInputSources")
            if isinstance(flat, list):
                supported = [str(item) for item in flat]

        # Skip a capability that's present but carries no usable data (e.g. the
        # generic mediaInputSource block that reports an empty list + null value).
        if current is None and not supported:
            continue

        return InputSourceState(
            capability=capability,
            current=str(current) if current is not None else None,
            supported=supported,
            source_names=source_names,
        )
    return None
