"""GUI commissioning flow for managed Somfy IO shutters."""

from __future__ import annotations

import logging
import re
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector

from .const import (
    CONF_AREA_ID,
    CONF_BACKUP_ENTITY_ID,
    CONF_CLOSE_SECONDS,
    CONF_COVER_TYPE,
    CONF_DEVICE_NAME,
    CONF_ESPHOME_ENTRY_ID,
    CONF_MY_PERCENT,
    CONF_MY_TILT_STEP,
    CONF_NAME,
    CONF_OPEN_SECONDS,
    CONF_REMOTE,
    CONF_REMOTE_ALIASES,
    CONF_SHUTTER_ID,
    CONF_SHUTTER_IDS,
    CONF_SHUTTERS,
    CONF_SLOT,
    CONF_STATE,
    CONF_STATUS_ENTITY_ID,
    CONF_TARGET_SLOT,
    CONF_TILT_INVERTED,
    CONF_TILT_STEPS,
    COVER_TYPE_SHUTTER,
    COVER_TYPE_VENETIAN,
    DATA_RUNTIME,
    DOMAIN,
    MAX_SHUTTER_SLOTS,
    STATE_ACTIVE,
    STATE_UNCERTAIN,
)
from .entity import ensure_shutter_id
from .entity_migration import (
    configure_transport_cover,
    find_transport_cover,
)
from .runtime import (
    ManagerError,
    ManagerRejected,
    SomfyIOManagerRuntime,
)

_LOGGER = logging.getLogger(__name__)

CONF_BRIDGE = "bridge"
CONF_REMOTE_PRESSED = "remote_pressed"
CONF_PROGRAM_JOGGED = "program_jogged"
CONF_PAIRING_JOGGED = "pairing_jogged"
CONF_ATTEMPT = "attempt"
CONF_RESUME_ACTION = "resume_action"
CONF_RETRY_SETUP = "retry_setup"
CONF_GROUP_REMOTE = "group_remote"
CONF_CONFIRM_REMOVE = "confirm_remove"

ACTION_CONTINUE = "continue"
ACTION_DISCARD = "discard"


class FlowError(Exception):
    """A user-correctable commissioning flow problem."""

    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


def _service_prefix(device_name: str) -> str:
    return device_name.replace("-", "_")


def _normalize_remote(value: Any) -> str:
    """Use a stable, human-readable 24-bit remote identity."""
    text = str(value or "").strip()
    if re.fullmatch(r"0[xX][0-9a-fA-F]{6}", text) is None:
        return ""
    return f"0x{text[2:].upper()}"


def _manager_entities(
    hass: HomeAssistant, esphome_entry_id: str
) -> tuple[str | None, str | None]:
    """Find the two manager state entities owned by an ESPHome entry."""
    registry = er.async_get(hass)
    status_entity = None
    backup_entity = None
    for entity in er.async_entries_for_config_entry(registry, esphome_entry_id):
        # HA 2026 exposes ESPHome text sensors in the sensor domain. Retain the
        # former text_sensor domain for installations upgraded from older HA.
        if not entity.entity_id.startswith(("sensor.", "text_sensor.")):
            continue
        searchable = (
            " ".join(
                value
                for value in (entity.original_name, entity.name, entity.entity_id)
                if value
            )
            .lower()
            .replace("_", " ")
            .replace("-", " ")
        )
        if "commissioning status" in searchable:
            status_entity = entity.entity_id
        elif "encrypted controller backup" in searchable:
            backup_entity = entity.entity_id
    return status_entity, backup_entity


def _eligible_bridges(hass: HomeAssistant) -> dict[str, dict[str, str]]:
    """Return loaded ESPHome devices exposing the complete manager API."""
    bridges: dict[str, dict[str, str]] = {}
    for entry in hass.config_entries.async_entries("esphome"):
        device_name = entry.data.get(CONF_DEVICE_NAME)
        if not isinstance(device_name, str):
            continue
        status_entity, backup_entity = _manager_entities(hass, entry.entry_id)
        if status_entity is None or backup_entity is None:
            continue
        prefix = _service_prefix(device_name)
        if not all(
            hass.services.has_service("esphome", f"{prefix}_somfy_{suffix}")
            for suffix in (
                "commission",
                "calibrate",
                "control",
                "restore",
                "move",
                "swap",
                "venetian",
                "remote_alias",
            )
        ):
            continue
        bridges[entry.entry_id] = {
            "title": entry.title or device_name,
            CONF_DEVICE_NAME: device_name,
            CONF_STATUS_ENTITY_ID: status_entity,
            CONF_BACKUP_ENTITY_ID: backup_entity,
        }
    return bridges


def _number_selector(
    minimum: float, maximum: float, step: float, unit: str | None = None
) -> selector.NumberSelector:
    config = selector.NumberSelectorConfig(
        min=minimum,
        max=maximum,
        step=step,
        mode=selector.NumberSelectorMode.BOX,
    )
    if unit is not None:
        config["unit_of_measurement"] = unit
    return selector.NumberSelector(config)


