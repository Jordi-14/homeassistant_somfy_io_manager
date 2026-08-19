"""Somfy IO shutter manager integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_call_later

from .const import (
    CONF_DEVICE_NAME,
    CONF_REMOTE_ALIASES,
    CONF_SHUTTERS,
    CONF_SLOT,
    CONF_STATE,
    DATA_RUNTIME,
    DOMAIN,
    PLATFORMS,
    STATE_ACTIVE,
)
from .entity import ensure_shutter_id
from .entity_migration import reconcile_transport_entities
from .runtime import ManagerError, SomfyIOManagerRuntime

_LOGGER = logging.getLogger(__name__)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Add internal shutter identities and discard legacy entity-ID metadata."""
    if entry.version > 3:
        return False
    options = dict(entry.options)
    if entry.version < 2:
        entity_registry = er.async_get(hass)
        for entity in list(
            er.async_entries_for_config_entry(entity_registry, entry.entry_id)
        ):
            entity_registry.async_remove(entity.entity_id)
        device_registry = dr.async_get(hass)
        for device in list(
            dr.async_entries_for_config_entry(device_registry, entry.entry_id)
        ):
            device_registry.async_remove_device(device.id)

        shutters = []
        for stored in options.get(CONF_SHUTTERS, []):
            shutter = dict(stored)
            ensure_shutter_id(shutter)
            shutter.pop("cover_entity_id", None)
            shutters.append(shutter)
        options[CONF_SHUTTERS] = shutters
    if entry.version < 3:
        options.setdefault(CONF_REMOTE_ALIASES, [])
        hass.config_entries.async_update_entry(entry, options=options, version=3)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a configured ESPHome commissioning bridge."""
    runtime = SomfyIOManagerRuntime(hass, entry)
    await runtime.async_setup()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {DATA_RUNTIME: runtime}

    _remove_obsolete_bridge_device(hass, entry)
    _reconcile_transports(hass, entry, runtime)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    if entry.options.get(CONF_REMOTE_ALIASES):
        hass.async_create_task(
            _async_sync_remote_aliases(runtime),
            "restore Somfy IO group remote aliases",
        )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a bridge."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    data = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if data is not None:
        await data[DATA_RUNTIME].async_unload()
    return True


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload per-shutter entities after the GUI changes metadata."""
    await hass.config_entries.async_reload(entry.entry_id)


async def _async_sync_remote_aliases(runtime: SomfyIOManagerRuntime) -> None:
    """Reapply HA's non-secret group mappings after bridge replacement."""
    try:
        await runtime.async_sync_remote_aliases()
    except ManagerError:
        _LOGGER.warning(
            "Could not synchronize Somfy group remote aliases; will retry on "
            "the next integration reload"
        )


def _remove_obsolete_bridge_device(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove the former empty manager hub; ESPHome remains the bridge device."""
    registry = dr.async_get(hass)
    bridge = registry.async_get_device(identifiers={(DOMAIN, entry.entry_id)})
    if bridge is None:
        return
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        if device.id != bridge.id and device.via_device_id == bridge.id:
            registry.async_update_device(device.id, via_device_id=None)
    registry.async_remove_device(bridge.id)


def _reconcile_transports(
    hass: HomeAssistant,
    entry: ConfigEntry,
    runtime: SomfyIOManagerRuntime,
    attempt: int = 0,
) -> None:
    """Hide ESPHome slot covers and keep active ones loaded as state transports."""
    active_slots = {
        int(shutter[CONF_SLOT])
        for shutter in entry.options.get(CONF_SHUTTERS, [])
        if shutter.get(CONF_STATE) == STATE_ACTIVE
    }
    found = reconcile_transport_entities(
        er.async_get(hass),
        esphome_entry_id=runtime.esphome_entry_id,
        device_name=entry.data[CONF_DEVICE_NAME],
        active_slots=active_slots,
    )
    missing = active_slots - found
    if not missing:
        if attempt:
            hass.async_create_task(
                hass.config_entries.async_reload(entry.entry_id),
                "reload Somfy shutters after ESPHome transport discovery",
            )
        return
    if attempt >= 11:
        _LOGGER.warning(
            "ESPHome transport covers did not appear for Somfy slots: %s",
            ", ".join(str(slot + 1) for slot in sorted(missing)),
        )
        return

    cancel = async_call_later(
        hass,
        5,
        lambda _now: _reconcile_transports(hass, entry, runtime, attempt + 1),
    )
    entry.async_on_unload(cancel)
