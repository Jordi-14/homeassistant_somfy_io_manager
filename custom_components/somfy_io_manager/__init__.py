"""Somfy IO shutter manager integration."""

from __future__ import annotations

import logging
import re

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_call_later

from .const import (
    CONF_PRIMARY_ENTRY_ID,
    CONF_REMOTE,
    CONF_REMOTE_ALIASES,
    CONF_SECONDARY_ENTRY_ID,
    CONF_SHUTTERS,
    CONF_SLOT,
    CONF_STATE,
    DATA_COORDINATOR,
    DATA_RUNTIME,
    DOMAIN,
    PLATFORMS,
    STATE_ACTIVE,
)
from .coordinator import SomfyIOMultiBridgeCoordinator
from .entity import ensure_shutter_id, initialize_shutter_device
from .entity_migration import find_transport_cover
from .runtime import ManagerError, SomfyIOManagerRuntime

_LOGGER = logging.getLogger(__name__)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Add internal shutter identities and discard legacy entity-ID metadata."""
    if entry.version > 4:
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
    if entry.version < 4:
        shutters = []
        for stored in options.get(CONF_SHUTTERS, []):
            shutter = dict(stored)
            ensure_shutter_id(shutter)
            shutter[CONF_PRIMARY_ENTRY_ID] = entry.entry_id
            if shutter.get(CONF_SECONDARY_ENTRY_ID) == entry.entry_id:
                shutter.pop(CONF_SECONDARY_ENTRY_ID, None)
            shutters.append(shutter)
        options[CONF_SHUTTERS] = shutters
        hass.config_entries.async_update_entry(entry, options=options, version=4)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a configured ESPHome commissioning bridge."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    coordinator = domain_data.get(DATA_COORDINATOR)
    if not isinstance(coordinator, SomfyIOMultiBridgeCoordinator):
        coordinator = SomfyIOMultiBridgeCoordinator(hass)
        domain_data[DATA_COORDINATOR] = coordinator
    runtime = SomfyIOManagerRuntime(hass, entry, coordinator)
    await runtime.async_setup()
    domain_data[entry.entry_id] = {DATA_RUNTIME: runtime}
    coordinator.register(runtime)

    _remove_obsolete_bridge_device(hass, entry)
    for shutter in entry.options.get(CONF_SHUTTERS, []):
        if shutter.get(CONF_STATE) == STATE_ACTIVE:
            initialize_shutter_device(hass, entry, shutter)
    _wait_for_transport_covers(hass, entry, runtime)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    hass.async_create_task(
        _async_hydrate_remote_metadata(runtime),
        "recover missing Somfy IO physical remote identities",
    )
    hass.async_create_task(
        _async_reconcile_bridge_transfers(coordinator),
        "reconcile Somfy IO bridge transfer journals",
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a bridge."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    data = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    coordinator = hass.data.get(DOMAIN, {}).get(DATA_COORDINATOR)
    if isinstance(coordinator, SomfyIOMultiBridgeCoordinator):
        await coordinator.async_unregister(entry.entry_id)
    if data is not None:
        await data[DATA_RUNTIME].async_unload()
    return True


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload per-shutter entities after the GUI changes metadata."""
    await hass.config_entries.async_reload(entry.entry_id)


async def _async_hydrate_remote_metadata(runtime: SomfyIOManagerRuntime) -> None:
    """Privately recover remote IDs missing from legacy HA shutter metadata."""
    shutters = [dict(item) for item in runtime.entry.options.get(CONF_SHUTTERS, [])]
    changed = False
    for shutter in shutters:
        if shutter.get(CONF_STATE) != STATE_ACTIVE or _valid_remote(
            shutter.get(CONF_REMOTE)
        ):
            continue
        try:
            status = await runtime.async_call(
                "commission",
                {"action": "query", "slot": int(shutter[CONF_SLOT])},
                "slot",
            )
        except ManagerError:
            _LOGGER.warning(
                "Could not recover private remote metadata for Somfy slot %s",
                int(shutter[CONF_SLOT]) + 1,
            )
            continue
        remote = status.get("remote")
        if not _valid_remote(remote):
            continue
        shutter[CONF_REMOTE] = str(remote).upper().replace("0X", "0x", 1)
        changed = True
    if changed:
        runtime.hass.config_entries.async_update_entry(
            runtime.entry,
            options={**runtime.entry.options, CONF_SHUTTERS: shutters},
        )


def _valid_remote(value: object) -> bool:
    """Validate one 24-bit remote identity without logging it."""
    return (
        isinstance(value, str)
        and re.fullmatch(r"0[xX][0-9a-fA-F]{6}", value) is not None
    )


async def _async_reconcile_bridge_transfers(
    coordinator: SomfyIOMultiBridgeCoordinator,
) -> None:
    """Reconcile HA checkpoints whenever either participating bridge loads."""
    for runtime in tuple(coordinator.runtimes.values()):
        try:
            await coordinator.async_reconcile_runtime_transfer(runtime)
        except ManagerError:
            _LOGGER.warning(
                "A Somfy bridge transfer remains safely paused; use Configure to resume"
            )


def _remove_obsolete_bridge_device(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove the former empty manager hub; ESPHome remains the bridge device."""
    registry = dr.async_get(hass)
    devices = list(dr.async_entries_for_config_entry(registry, entry.entry_id))
    bridge = next(
        (
            device
            for device in devices
            if (DOMAIN, entry.entry_id) in device.identifiers
        ),
        None,
    )
    if bridge is None:
        return
    for device in devices:
        if device.id != bridge.id and device.via_device_id == bridge.id:
            registry.async_update_device(device.id, via_device_id=None)
    registry.async_remove_device(bridge.id)


def _wait_for_transport_covers(
    hass: HomeAssistant,
    entry: ConfigEntry,
    runtime: SomfyIOManagerRuntime,
    attempt: int = 0,
) -> None:
    """Wait for active transports without changing ESPHome registry entries."""
    active_slots = {
        int(shutter[CONF_SLOT])
        for shutter in entry.options.get(CONF_SHUTTERS, [])
        if shutter.get(CONF_STATE) == STATE_ACTIVE
    }
    registry = er.async_get(hass)
    found = {
        slot
        for slot in active_slots
        if find_transport_cover(registry, runtime.esphome_entry_id, slot) is not None
    }
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
        lambda _now: _wait_for_transport_covers(hass, entry, runtime, attempt + 1),
    )
    entry.async_on_unload(cancel)
