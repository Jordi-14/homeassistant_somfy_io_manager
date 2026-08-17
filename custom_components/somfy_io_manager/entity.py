"""Shared helpers for shutter entities."""

from __future__ import annotations

import re
from uuid import uuid4

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo

from .const import CONF_NAME, CONF_SHUTTER_ID, DOMAIN


def ensure_shutter_id(shutter: dict) -> str:
    """Ensure a shutter has an internal identity independent of its slot."""
    value = shutter.get(CONF_SHUTTER_ID)
    if not isinstance(value, str) or not value:
        value = uuid4().hex
        shutter[CONF_SHUTTER_ID] = value
    return value


def shutter_object_id(shutter: dict) -> str:
    """Return a suggested Home Assistant object ID derived from the name."""
    value = re.sub(r"[^a-z0-9]+", "_", shutter[CONF_NAME].lower()).strip("_")
    return value or "somfy_shutter"


def shutter_device_identifier(entry: ConfigEntry, shutter: dict) -> tuple[str, str]:
    """Return the stable virtual-device identifier for one shutter."""
    return DOMAIN, f"{entry.entry_id}:{ensure_shutter_id(shutter)}"


def shutter_device_info(entry: ConfigEntry, shutter: dict) -> DeviceInfo:
    """Describe one user-facing Somfy shutter device."""
    return DeviceInfo(
        identifiers={shutter_device_identifier(entry, shutter)},
        name=shutter[CONF_NAME],
        manufacturer="Somfy / Nice",
        model="io-homecontrol shutter",
    )


def configure_shutter_entity(
    hass: HomeAssistant,
    *,
    entity_id: str,
    area_id: str | None,
) -> None:
    """Assign a shutter entity and its device to the configured area."""
    registry = er.async_get(hass)
    entry = registry.async_update_entity(entity_id, area_id=area_id)

    if entry.device_id is not None:
        dr.async_get(hass).async_update_device(entry.device_id, area_id=area_id)