def _details_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    defaults = defaults or {}
    fields: dict[Any, Any] = {
        vol.Required(
            CONF_NAME, default=defaults.get(CONF_NAME, "")
        ): selector.TextSelector(),
        vol.Optional(CONF_AREA_ID): selector.AreaSelector(),
        vol.Required(
            CONF_OPEN_SECONDS, default=defaults.get(CONF_OPEN_SECONDS, 30.0)
        ): _number_selector(1.0, 300.0, 0.1, "s"),
        vol.Required(
            CONF_CLOSE_SECONDS, default=defaults.get(CONF_CLOSE_SECONDS, 30.0)
        ): _number_selector(1.0, 300.0, 0.1, "s"),
        vol.Required(
            CONF_MY_PERCENT, default=defaults.get(CONF_MY_PERCENT, 50.0)
        ): _number_selector(0.0, 100.0, 1.0, "%"),
        vol.Required(
            CONF_COVER_TYPE,
            default=defaults.get(CONF_COVER_TYPE, COVER_TYPE_SHUTTER),
        ): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[COVER_TYPE_SHUTTER, COVER_TYPE_VENETIAN],
                translation_key="cover_type",
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        ),
        vol.Required(
            CONF_TILT_STEPS, default=defaults.get(CONF_TILT_STEPS, 12)
        ): _number_selector(1, 254, 1),
        vol.Required(
            CONF_MY_TILT_STEP, default=defaults.get(CONF_MY_TILT_STEP, 6)
        ): _number_selector(0, 254, 1),
        vol.Required(
            CONF_TILT_INVERTED,
            default=defaults.get(CONF_TILT_INVERTED, True),
        ): selector.BooleanSelector(),
    }
    return vol.Schema(fields)


def _slot_details_schema(
    defaults: dict[str, Any] | None = None, *, include_target: bool = False
) -> vol.Schema:
    """Build shutter details with one-based firmware slot selectors."""
    defaults = defaults or {}
    fields: dict[Any, Any] = {
        vol.Required(CONF_SLOT, default=defaults.get(CONF_SLOT, 1)): _number_selector(
            1, MAX_SHUTTER_SLOTS, 1
        )
    }
    if include_target:
        fields[
            vol.Required(
                CONF_TARGET_SLOT,
                default=defaults.get(CONF_TARGET_SLOT, defaults.get(CONF_SLOT, 1)),
            )
        ] = _number_selector(1, MAX_SHUTTER_SLOTS, 1)
    fields.update(_details_schema(defaults).schema)
    return vol.Schema(fields)


