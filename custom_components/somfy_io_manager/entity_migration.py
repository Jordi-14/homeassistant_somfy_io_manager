"""Find ESPHome transports and prepare them during explicit commissioning."""

from __future__ import annotations

import re

from homeassistant.helpers import entity_registry as er

_SLOT_COVER_NAME = re.compile(r"somfy shutter slot\s+(\d+)$", re.IGNORECASE)


def _slugify(value: str) -> str:
    """Return a conservative Home Assistant object-ID fragment."""
    value = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return value or "somfy_io_bridge"


def find_transport_cover(
    registry: er.EntityRegistry, esphome_entry_id: str, slot: int
) -> er.RegistryEntry | None:
    """Find the generated ESPHome cover for an internal firmware slot."""
    expected_number = slot + 1
    for entity in er.async_entries_for_config_entry(registry, esphome_entry_id):
        if not entity.entity_id.startswith("cover."):
            continue
        match = _SLOT_COVER_NAME.search(entity.original_name or "")
        if match and int(match.group(1)) == expected_number:
            return entity
    return None


def configure_transport_cover(
    registry: er.EntityRegistry,
    *,
    esphome_entry_id: str,
    device_name: str,
    slot: int,
    active: bool,
) -> er.RegistryEntry | None:
    """Adjust integration-owned disabled state without rewriting user settings."""
    source = find_transport_cover(registry, esphome_entry_id, slot)
    if source is None:
        return None

    if (
        source.area_id is not None
        or source.disabled_by not in (None, er.RegistryEntryDisabler.INTEGRATION)
        or source.hidden_by not in (None, er.RegistryEntryHider.INTEGRATION)
    ):
        return source

    previous_name = f"Slot {slot + 1} transport"
    previous_id = f"cover.{_slugify(device_name)}_slot_{slot + 1:02d}_transport"
    if source.name != previous_name or source.entity_id != previous_id:
        if source.name is not None:
            return source
        suggested_id = source.suggested_object_id or source.object_id_base
        if not suggested_id or source.entity_id != f"cover.{_slugify(suggested_id)}":
            return source

    disabled_by = None if active else er.RegistryEntryDisabler.INTEGRATION
    if source.disabled_by == disabled_by:
        return source
    return registry.async_update_entity(source.entity_id, disabled_by=disabled_by)
