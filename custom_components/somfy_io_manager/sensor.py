"""Per-shutter physical-remote event sensors."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event

from .const import (
    CONF_AREA_ID,
    CONF_SHUTTERS,
    CONF_SLOT,
    CONF_STATE,
    DATA_RUNTIME,
    DOMAIN,
    STATE_ACTIVE,
)
from .entity import (
    configure_shutter_entity,
    ensure_shutter_id,
    shutter_device_info,
    shutter_object_id,
)
from .runtime import SomfyIOManagerRuntime, parse_status

_REMOTE_COMMAND_NAMES = {
    "OPEN": "Open",
    "CLOSE": "Close",
    "STOP_MY": "Stop/MY",
    "TILT_CLOCKWISE": "Tilt clockwise",
    "TILT_COUNTERCLOCKWISE": "Tilt counterclockwise",
    "0X0000": "Open",
    "0XC800": "Close",
    "0XD200": "Stop/MY",
    "0XD800": "MY",
    "0XF00D": "Tilt clockwise",
    "0XF00E": "Tilt counterclockwise",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create a received-command sensor for each active shutter."""
    runtime = hass.data[DOMAIN][entry.entry_id][DATA_RUNTIME]
    shutters = entry.options.get(CONF_SHUTTERS, [])
    async_add_entities(
        SomfyRemoteSensor(runtime, entry, shutter)
        for shutter in shutters
        if shutter.get(CONF_STATE) == STATE_ACTIVE
    )


class SomfyRemoteSensor(SensorEntity):
    """Record deduplicated physical-remote presses for one shutter."""

    _attr_icon = "mdi:remote"
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_force_update = True

    def __init__(
        self,
        runtime: SomfyIOManagerRuntime,
        entry: ConfigEntry,
        shutter: dict,
    ) -> None:
        self._runtime = runtime
        self._slot = int(shutter[CONF_SLOT])
        self._shutter_id = ensure_shutter_id(shutter)
        self._area_id = shutter.get(CONF_AREA_ID)
        self._attr_name = "Detected remote"
        cover_object_id = shutter_object_id(shutter)
        self._attr_unique_id = f"{entry.entry_id}-{self._shutter_id}-remote"
        self._attr_suggested_object_id = f"{cover_object_id}_detected_remote"
        self._attr_device_info = shutter_device_info(entry, shutter)
        self._attr_extra_state_attributes = {}
        self._remove_listener = None

    async def async_added_to_hass(self) -> None:
        """Start listening for physical-remote events."""
        await super().async_added_to_hass()
        self._remove_listener = async_track_state_change_event(
            self.hass, self._runtime.status_entity_id, self._status_changed
        )
        self.async_on_remove(self._remove_listener)
        self.async_on_remove(
            self._runtime.coordinator.subscribe(
                self._shutter_id, self._observation_received
            )
        )

        configure_shutter_entity(
            self.hass,
            entity_id=self.entity_id,
            area_id=self._area_id,
        )

    @callback
    def _status_changed(self, event: Event) -> None:
        """Retain the single-bridge event path for older compatible firmware."""
        new_state = event.data.get("new_state")
        status = parse_status(new_state.state if new_state else None)
        target_slots = status.get("slots") if status is not None else None
        matches_slot = (
            self._slot in target_slots
            if isinstance(target_slots, list) and target_slots
            else status is not None and status.get("slot") == self._slot
        )
        if (
            status is None
            or status.get("action") != "remote_command"
            or not matches_slot
        ):
            return
        self._apply_observation(status)

    @callback
    def _observation_received(self, status: dict) -> None:
        """Display one installation-wide deduplicated physical gesture."""
        self._apply_observation(status)

    @callback
    def _apply_observation(self, status: dict) -> None:
        raw_command = str(status.get("command") or status.get("detail") or "")
        command_name = _REMOTE_COMMAND_NAMES.get(raw_command.upper(), "Unknown")
        step_count = status.get("steps")
        if (
            raw_command.upper()
            in {
                "0XF00D",
                "0XF00E",
                "TILT_CLOCKWISE",
                "TILT_COUNTERCLOCKWISE",
            }
            and isinstance(step_count, int)
            and not isinstance(step_count, bool)
            and step_count > 1
        ):
            command_name = f"{command_name} ({step_count} steps)"
        self._attr_native_value = command_name
        self._attr_extra_state_attributes = {
            "remote_id": status.get("remote"),
            "raw_command": raw_command,
            "sequence": status.get("sequence"),
            "steps": step_count,
            "strongest_bridge": status.get("source_bridge"),
            "heard_by_bridges": status.get("heard_by"),
            "rssi": status.get("rssi"),
        }
        self.async_write_ha_state()