def _validate_details(user_input: dict[str, Any]) -> dict[str, Any]:
    details = dict(user_input)
    name = str(details[CONF_NAME]).strip()
    if not name:
        raise FlowError("invalid_name")
    details[CONF_NAME] = name
    ensure_shutter_id(details)
    details[CONF_OPEN_SECONDS] = float(details[CONF_OPEN_SECONDS])
    details[CONF_CLOSE_SECONDS] = float(details[CONF_CLOSE_SECONDS])
    details[CONF_MY_PERCENT] = float(details[CONF_MY_PERCENT])
    details[CONF_COVER_TYPE] = str(
        details.get(CONF_COVER_TYPE, COVER_TYPE_SHUTTER)
    )
    if details[CONF_COVER_TYPE] not in {
        COVER_TYPE_SHUTTER,
        COVER_TYPE_VENETIAN,
    }:
        raise FlowError("invalid_cover_type")
    details[CONF_TILT_STEPS] = int(details.get(CONF_TILT_STEPS, 12))
    details[CONF_MY_TILT_STEP] = int(details.get(CONF_MY_TILT_STEP, 6))
    details[CONF_TILT_INVERTED] = bool(
        details.get(CONF_TILT_INVERTED, True)
    )
    if (
        details[CONF_COVER_TYPE] == COVER_TYPE_VENETIAN
        and details[CONF_MY_TILT_STEP] > details[CONF_TILT_STEPS]
    ):
        raise FlowError("invalid_my_tilt_step")
    return details


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Connect the GUI manager to one ESPHome radio bridge."""

    VERSION = 3

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        bridges = _eligible_bridges(self.hass)
        if not bridges:
            return self.async_abort(reason="no_compatible_bridges")

        errors: dict[str, str] = {}
        if user_input is not None:
            bridge_id = user_input[CONF_BRIDGE]
            bridge = bridges.get(bridge_id)
            if bridge is None:
                errors["base"] = "bridge_unavailable"
            else:
                await self.async_set_unique_id(bridge_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Somfy IO – {bridge['title']}",
                    data={
                        CONF_ESPHOME_ENTRY_ID: bridge_id,
                        CONF_DEVICE_NAME: bridge[CONF_DEVICE_NAME],
                        CONF_STATUS_ENTITY_ID: bridge[CONF_STATUS_ENTITY_ID],
                        CONF_BACKUP_ENTITY_ID: bridge[CONF_BACKUP_ENTITY_ID],
                    },
                    options={CONF_SHUTTERS: [], CONF_REMOTE_ALIASES: []},
                )

        choices = [
            selector.SelectOptionDict(value=entry_id, label=data["title"])
            for entry_id, data in bridges.items()
        ]
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_BRIDGE): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=choices,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> SomfyOptionsFlow:
        return SomfyOptionsFlow(config_entry)


class SomfyOptionsFlow(config_entries.OptionsFlowWithConfigEntry):
    """Commission, import, and calibrate shutters without editing YAML."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        super().__init__(config_entry)
        self._draft: dict[str, Any] = {}
        self._slot: int | None = None
        self._replacing_slot: int | None = None
        self._alias_remote: str | None = None
        self._alias_shutter_ids: list[str] = []

    @property
    def _runtime(self) -> SomfyIOManagerRuntime:
        return self.hass.data[DOMAIN][self.config_entry.entry_id][DATA_RUNTIME]

    def _shutters(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self.config_entry.options.get(CONF_SHUTTERS, [])]

    def _remote_aliases(self) -> list[dict[str, Any]]:
        """Return a mutable copy of receive-only group mappings."""
        aliases = []
        for item in self.config_entry.options.get(CONF_REMOTE_ALIASES, []):
            if not isinstance(item, dict) or not isinstance(
                item.get(CONF_SHUTTER_IDS), list
            ):
                continue
            remote = _normalize_remote(item.get(CONF_REMOTE))
            if not remote:
                continue
            aliases.append(
                {
                    CONF_REMOTE: remote,
                    CONF_SHUTTER_IDS: [
                        str(value) for value in item[CONF_SHUTTER_IDS]
                    ],
                }
            )
        return aliases

    def _active_shutters(self) -> list[dict[str, Any]]:
        shutters = [
            shutter
            for shutter in self._shutters()
            if shutter.get(CONF_STATE) == STATE_ACTIVE
        ]
        for shutter in shutters:
            ensure_shutter_id(shutter)
        return shutters

    def _shutter_alias_options(self) -> list[selector.SelectOptionDict]:
        return [
            selector.SelectOptionDict(
                value=str(shutter[CONF_SHUTTER_ID]),
                label=f"{shutter[CONF_NAME]} (slot {int(shutter[CONF_SLOT]) + 1})",
            )
            for shutter in self._active_shutters()
        ]

    def _selected_shutter_ids(self, value: Any) -> list[str]:
        selected = [value] if isinstance(value, str) else list(value or [])
        valid = {
            str(shutter[CONF_SHUTTER_ID]) for shutter in self._active_shutters()
        }
        result = sorted({str(item) for item in selected if str(item) in valid})
        if not result:
            raise FlowError("select_at_least_one_shutter")
        return result

    def _slots_csv(self, shutter_ids: list[str]) -> str:
        selected = set(shutter_ids)
        slots = sorted(
            int(shutter[CONF_SLOT])
            for shutter in self._active_shutters()
            if str(shutter[CONF_SHUTTER_ID]) in selected
        )
        if len(slots) != len(selected):
            raise FlowError("group_shutter_missing")
        return ",".join(str(slot) for slot in slots)

    def _group_remote_options(self) -> list[selector.SelectOptionDict]:
        names = {
            str(shutter[CONF_SHUTTER_ID]): str(shutter[CONF_NAME])
            for shutter in self._active_shutters()
        }
        options = []
        for alias in self._remote_aliases():
            affected = [
                names[shutter_id]
                for shutter_id in alias[CONF_SHUTTER_IDS]
                if shutter_id in names
            ]
            options.append(
                selector.SelectOptionDict(
                    value=alias[CONF_REMOTE],
                    label=f"{alias[CONF_REMOTE]} — {', '.join(affected)}",
                )
            )
        return options

    def _save_remote_alias(
        self, remote: str, shutter_ids: list[str]
    ) -> config_entries.ConfigFlowResult:
        aliases = [
            alias
            for alias in self._remote_aliases()
            if alias[CONF_REMOTE] != remote
        ]
        aliases.append(
            {CONF_REMOTE: remote, CONF_SHUTTER_IDS: sorted(set(shutter_ids))}
        )
        aliases.sort(key=lambda item: item[CONF_REMOTE])
        return self.async_create_entry(
            title="",
            data={
                **self.config_entry.options,
                CONF_REMOTE_ALIASES: aliases,
            },
        )

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        del user_input
        menu = ["add_shutter", "import_existing"]
        if self._shutters():
            menu.extend(("edit_calibration", "move_shutter"))
        if len(self._shutters()) >= 2:
            menu.append("swap_shutters")
        if self._active_shutters():
            menu.append("add_group_remote")
        if self._remote_aliases():
            menu.extend(("edit_group_remote", "remove_group_remote"))
        if self._runtime.pending:
            menu.append("resume_attempt")
        return self.async_show_menu(step_id="init", menu_options=menu)

    async def async_step_add_shutter(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                self._draft = _validate_details(user_input)
                requested_slot = int(user_input[CONF_SLOT]) - 1
                status = await self._runtime.async_call(
                    "commission",
                    {"action": "stage", "slot": requested_slot},
                    "staged",
                )
                self._slot = int(status["slot"])
                self._draft[CONF_SLOT] = self._slot
                self._draft[CONF_STATE] = "staged"
                self._runtime.async_remember_pending(self._slot, self._draft)
                await self._runtime.async_call(
                    "commission",
                    {"action": "discover", "slot": self._slot},
                    "waiting_remote",
                )
                return await self.async_step_capture_remote()
            except FlowError as err:
                errors["base"] = err.key
            except ManagerRejected as err:
                errors["base"] = (
                    "target_slot_not_empty"
                    if err.detail == "target_slot_not_empty"
                    else "manager_unavailable"
                )
            except ManagerError:
                _LOGGER.exception("Could not stage a Somfy IO controller")
                errors["base"] = "manager_unavailable"

        return self.async_show_form(
            step_id="add_shutter",
            data_schema=_slot_details_schema(user_input),
            errors=errors,
        )

    async def async_step_capture_remote(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input.get(CONF_REMOTE_PRESSED):
                errors["base"] = "press_remote_first"
            else:
                try:
                    # The discovery result is persisted in the firmware slot.
                    # Query that durable state instead of inspecting the last
                    # shared status-sensor value, which another bridge event
                    # may have overwritten before the user submits this form.
                    status = await self._runtime.async_call(
                        "commission",
                        {"action": "query", "slot": self._slot},
                        "slot",
                    )
                    remote = str(status.get("remote") or "")
                    if remote not in {"", "0x000000"}:
                        self._draft["remote"] = remote
                        self._draft["rssi"] = status.get("rssi")
                        self._draft[CONF_STATE] = "remote_detected"
                        self._runtime.async_remember_pending(self._slot, self._draft)
                        return await self.async_step_prepare_pairing()

                    errors["base"] = "remote_not_detected"
                    await self._runtime.async_call(
                        "commission",
                        {"action": "discover", "slot": self._slot},
                        "waiting_remote",
                    )
                except ManagerError:
                    errors["base"] = "manager_unavailable"

        return self.async_show_form(
            step_id="capture_remote",
            data_schema=vol.Schema(
                {vol.Required(CONF_REMOTE_PRESSED, default=False): bool}
            ),
            errors=errors,
            description_placeholders={"slot": str((self._slot or 0) + 1)},
        )

    async def async_step_prepare_pairing(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input.get(CONF_PROGRAM_JOGGED):
                errors["base"] = "program_jog_required"
            else:
                try:
                    await self._runtime.async_call(
                        "commission",
                        {"action": "arm", "slot": self._slot},
                        "armed",
                    )
                    await self._runtime.async_call(
                        "commission",
                        {"action": "pair", "slot": self._slot},
                        "pair_sent",
                    )
                except ManagerError:
                    _LOGGER.exception("Somfy IO pairing transmission failed")
                    errors["base"] = "manager_unavailable"
                else:
                    self._draft[CONF_STATE] = STATE_UNCERTAIN
                    self._runtime.async_remember_pending(self._slot, self._draft)
                    return await self.async_step_confirm_pairing()

        return self.async_show_form(
            step_id="prepare_pairing",
            data_schema=vol.Schema(
                {vol.Required(CONF_PROGRAM_JOGGED, default=False): bool}
            ),
            errors=errors,
            description_placeholders={
                "name": self._draft.get(CONF_NAME, "shutter"),
                "remote": str(self._draft.get("remote", "")),
            },
        )

    async def async_step_confirm_pairing(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            if not user_input.get(CONF_PAIRING_JOGGED):
                self._draft[CONF_STATE] = STATE_UNCERTAIN
                return self._save_uncertain()
            try:
                await self._runtime.async_call(
                    "commission",
                    {"action": "confirm", "slot": self._slot},
                    "active",
                )
            except ManagerError:
                return self.async_show_form(
                    step_id="confirm_pairing",
                    data_schema=vol.Schema(
                        {vol.Required(CONF_PAIRING_JOGGED, default=True): bool}
                    ),
                    errors={"base": "manager_unavailable"},
                )
            self._draft[CONF_STATE] = STATE_ACTIVE
            return await self._async_finalize()

        return self.async_show_form(
            step_id="confirm_pairing",
            data_schema=vol.Schema(
                {vol.Required(CONF_PAIRING_JOGGED, default=True): bool}
            ),
            description_placeholders={"name": self._draft.get(CONF_NAME, "shutter")},
        )

    async def async_step_import_existing(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        defaults = dict(user_input or {})
        if user_input is not None:
            try:
                self._draft = _validate_details(user_input)
                source_slot = int(user_input[CONF_SLOT]) - 1
                target_slot = int(user_input[CONF_TARGET_SLOT]) - 1
                status = await self._runtime.async_call(
                    "commission",
                    {"action": "query", "slot": source_slot},
                    "slot",
                )
                if status.get("state") != STATE_ACTIVE:
                    raise FlowError("slot_not_active")
                if target_slot != source_slot:
                    await self._runtime.async_call(
                        "move",
                        {"slot": source_slot, "target_slot": target_slot},
                        "moved",
                    )
                    self._replacing_slot = source_slot
                    self._runtime.async_move_backup(source_slot, target_slot)
                self._slot = target_slot
                self._draft.pop(CONF_TARGET_SLOT, None)
                self._draft[CONF_SLOT] = self._slot
                self._draft[CONF_STATE] = STATE_ACTIVE
                return await self._async_finalize()
            except FlowError as err:
                errors["base"] = err.key
            except ManagerRejected as err:
                if err.detail in {"invalid_slot", "source_slot_not_active"}:
                    errors["base"] = "slot_not_active"
                elif err.detail == "target_slot_not_empty":
                    errors["base"] = "target_slot_not_empty"
                else:
                    errors["base"] = "manager_unavailable"
            except ManagerError:
                errors["base"] = "manager_unavailable"

        return self.async_show_form(
            step_id="import_existing",
            data_schema=_slot_details_schema(defaults, include_target=True),
            errors=errors,
        )

    async def async_step_resume_attempt(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        pending = self._runtime.pending
        if not pending:
            return self.async_abort(reason="no_pending_attempts")
        errors: dict[str, str] = {}
        if user_input is not None:
            self._slot = int(user_input[CONF_ATTEMPT])
            self._draft = dict(pending[self._slot])
            try:
                status = await self._runtime.async_call(
                    "commission",
                    {"action": "query", "slot": self._slot},
                    "slot",
                )
                state = status.get("state")
                if user_input[CONF_RESUME_ACTION] == ACTION_DISCARD:
                    if state != "staged":
                        raise FlowError("cannot_discard_after_pairing")
                    await self._runtime.async_call(
                        "commission",
                        {"action": "discard", "slot": self._slot},
                        "discarded",
                    )
                    self._runtime.async_clear_pending(self._slot)
                    return self.async_create_entry(
                        title="", data=self.config_entry.options
                    )
                if state == "staged":
                    await self._runtime.async_call(
                        "commission",
                        {"action": "discover", "slot": self._slot},
                        "waiting_remote",
                    )
                    return await self.async_step_capture_remote()
                if state == "pair_sent":
                    return await self.async_step_confirm_pairing()
                if state == STATE_ACTIVE:
                    self._draft[CONF_STATE] = STATE_ACTIVE
                    return await self._async_finalize()
                raise FlowError("attempt_not_resumable")
            except FlowError as err:
                errors["base"] = err.key
            except ManagerError:
                errors["base"] = "manager_unavailable"

        attempt_options = [
            selector.SelectOptionDict(
                value=str(slot),
                label=f"{draft.get(CONF_NAME, 'Shutter')} (slot {slot + 1})",
            )
            for slot, draft in pending.items()
        ]
        return self.async_show_form(
            step_id="resume_attempt",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ATTEMPT): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=attempt_options)
                    ),
                    vol.Required(
                        CONF_RESUME_ACTION, default=ACTION_CONTINUE
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[ACTION_CONTINUE, ACTION_DISCARD],
                            translation_key="resume_action",
                        )
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_edit_calibration(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        shutters = self._shutters()
        if not shutters:
            return self.async_abort(reason="no_managed_shutters")
        if user_input is not None:
            selected_slot = int(user_input[CONF_SLOT])
            self._slot = selected_slot
            self._draft = next(
                item for item in shutters if item[CONF_SLOT] == selected_slot
            )
            return await self.async_step_calibration_values()
        options = [
            selector.SelectOptionDict(value=str(item[CONF_SLOT]), label=item[CONF_NAME])
            for item in shutters
        ]
        return self.async_show_form(
            step_id="edit_calibration",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SLOT): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=options)
                    )
                }
            ),
        )

    async def async_step_move_shutter(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Move an active controller identity to another firmware slot."""
        shutters = self._shutters()
        if not shutters:
            return self.async_abort(reason="no_managed_shutters")
        errors: dict[str, str] = {}
        if user_input is not None:
            source_slot = int(user_input[CONF_SLOT])
            target_slot = int(user_input[CONF_TARGET_SLOT]) - 1
            if source_slot == target_slot:
                errors["base"] = "same_slot"
            else:
                self._draft = next(
                    item for item in shutters if item[CONF_SLOT] == source_slot
                )
                try:
                    await self._runtime.async_call(
                        "move",
                        {"slot": source_slot, "target_slot": target_slot},
                        "moved",
                    )
                    self._replacing_slot = source_slot
                    self._slot = target_slot
                    self._draft[CONF_SLOT] = target_slot
                    self._runtime.async_move_backup(source_slot, target_slot)
                    return await self._async_finalize()
                except ManagerRejected as err:
                    errors["base"] = (
                        "target_slot_not_empty"
                        if err.detail == "target_slot_not_empty"
                        else "manager_unavailable"
                    )
                except ManagerError:
                    errors["base"] = "manager_unavailable"

        options = [
            selector.SelectOptionDict(
                value=str(item[CONF_SLOT]),
                label=f"{item[CONF_NAME]} (slot {item[CONF_SLOT] + 1})",
            )
            for item in shutters
        ]
        return self.async_show_form(
            step_id="move_shutter",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SLOT): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=options)
                    ),
                    vol.Required(CONF_TARGET_SLOT, default=1): _number_selector(
                        1, MAX_SHUTTER_SLOTS, 1
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_calibration_values(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            cover_type = str(user_input[CONF_COVER_TYPE])
            tilt_steps = int(user_input[CONF_TILT_STEPS])
            my_tilt_step = int(user_input[CONF_MY_TILT_STEP])
            if (
                cover_type == COVER_TYPE_VENETIAN
                and my_tilt_step > tilt_steps
            ):
                errors["base"] = "invalid_my_tilt_step"
            else:
                self._draft.update(
                    {
                        CONF_OPEN_SECONDS: float(user_input[CONF_OPEN_SECONDS]),
                        CONF_CLOSE_SECONDS: float(user_input[CONF_CLOSE_SECONDS]),
                        CONF_MY_PERCENT: float(user_input[CONF_MY_PERCENT]),
                        CONF_COVER_TYPE: cover_type,
                        CONF_TILT_STEPS: tilt_steps,
                        CONF_MY_TILT_STEP: my_tilt_step,
                        CONF_TILT_INVERTED: bool(
                            user_input[CONF_TILT_INVERTED]
                        ),
                    }
                )
                try:
                    await self._async_calibrate()
                    await self._async_configure_venetian()
                    return self._save_active()
                except ManagerError:
                    errors["base"] = "manager_unavailable"
        return self.async_show_form(
            step_id="calibration_values",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_OPEN_SECONDS,
                        default=self._draft[CONF_OPEN_SECONDS],
                    ): _number_selector(1.0, 300.0, 0.1, "s"),
                    vol.Required(
                        CONF_CLOSE_SECONDS,
                        default=self._draft[CONF_CLOSE_SECONDS],
                    ): _number_selector(1.0, 300.0, 0.1, "s"),
                    vol.Required(
                        CONF_MY_PERCENT,
                        default=self._draft[CONF_MY_PERCENT],
                    ): _number_selector(0.0, 100.0, 1.0, "%"),
                    vol.Required(
                        CONF_COVER_TYPE,
                        default=self._draft.get(
                            CONF_COVER_TYPE, COVER_TYPE_SHUTTER
                        ),
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[COVER_TYPE_SHUTTER, COVER_TYPE_VENETIAN],
                            translation_key="cover_type",
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Required(
                        CONF_TILT_STEPS,
                        default=self._draft.get(CONF_TILT_STEPS, 12),
                    ): _number_selector(1, 254, 1),
                    vol.Required(
                        CONF_MY_TILT_STEP,
                        default=self._draft.get(CONF_MY_TILT_STEP, 6),
                    ): _number_selector(0, 254, 1),
                    vol.Required(
                        CONF_TILT_INVERTED,
                        default=self._draft.get(CONF_TILT_INVERTED, True),
                    ): selector.BooleanSelector(),
                }
            ),
            errors=errors,
        )

    async def async_step_swap_shutters(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Exchange two active identities without using an empty slot."""
        shutters = self._shutters()
        if len(shutters) < 2:
            return self.async_abort(reason="not_enough_managed_shutters")
        errors: dict[str, str] = {}
        if user_input is not None:
            first_slot = int(user_input[CONF_SLOT])
            second_slot = int(user_input[CONF_TARGET_SLOT])
            if first_slot == second_slot:
                errors["base"] = "same_slot"
            else:
                first = next(item for item in shutters if item[CONF_SLOT] == first_slot)
                second = next(
                    item for item in shutters if item[CONF_SLOT] == second_slot
                )
                try:
                    registry = er.async_get(self.hass)
                    if (
                        find_transport_cover(
                            registry, self._runtime.esphome_entry_id, first_slot
                        )
                        is None
                        or find_transport_cover(
                            registry, self._runtime.esphome_entry_id, second_slot
                        )
                        is None
                    ):
                        raise FlowError("slot_entity_missing")
                    await self._runtime.async_call(
                        "swap",
                        {"slot": first_slot, "target_slot": second_slot},
                        "swapped",
                    )
                    self._runtime.async_swap_backups(first_slot, second_slot)
                    first[CONF_SLOT] = second_slot
                    second[CONF_SLOT] = first_slot
                    updated = [
                        item
                        for item in shutters
                        if item[CONF_SLOT] not in {first_slot, second_slot}
                    ]
                    updated.extend((first, second))
                    updated.sort(key=lambda item: item[CONF_SLOT])
                    return self.async_create_entry(
                        title="",
                        data={**self.config_entry.options, CONF_SHUTTERS: updated},
                    )
                except FlowError as err:
                    errors["base"] = err.key
                except ManagerRejected as err:
                    errors["base"] = (
                        "both_slots_must_be_active"
                        if err.detail == "both_slots_must_be_active"
                        else "manager_unavailable"
                    )
                except ManagerError:
                    errors["base"] = "manager_unavailable"

        options = [
            selector.SelectOptionDict(
                value=str(item[CONF_SLOT]),
                label=f"{item[CONF_NAME]} (slot {item[CONF_SLOT] + 1})",
            )
            for item in shutters
        ]
        shutter_selector = selector.SelectSelector(
            selector.SelectSelectorConfig(options=options)
        )
        return self.async_show_form(
            step_id="swap_shutters",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SLOT): shutter_selector,
                    vol.Required(CONF_TARGET_SLOT): shutter_selector,
                }
            ),
            errors=errors,
        )

    async def async_step_add_group_remote(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Capture a receive-only remote identity for any set of shutters."""
        if not self._active_shutters():
            return self.async_abort(reason="no_managed_shutters")
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                self._alias_shutter_ids = self._selected_shutter_ids(
                    user_input.get(CONF_SHUTTER_IDS)
                )
                await self._runtime.async_call(
                    "remote_alias",
                    {
                        "action": "discover",
                        "remote": "",
                        "slots": self._slots_csv(self._alias_shutter_ids),
                    },
                    "alias_listening",
                )
                return await self.async_step_capture_group_remote()
            except FlowError as err:
                errors["base"] = err.key
            except ManagerError:
                errors["base"] = "manager_unavailable"

        return self.async_show_form(
            step_id="add_group_remote",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SHUTTER_IDS): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=self._shutter_alias_options(),
                            multiple=True,
                        )
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_capture_group_remote(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Confirm capture, then atomically store the mapping in the ESP."""
        if not self._alias_shutter_ids:
            return await self.async_step_add_group_remote()
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input.get(CONF_REMOTE_PRESSED):
                errors["base"] = "press_group_remote_first"
            else:
                try:
                    status = await self._runtime.async_call(
                        "remote_alias",
                        {"action": "query", "remote": "", "slots": ""},
                        "alias_remote_detected",
                    )
                    remote = _normalize_remote(status.get("remote"))
                    if not remote:
                        raise FlowError("group_remote_not_detected")

                    # Adding an already-known group extends it. Removing
                    # shutters is deliberately reserved for the edit flow.
                    selected = set(self._alias_shutter_ids)
                    for alias in self._remote_aliases():
                        if alias[CONF_REMOTE] == remote:
                            selected.update(alias[CONF_SHUTTER_IDS])
                    self._alias_shutter_ids = sorted(selected)
                    await self._runtime.async_call(
                        "remote_alias",
                        {
                            "action": "set",
                            "remote": remote,
                            "slots": self._slots_csv(self._alias_shutter_ids),
                        },
                        "alias_saved",
                    )
                    return self._save_remote_alias(
                        remote, self._alias_shutter_ids
                    )
                except FlowError as err:
                    errors["base"] = err.key
                except ManagerRejected as err:
                    if err.detail == "remote_alias_not_detected":
                        errors["base"] = "group_remote_not_detected"
                        try:
                            await self._runtime.async_call(
                                "remote_alias",
                                {
                                    "action": "discover",
                                    "remote": "",
                                    "slots": self._slots_csv(
                                        self._alias_shutter_ids
                                    ),
                                },
                                "alias_listening",
                            )
                        except ManagerError:
                            errors["base"] = "manager_unavailable"
                    else:
                        errors["base"] = "manager_unavailable"
                except ManagerError:
                    errors["base"] = "manager_unavailable"

        return self.async_show_form(
            step_id="capture_group_remote",
            data_schema=vol.Schema(
                {vol.Required(CONF_REMOTE_PRESSED, default=False): bool}
            ),
            errors=errors,
            description_placeholders={
                "count": str(len(self._alias_shutter_ids))
            },
        )

    async def async_step_edit_group_remote(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Choose a stored group identity whose membership will be changed."""
        aliases = self._remote_aliases()
        if not aliases:
            return self.async_abort(reason="no_group_remotes")
        if user_input is not None:
            self._alias_remote = _normalize_remote(user_input[CONF_GROUP_REMOTE])
            return await self.async_step_group_remote_targets()
        return self.async_show_form(
            step_id="edit_group_remote",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_GROUP_REMOTE): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=self._group_remote_options()
                        )
                    )
                }
            ),
        )

    async def async_step_group_remote_targets(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Replace the complete shutter membership of one group identity."""
        alias = next(
            (
                item
                for item in self._remote_aliases()
                if item[CONF_REMOTE] == self._alias_remote
            ),
            None,
        )
        if alias is None:
            return self.async_abort(reason="no_group_remotes")
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                shutter_ids = self._selected_shutter_ids(
                    user_input.get(CONF_SHUTTER_IDS)
                )
                await self._runtime.async_call(
                    "remote_alias",
                    {
                        "action": "set",
                        "remote": self._alias_remote,
                        "slots": self._slots_csv(shutter_ids),
                    },
                    "alias_saved",
                )
                return self._save_remote_alias(
                    self._alias_remote, shutter_ids
                )
            except FlowError as err:
                errors["base"] = err.key
            except ManagerError:
                errors["base"] = "manager_unavailable"

        current = [
            shutter_id
            for shutter_id in alias[CONF_SHUTTER_IDS]
            if any(
                option["value"] == shutter_id
                for option in self._shutter_alias_options()
            )
        ]
        return self.async_show_form(
            step_id="group_remote_targets",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SHUTTER_IDS, default=current
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=self._shutter_alias_options(),
                            multiple=True,
                        )
                    )
                }
            ),
            errors=errors,
            description_placeholders={"remote": self._alias_remote},
        )

    async def async_step_remove_group_remote(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Remove one receive-only group mapping without transmitting RF."""
        aliases = self._remote_aliases()
        if not aliases:
            return self.async_abort(reason="no_group_remotes")
        errors: dict[str, str] = {}
        if user_input is not None:
            remote = _normalize_remote(user_input[CONF_GROUP_REMOTE])
            if not user_input.get(CONF_CONFIRM_REMOVE):
                errors["base"] = "confirm_group_remote_removal"
            else:
                try:
                    await self._runtime.async_call(
                        "remote_alias",
                        {"action": "remove", "remote": remote, "slots": ""},
                        "alias_removed",
                    )
                    remaining = [
                        alias
                        for alias in aliases
                        if alias[CONF_REMOTE] != remote
                    ]
                    return self.async_create_entry(
                        title="",
                        data={
                            **self.config_entry.options,
                            CONF_REMOTE_ALIASES: remaining,
                        },
                    )
                except ManagerError:
                    errors["base"] = "manager_unavailable"

        return self.async_show_form(
            step_id="remove_group_remote",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_GROUP_REMOTE): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=self._group_remote_options()
                        )
                    ),
                    vol.Required(CONF_CONFIRM_REMOVE, default=False): bool,
                }
            ),
            errors=errors,
        )

    async def async_step_finish_setup(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            return await self._async_finalize()
        return self.async_show_form(
            step_id="finish_setup",
            data_schema=vol.Schema(
                {vol.Required(CONF_RETRY_SETUP, default=True): bool}
            ),
        )

    async def _async_finalize(self) -> config_entries.ConfigFlowResult:
        try:
            ensure_shutter_id(self._draft)
            await self._async_calibrate()
            await self._async_configure_venetian()
            await self._runtime.async_export_backup(self._slot)
            self._prepare_transport_entity()
        except ManagerError:
            return self.async_show_form(
                step_id="finish_setup",
                data_schema=vol.Schema(
                    {vol.Required(CONF_RETRY_SETUP, default=True): bool}
                ),
                errors={"base": "manager_unavailable"},
            )
        return self._save_active()

    async def _async_calibrate(self) -> None:
        await self._runtime.async_call(
            "calibrate",
            {
                "slot": self._slot,
                "open_seconds": self._draft[CONF_OPEN_SECONDS],
                "close_seconds": self._draft[CONF_CLOSE_SECONDS],
                "my_percent": self._draft[CONF_MY_PERCENT],
            },
            "calibrated",
        )

    async def _async_configure_venetian(self) -> None:
        is_venetian = (
            self._draft.get(CONF_COVER_TYPE, COVER_TYPE_SHUTTER)
            == COVER_TYPE_VENETIAN
        )
        await self._runtime.async_call(
            "venetian",
            {
                "slot": self._slot,
                "enabled": is_venetian,
                "tilt_steps": int(self._draft.get(CONF_TILT_STEPS, 12)),
                "tilt_inverted": bool(
                    self._draft.get(CONF_TILT_INVERTED, True)
                ),
                "my_tilt_step": int(
                    self._draft.get(CONF_MY_TILT_STEP, 6)
                ),
            },
            "venetian_configured",
        )

    def _prepare_transport_entity(self) -> None:
        """Hide the ESPHome cover used as the user-facing cover's transport."""
        registry = er.async_get(self.hass)
        configure_transport_cover(
            registry,
            esphome_entry_id=self._runtime.esphome_entry_id,
            device_name=self.config_entry.data[CONF_DEVICE_NAME],
            slot=self._slot,
            active=True,
        )

        if self._replacing_slot is not None and self._replacing_slot != self._slot:
            configure_transport_cover(
                registry,
                esphome_entry_id=self._runtime.esphome_entry_id,
                device_name=self.config_entry.data[CONF_DEVICE_NAME],
                slot=self._replacing_slot,
                active=False,
            )

    def _save_uncertain(self) -> config_entries.ConfigFlowResult:
        ensure_shutter_id(self._draft)
        shutters = [
            shutter for shutter in self._shutters() if shutter[CONF_SLOT] != self._slot
        ]
        shutters.append(dict(self._draft))
        return self.async_create_entry(
            title="", data={**self.config_entry.options, CONF_SHUTTERS: shutters}
        )

    def _save_active(self) -> config_entries.ConfigFlowResult:
        ensure_shutter_id(self._draft)
        self._draft[CONF_STATE] = STATE_ACTIVE
        replaced_slots = {self._slot}
        if self._replacing_slot is not None:
            replaced_slots.add(self._replacing_slot)
        shutters = [
            shutter
            for shutter in self._shutters()
            if shutter[CONF_SLOT] not in replaced_slots
        ]
        shutters.append(dict(self._draft))
        shutters.sort(key=lambda item: item[CONF_SLOT])
        self._runtime.async_clear_pending(self._slot)
        return self.async_create_entry(
            title="", data={**self.config_entry.options, CONF_SHUTTERS: shutters}
        )
