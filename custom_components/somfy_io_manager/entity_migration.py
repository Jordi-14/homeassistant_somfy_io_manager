"""Entity-registry migration helpers for hidden ESPHome transports."""

from __future__ import annotations

import re

from homeassistant.helpers import entity_registry as er

_SLOT_COVER_NAME = re.compile(r"somfy shutter slot\s+(\d+)$", re.IGNORECASE)
_NATIVE_MY_NAME = re.compile(
    r"somfy shutter slot\s+(\d+)\s+my position$", re.IGNORECASE
)


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
    """Give a generated cover a hidden slot-based transport identity."""
    source = find_transport_cover(registry, esphome_entry_id, slot)
    if source is None:
        return None

    desired_object_id = f"{_slugify(device_name)}_slot_{slot + 1:02d}_transport"
    desired_entity_id = f"cover.{desired_object_id}"
    if source.entity_id != desired_entity_id:
        occupied = registry.async_get(desired_entity_id)
        if occupied is not None and occupied.entity_id != source.entity_id:
            desired_entity_id = registry.async_get_available_entity_id(
                "cover", desired_object_id
            )

    return registry.async_update_entity(
        source.entity_id,
        new_entity_id=desired_entity_id,
        name=f"Slot {slot + 1} transport",
        area_id=None,
        disabled_by=None if active else er.RegistryEntryDisabler.INTEGRATION,
        hidden_by=er.RegistryEntryHider.INTEGRATION,
    )


def reconcile_transport_entities(
    registry: er.EntityRegistry,
    *,
    esphome_entry_id: str,
    device_name: str,
    active_slots: set[int],
) -> set[int]:
    """Hide every slot cover and enable transports for active shutters."""
    found: set[int] = set()
    for slot in range(20):
        if (
            configure_transport_cover(
                registry,
                esphome_entry_id=esphome_entry_id,
                device_name=device_name,
                slot=slot,
                active=slot in active_slots,
            )
            is not None
        ):
            found.add(slot)

    # Remove entities created by the short-lived native-MY architecture. MY is
    # now a user-facing ButtonEntity owned by this integration.
    for entity in list(er.async_entries_for_config_entry(registry, esphome_entry_id)):
        if entity.entity_id.startswith("button.") and _NATIVE_MY_NAME.search(
            entity.original_name or ""
        ):
            registry.async_remove(entity.entity_id)
    return found
