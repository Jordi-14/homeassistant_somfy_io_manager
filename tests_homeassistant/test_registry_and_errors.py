"""Exercise registry ownership and entity actions against Home Assistant."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.somfy_io_manager as integration
from custom_components.somfy_io_manager.button import SomfyMyButton
from custom_components.somfy_io_manager.const import (
    CONF_AREA_ID,
    CONF_BACKUP_ENTITY_ID,
    CONF_DEVICE_NAME,
    CONF_ESPHOME_ENTRY_ID,
    CONF_NAME,
    CONF_SHUTTER_ID,
    CONF_SHUTTERS,
    CONF_SLOT,
    CONF_STATE,
    CONF_STATUS_ENTITY_ID,
    DOMAIN,
    STATE_ACTIVE,
)
from custom_components.somfy_io_manager.cover import SomfyManagedCover
from custom_components.somfy_io_manager.entity import (
    initialize_shutter_device,
)
from custom_components.somfy_io_manager.entity_migration import (
    configure_transport_cover,
)
from custom_components.somfy_io_manager.runtime import (
    ManagerRejected,
    ManagerUnavailable,
    SomfyIOManagerRuntime,
)
from custom_components.somfy_io_manager.sensor import SomfyRemoteSensor


@pytest.fixture(autouse=True)
def custom_integrations(enable_custom_integrations):
    yield


def _entries(hass, *, area_id=None):
    esphome = MockConfigEntry(domain="esphome")
    esphome.add_to_hass(hass)
    shutter = {
        CONF_NAME: "Living room",
        CONF_SLOT: 0,
        CONF_SHUTTER_ID: "living-room",
        CONF_STATE: STATE_ACTIVE,
        CONF_AREA_ID: area_id,
    }
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data={
            CONF_ESPHOME_ENTRY_ID: esphome.entry_id,
            CONF_DEVICE_NAME: "bridge",
            CONF_STATUS_ENTITY_ID: "sensor.bridge_somfy_status",
            CONF_BACKUP_ENTITY_ID: "sensor.bridge_somfy_backup",
        },
        options={CONF_SHUTTERS: [shutter]},
    )
    entry.add_to_hass(hass)
    return esphome, entry, shutter


def _transport(hass, esphome, *, slot=0, disabled_by=None):
    return er.async_get(hass).async_get_or_create(
        "cover",
        "esphome",
        f"transport-{slot}",
        config_entry=esphome,
        suggested_object_id=f"bridge_somfy_shutter_slot_{slot + 1}",
        original_name=f"Somfy Shutter Slot {slot + 1}",
        disabled_by=disabled_by,
    )


def _settings(entry):
    return (
        entry.entity_id,
        entry.name,
        entry.area_id,
        entry.disabled_by,
        entry.hidden_by,
    )


async def test_startup_and_reload_preserve_esphome_entities(hass, monkeypatch):
    esphome, entry, _ = _entries(hass)
    registry = er.async_get(hass)
    active = _transport(hass, esphome)
    inactive = _transport(hass, esphome, slot=1)
    area = ar.async_get(hass).async_create("User area")
    active = registry.async_update_entity(
        active.entity_id,
        new_entity_id="cover.user_transport",
        name="My transport name",
        area_id=area.id,
        hidden_by=er.RegistryEntryHider.USER,
    )
    inactive = registry.async_update_entity(
        inactive.entity_id,
        disabled_by=er.RegistryEntryDisabler.USER,
    )
    button = registry.async_get_or_create(
        "button",
        "esphome",
        "native-my",
        config_entry=esphome,
        original_name="Somfy Shutter Slot 1 MY position",
    )
    before = {item.id: _settings(item) for item in (active, inactive, button)}
    runtime = SimpleNamespace(
        esphome_entry_id=esphome.entry_id,
        async_setup=AsyncMock(),
    )
    monkeypatch.setattr(integration, "SomfyIOManagerRuntime", lambda *_: runtime)
    monkeypatch.setattr(hass.config_entries, "async_forward_entry_setups", AsyncMock())

    for _ in range(2):
        assert await integration.async_setup_entry(hass, entry)
        after = {
            item.id: _settings(item)
            for item in er.async_entries_for_config_entry(registry, esphome.entry_id)
        }
        assert after == before


async def test_delayed_transport_discovery_only_reloads_the_manager(hass, monkeypatch):
    esphome, entry, _ = _entries(hass)
    pending = []
    monkeypatch.setattr(
        integration,
        "async_call_later",
        lambda _hass, _delay, callback: pending.append(callback) or (lambda: None),
    )
    reload_entry = AsyncMock(return_value=True)
    monkeypatch.setattr(hass.config_entries, "async_reload", reload_entry)
    runtime = SimpleNamespace(esphome_entry_id=esphome.entry_id)

    integration._wait_for_transport_covers(hass, entry, runtime)
    assert len(pending) == 1
    source = _transport(hass, esphome)
    before = _settings(source)
    pending.pop()(None)
    await hass.async_block_till_done()

    reload_entry.assert_awaited_once_with(entry.entry_id)
    assert _settings(er.async_get(hass).async_get(source.entity_id)) == before


@pytest.mark.parametrize("initial_area", [False, True])
@pytest.mark.parametrize("user_area", [False, True])
async def test_pairing_area_is_applied_once_and_platforms_preserve_changes(
    hass, initial_area, user_area
):
    areas = ar.async_get(hass)
    pairing_area = areas.async_create("Pairing area")
    changed_area = areas.async_create("Changed area")
    entity_area = areas.async_create("Entity area")
    _, entry, shutter = _entries(
        hass, area_id=pairing_area.id if initial_area else None
    )
    device = initialize_shutter_device(hass, entry, shutter)
    devices = dr.async_get(hass)
    assert device.area_id == (pairing_area.id if initial_area else None)
    devices.async_update_device(
        device.id, area_id=changed_area.id if user_area else None
    )
    initialize_shutter_device(hass, entry, shutter)

    runtime = SomfyIOManagerRuntime(hass, entry)
    entities = (
        SomfyManagedCover(runtime, entry, shutter, None),
        SomfyMyButton(runtime, entry, shutter),
        SomfyRemoteSensor(runtime, entry, shutter),
    )
    for domain, entity in zip(("cover", "button", "sensor"), entities, strict=True):
        registry = er.async_get(hass)
        registered = registry.async_get_or_create(
            domain, DOMAIN, entity.unique_id, config_entry=entry, device_id=device.id
        )
        registry.async_update_entity(registered.entity_id, name=f"User {domain} name")
        registry.async_update_entity(registered.entity_id, area_id=entity_area.id)
        entity.hass = hass
        entity.entity_id = registered.entity_id
        await entity.async_added_to_hass()
        assert registry.async_get(registered.entity_id).area_id == entity_area.id
        assert registry.async_get(registered.entity_id).name == f"User {domain} name"
        assert devices.async_get(device.id).area_id == (
            changed_area.id if user_area else None
        )
        if isinstance(entity, SomfyRemoteSensor):
            entity._remove_listener()


async def test_deleted_pairing_area_does_not_prevent_device_creation(hass):
    _, entry, shutter = _entries(hass, area_id="deleted-area")
    device = initialize_shutter_device(hass, entry, shutter)
    assert device is not None
    assert device.area_id is None


@pytest.mark.parametrize("customization", ["name", "area", "disabled", "hidden", "id"])
async def test_explicit_setup_preserves_customized_transports(hass, customization):
    esphome, _, _ = _entries(hass)
    registry = er.async_get(hass)
    source = _transport(hass, esphome, disabled_by=er.RegistryEntryDisabler.INTEGRATION)
    changes = {
        "name": {"name": "User name"},
        "area": {"area_id": ar.async_get(hass).async_create("User area").id},
        "disabled": {"disabled_by": er.RegistryEntryDisabler.USER},
        "hidden": {"hidden_by": er.RegistryEntryHider.USER},
        "id": {"new_entity_id": "cover.user_chosen_id"},
    }
    source = registry.async_update_entity(source.entity_id, **changes[customization])
    before = _settings(source)
    configured = configure_transport_cover(
        registry,
        esphome_entry_id=esphome.entry_id,
        device_name="bridge",
        slot=0,
        active=True,
    )
    assert _settings(configured) == before


async def test_explicit_setup_only_enables_an_untouched_transport(hass):
    esphome, _, _ = _entries(hass)
    source = _transport(hass, esphome, disabled_by=er.RegistryEntryDisabler.INTEGRATION)
    configured = configure_transport_cover(
        er.async_get(hass),
        esphome_entry_id=esphome.entry_id,
        device_name="bridge",
        slot=0,
        active=True,
    )
    assert configured.disabled_by is None
    assert configured.entity_id == source.entity_id
    assert configured.name == source.name
    assert configured.area_id == source.area_id
    assert configured.hidden_by == source.hidden_by


async def test_explicit_setup_can_enable_a_previous_integration_transport(hass):
    esphome, _, _ = _entries(hass)
    registry = er.async_get(hass)
    source = _transport(hass, esphome, disabled_by=er.RegistryEntryDisabler.INTEGRATION)
    source = registry.async_update_entity(
        source.entity_id,
        new_entity_id="cover.bridge_slot_01_transport",
        name="Slot 1 transport",
        hidden_by=er.RegistryEntryHider.INTEGRATION,
    )
    configured = configure_transport_cover(
        registry,
        esphome_entry_id=esphome.entry_id,
        device_name="bridge",
        slot=0,
        active=True,
    )
    assert configured.disabled_by is None
    assert configured.hidden_by == source.hidden_by
    assert configured.entity_id == source.entity_id


@pytest.mark.parametrize(
    ("entity_type", "method", "arguments"),
    [
        (SomfyManagedCover, "async_open_cover", {}),
        (SomfyManagedCover, "async_close_cover", {}),
        (SomfyManagedCover, "async_stop_cover", {}),
        (SomfyManagedCover, "async_set_cover_position", {"position": 50}),
        (SomfyMyButton, "async_press", {}),
    ],
)
async def test_entity_actions_raise_a_translated_homeassistant_error(
    hass, entity_type, method, arguments
):
    _, entry, shutter = _entries(hass)
    runtime = SomfyIOManagerRuntime(hass, entry)
    entity = (
        entity_type(runtime, entry, shutter, None)
        if entity_type is SomfyManagedCover
        else entity_type(runtime, entry, shutter)
    )
    with pytest.raises(HomeAssistantError) as caught:
        await getattr(entity, method)(**arguments)
    assert isinstance(caught.value, ManagerUnavailable)
    assert caught.value.translation_domain == DOMAIN
    assert caught.value.translation_key == "manager_unavailable"


async def test_timeout_is_readable_and_does_not_repeat_the_command(hass):
    _, entry, _ = _entries(hass)
    runtime = SomfyIOManagerRuntime(hass, entry)
    calls = []

    async def handler(call):
        calls.append(call)

    hass.services.async_register("esphome", runtime.service_name("control"), handler)
    with pytest.raises(ManagerUnavailable) as caught:
        await runtime.async_call("control", {"slot": 0}, "command_sent", timeout=0.001)
    assert caught.value.translation_key == "manager_unavailable"
    assert len(calls) == 1


async def test_rejected_command_keeps_its_detail_and_translation(hass):
    _, entry, _ = _entries(hass)
    runtime = SomfyIOManagerRuntime(hass, entry)

    async def handler(_call):
        hass.states.async_set(
            runtime.status_entity_id,
            json.dumps(
                {
                    "v": 1,
                    "event": 1,
                    "slot": 0,
                    "action": "error",
                    "detail": "not_calibrated",
                }
            ),
        )

    hass.services.async_register("esphome", runtime.service_name("control"), handler)
    with pytest.raises(ManagerRejected) as caught:
        await runtime.async_call("control", {"slot": 0}, "command_sent")
    assert caught.value.detail == "not_calibrated"
    assert caught.value.translation_key == "manager_rejected"
    assert caught.value.translation_placeholders == {"detail": "not_calibrated"}
