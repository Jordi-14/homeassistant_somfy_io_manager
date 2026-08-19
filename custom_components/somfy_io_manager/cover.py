"""User-facing Somfy shutter covers backed by hidden ESPHome transports."""

from __future__ import annotations

from homeassistant.components.cover import (
    ATTR_POSITION,
    ATTR_TILT_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
    CoverState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event

from .const import (
    CONF_AREA_ID,
    CONF_COVER_TYPE,
    CONF_SHUTTERS,
    CONF_SLOT,
    CONF_STATE,
    COVER_TYPE_SHUTTER,
    COVER_TYPE_VENETIAN,
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
from .entity_migration import find_transport_cover
from .runtime import SomfyIOManagerRuntime


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create one user-facing cover for every active shutter."""
    runtime = hass.data[DOMAIN][entry.entry_id][DATA_RUNTIME]
    registry = er.async_get(hass)
    entities: list[SomfyManagedCover] = []
    for shutter in entry.options.get(CONF_SHUTTERS, []):
        if shutter.get(CONF_STATE) != STATE_ACTIVE:
            continue
        transport = find_transport_cover(
            registry, runtime.esphome_entry_id, int(shutter[CONF_SLOT])
        )
        entities.append(
            SomfyManagedCover(
                runtime,
                entry,
                shutter,
                transport.entity_id if transport is not None else None,
            )
        )
    async_add_entities(entities)


class SomfyManagedCover(CoverEntity):
    """Expose a hidden ESPHome slot cover as a Somfy shutter entity."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_should_poll = False
    _BASE_FEATURES = (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.STOP
        | CoverEntityFeature.SET_POSITION
    )
    _TILT_FEATURES = (
        CoverEntityFeature.STOP_TILT
        | CoverEntityFeature.SET_TILT_POSITION
    )

    def __init__(
        self,
        runtime: SomfyIOManagerRuntime,
        entry: ConfigEntry,
        shutter: dict,
        transport_entity_id: str | None,
    ) -> None:
        object_id = shutter_object_id(shutter)
        shutter_id = ensure_shutter_id(shutter)
        self._runtime = runtime
        self._slot = int(shutter[CONF_SLOT])
        self._is_venetian = (
            shutter.get(CONF_COVER_TYPE, COVER_TYPE_SHUTTER)
            == COVER_TYPE_VENETIAN
        )
        self._attr_device_class = (
            CoverDeviceClass.BLIND
            if self._is_venetian
            else CoverDeviceClass.SHUTTER
        )
        self._attr_supported_features = self._BASE_FEATURES
        if self._is_venetian:
            self._attr_supported_features |= self._TILT_FEATURES
        self._area_id = shutter.get(CONF_AREA_ID)
        self._transport_entity_id = transport_entity_id
        self._attr_unique_id = f"{entry.entry_id}-{shutter_id}-cover"
        self._attr_suggested_object_id = object_id
        self._attr_device_info = shutter_device_info(entry, shutter)
        self._attr_available = False
        self._attr_is_closed = None

    async def async_added_to_hass(self) -> None:
        """Start mirroring the hidden ESPHome transport cover."""
        await super().async_added_to_hass()
        configure_shutter_entity(
            self.hass,
            entity_id=self.entity_id,
            area_id=self._area_id,
        )
        if self._transport_entity_id is None:
            return
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, self._transport_entity_id, self._transport_changed
            )
        )
        self._apply_transport_state(
            self.hass.states.get(self._transport_entity_id), write=False
        )

    @callback
    def _transport_changed(self, event: Event) -> None:
        """Mirror position and operation changes from ESPHome."""
        self._apply_transport_state(event.data.get("new_state"), write=True)

    @callback
    def _apply_transport_state(self, state, *, write: bool) -> None:
        if state is None or state.state in {STATE_UNAVAILABLE, STATE_UNKNOWN}:
            self._attr_available = False
            self._attr_is_opening = False
            self._attr_is_closing = False
            self._attr_is_closed = None
        else:
            self._attr_available = True
            self._attr_is_opening = state.state == CoverState.OPENING
            self._attr_is_closing = state.state == CoverState.CLOSING
            self._attr_is_closed = state.state == CoverState.CLOSED
            position = state.attributes.get("current_position")
            if isinstance(position, (int, float)):
                self._attr_current_cover_position = max(
                    0, min(100, round(float(position)))
                )
            tilt = state.attributes.get("current_tilt_position")
            if self._is_venetian and isinstance(tilt, (int, float)):
                self._attr_current_cover_tilt_position = max(
                    0, min(100, round(float(tilt)))
                )
        if write:
            self.async_write_ha_state()

    async def _async_control(self, command: str, position: float = 0.0) -> None:
        await self._runtime.async_call(
            "control",
            {
                "slot": self._slot,
                "command": command,
                "position_percent": position,
            },
            "command_sent",
        )

    async def async_open_cover(self, **kwargs) -> None:
        """Open the shutter."""
        await self._async_control("open")

    async def async_close_cover(self, **kwargs) -> None:
        """Close the shutter."""
        await self._async_control("close")

    async def async_stop_cover(self, **kwargs) -> None:
        """Stop the shutter without recalling MY."""
        await self._async_control("stop")

    async def async_set_cover_position(self, **kwargs) -> None:
        """Move the shutter to an estimated percentage."""
        await self._async_control("position", float(kwargs[ATTR_POSITION]))

    async def async_open_cover_tilt(self, **kwargs) -> None:
        """Rotate Venetian slats to the configured open endpoint."""
        await self._async_control("tilt_position", 100.0)

    async def async_close_cover_tilt(self, **kwargs) -> None:
        """Rotate Venetian slats to the configured closed endpoint."""
        await self._async_control("tilt_position", 0.0)

    async def async_stop_cover_tilt(self, **kwargs) -> None:
        """Cancel any remaining queued slat detents."""
        await self._async_control("tilt_stop")

    async def async_set_cover_tilt_position(self, **kwargs) -> None:
        """Rotate Venetian slats to an estimated percentage."""
        await self._async_control(
            "tilt_position", float(kwargs[ATTR_TILT_POSITION])
        )
