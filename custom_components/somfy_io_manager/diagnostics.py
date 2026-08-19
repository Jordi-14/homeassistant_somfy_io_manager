"""Privacy-preserving diagnostics for Somfy IO Shutter Manager."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant

from .const import (
    CONF_BACKUP_ENTITY_ID,
    CONF_CLOSE_SECONDS,
    CONF_COVER_TYPE,
    CONF_DEVICE_NAME,
    CONF_MY_PERCENT,
    CONF_MY_TILT_STEP,
    CONF_OPEN_SECONDS,
    CONF_SHUTTERS,
    CONF_SLOT,
    CONF_STATE,
    CONF_STATUS_ENTITY_ID,
    CONF_TILT_INVERTED,
    CONF_TILT_STEPS,
    DATA_RUNTIME,
    DOMAIN,
    MANAGER_API_VERSION,
    STATE_ACTIVE,
    STATE_UNCERTAIN,
)
from .runtime import SomfyIOManagerRuntime, parse_status

_SERVICE_SUFFIXES = (
    "commission",
    "calibrate",
    "control",
    "restore",
    "move",
    "swap",
    "venetian",
)
_SAFE_STATUS_FIELDS = ("v", "event", "action", "slot", "state", "rssi")


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics without identities, keys, or recovery payloads."""
    runtime_data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    runtime = runtime_data.get(DATA_RUNTIME) if runtime_data else None
    status_state = hass.states.get(entry.data[CONF_STATUS_ENTITY_ID])
    backup_state = hass.states.get(entry.data[CONF_BACKUP_ENTITY_ID])
    status = parse_status(status_state.state if status_state else None)

    diagnostics: dict[str, Any] = {
        "entry": {
            "version": entry.version,
            "manager_api_version": MANAGER_API_VERSION,
        },
        "bridge": {
            "runtime_loaded": isinstance(runtime, SomfyIOManagerRuntime),
            "status_entity_available": _state_available(status_state),
            "backup_entity_available": _state_available(backup_state),
            "services": _service_availability(hass, entry.data[CONF_DEVICE_NAME]),
            "last_status": _sanitized_manager_status(status),
        },
    }

    shutters = entry.options.get(CONF_SHUTTERS, [])
    diagnostics["shutters"] = {
        "count": len(shutters),
        "active": sum(
            shutter.get(CONF_STATE) == STATE_ACTIVE for shutter in shutters
        ),
        "uncertain": sum(
            shutter.get(CONF_STATE) == STATE_UNCERTAIN for shutter in shutters
        ),
        "slots": _shutter_diagnostics(shutters, runtime),
    }
    return diagnostics


def _state_available(state: Any) -> bool:
    """Return whether a transport entity currently has usable state."""
    return state is not None and state.state not in {
        "",
        STATE_UNKNOWN,
        STATE_UNAVAILABLE,
    }


def _service_availability(
    hass: HomeAssistant,
    device_name: str,
) -> dict[str, bool]:
    """Report the manager API surface without exposing generated service IDs."""
    prefix = device_name.replace("-", "_")
    return {
        suffix: hass.services.has_service(
            "esphome", f"{prefix}_somfy_{suffix}"
        )
        for suffix in _SERVICE_SUFFIXES
    }


def _sanitized_manager_status(status: dict[str, Any] | None) -> dict[str, Any] | None:
    """Keep operational status fields and omit all radio identities."""
    if status is None:
        return None
    safe = {key: status.get(key) for key in _SAFE_STATUS_FIELDS if key in status}
    slot = safe.get("slot")
    if isinstance(slot, int) and slot >= 0:
        safe["slot"] = slot + 1
    return safe


def _shutter_diagnostics(
    shutters: list[dict[str, Any]],
    runtime: SomfyIOManagerRuntime | None,
) -> list[dict[str, Any]]:
    """Return anonymous configuration and recovery health for each slot."""
    pending_slots = set(runtime.pending) if runtime is not None else set()
    result = []
    for shutter in sorted(shutters, key=lambda item: int(item[CONF_SLOT])):
        slot = int(shutter[CONF_SLOT])
        result.append(
            {
                "slot": slot + 1,
                "state": shutter.get(CONF_STATE),
                "open_seconds": shutter.get(CONF_OPEN_SECONDS),
                "close_seconds": shutter.get(CONF_CLOSE_SECONDS),
                "my_percent": shutter.get(CONF_MY_PERCENT),
                "cover_type": shutter.get(CONF_COVER_TYPE, "shutter"),
                "tilt_steps": shutter.get(CONF_TILT_STEPS),
                "my_tilt_step": shutter.get(CONF_MY_TILT_STEP),
                "tilt_inverted": shutter.get(CONF_TILT_INVERTED),
                "encrypted_recovery_available": (
                    runtime is not None and runtime.backup_for_slot(slot) is not None
                ),
                "commissioning_pending": slot in pending_slots,
            }
        )
    return result
