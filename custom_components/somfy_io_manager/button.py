"""Per-shutter MY buttons."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

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
from .runtime import SomfyIOManagerRuntime


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create one MY button for every active shutter."""
    runtime = hass.data[DOMAIN][entry.entry_id][DATA_RUNTIME]
    async_add_entities(
        SomfyMyButton(runtime, entry, shutter)
        for shutter in entry.options.get(CONF_SHUTTERS, [])
        if shutter.get(CONF_STATE) == STATE_ACTIVE
    )


class SomfyMyButton(ButtonEntity):
    """Recall a motor's native MY favourite."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:heart"
    _attr_name = "MY position"
    _attr_should_poll = False

    def __init__(
        self,
        runtime: SomfyIOManagerRuntime,
        entry: ConfigEntry,
        shutter: dict,
    ) -> None:
        object_id = shutter_object_id(shutter)
        shutter_id = ensure_shutter_id(shutter)
        self._runtime = runtime
        self._slot = int(shutter[CONF_SLOT])
        self._area_id = shutter.get(CONF_AREA_ID)
        self._attr_unique_id = f"{entry.entry_id}-{shutter_id}-my"
        self._attr_suggested_object_id = f"{object_id}_my_position"
        self._attr_device_info = shutter_device_info(entry, shutter)

    async def async_added_to_hass(self) -> None:
        """Assign the button to the shutter device and requested area."""
        await super().async_added_to_hass()
        configure_shutter_entity(
            self.hass,
            entity_id=self.entity_id,
            area_id=self._area_id,
        )

    async def async_press(self) -> None:
        """Send the deterministic native-MY sequence through the bridge."""
        await self._runtime.async_call(
            "control",
            {"slot": self._slot, "command": "my", "position_percent": 0.0},
            "command_sent",
        )
