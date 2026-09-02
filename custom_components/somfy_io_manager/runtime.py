"""Runtime bridge and encrypted recovery storage."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterable
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.storage import Store

from .const import (
    CONF_BACKUP_ENTITY_ID,
    CONF_DEVICE_NAME,
    CONF_ESPHOME_ENTRY_ID,
    CONF_REMOTE,
    CONF_REMOTE_ALIASES,
    CONF_SHUTTER_ID,
    CONF_SHUTTER_IDS,
    CONF_SHUTTERS,
    CONF_SLOT,
    CONF_STATE,
    CONF_STATUS_ENTITY_ID,
    DOMAIN,
    MANAGER_API_VERSION,
    STATE_ACTIVE,
    STATE_UNCERTAIN,
    STORAGE_VERSION,
)
from .multibridge_logic import status_response_is_new

_LOGGER = logging.getLogger(__name__)


class ManagerError(Exception):
    """Base error raised by the commissioning bridge."""


class ManagerUnavailable(ManagerError):
    """The ESPHome manager or one of its entities is unavailable."""


class ManagerRejected(ManagerError):
    """The ESPHome manager rejected an operation."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def parse_status(value: str | None) -> dict[str, Any] | None:
    """Parse and minimally validate manager status JSON."""
    if not value or value in {"unknown", "unavailable"}:
        return None
    try:
        status = json.loads(value)
    except (TypeError, ValueError):
        return None
    if (
        not isinstance(status, dict)
        or status.get("v") != MANAGER_API_VERSION
        or not isinstance(status.get("action"), str)
    ):
        return None
    return status


class SomfyIOManagerRuntime:
    """Coordinate HA service calls and persist encrypted backups."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, coordinator: Any
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.esphome_entry_id = entry.data[CONF_ESPHOME_ENTRY_ID]
        self.device_name = entry.data[CONF_DEVICE_NAME]
        self.status_entity_id = entry.data[CONF_STATUS_ENTITY_ID]
        self.backup_entity_id = entry.data[CONF_BACKUP_ENTITY_ID]
        self.coordinator = coordinator
        self.service_prefix = self.device_name.replace("-", "_")
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )
        self._backups: dict[str, str] = {}
        self._pending: dict[str, dict[str, Any]] = {}
        self._pending_transfer: dict[str, Any] | None = None
        self._synced_aliases: set[str] = set()
        self._multi_bridge_caps = 0
        self._remove_listener = None
        self._service_lock = asyncio.Lock()

    async def async_setup(self) -> None:
        """Load recovery data and start following backup refreshes."""
        stored = await self._store.async_load()
        if isinstance(stored, dict) and isinstance(stored.get("backups"), dict):
            self._backups = {
                str(slot): value
                for slot, value in stored["backups"].items()
                if isinstance(value, str)
            }
        if isinstance(stored, dict) and isinstance(stored.get("pending"), dict):
            self._pending = {
                str(slot): value
                for slot, value in stored["pending"].items()
                if isinstance(value, dict)
            }
        if isinstance(stored, dict) and isinstance(
            stored.get("pending_transfer"), dict
        ):
            self._pending_transfer = dict(stored["pending_transfer"])
        if isinstance(stored, dict) and isinstance(stored.get("synced_aliases"), list):
            self._synced_aliases = {
                str(value)
                for value in stored["synced_aliases"]
                if isinstance(value, str)
            }
        # An uncertain post-RF attempt is also kept in config-entry options so
        # it remains resumable even if HA restarted before the delayed Store
        # write completed.
        for shutter in self.entry.options.get(CONF_SHUTTERS, []):
            if shutter.get(CONF_STATE) == STATE_UNCERTAIN:
                self._pending.setdefault(str(shutter[CONF_SLOT]), dict(shutter))
        self._remove_listener = async_track_state_change_event(
            self.hass, self.status_entity_id, self._async_status_changed
        )
        current = self.hass.states.get(self.status_entity_id)
        self._remember_capabilities(
            parse_status(current.state if current is not None else None)
        )

    async def async_unload(self) -> None:
        """Stop listeners."""
        if self._remove_listener is not None:
            self._remove_listener()
            self._remove_listener = None

    @callback
    def _async_status_changed(self, event: Event) -> None:
        new_state = event.data.get("new_state")
        status = parse_status(new_state.state if new_state else None)
        self._remember_capabilities(status)
        if status is not None and status.get("action") == "remote_observation":
            self.coordinator.receive_observation(self, status)
        if status is None or status.get("action") not in {
            "backup_updated",
            "backup_exported",
            "pair_sent",
            "active",
            "calibrated",
            "venetian_configured",
            "restored",
            "moved",
            "transfer_prepared",
            "transfer_activated",
        }:
            return
        slot = status.get("slot")
        backup_state = self.hass.states.get(self.backup_entity_id)
        if not isinstance(slot, int) or slot < 0 or backup_state is None:
            return
        backup = backup_state.state
        if not backup or backup in {"unknown", "unavailable"}:
            return
        self.async_remember_backup(slot, backup)

    @callback
    def async_remember_backup(self, slot: int, encrypted_backup: str) -> None:
        """Persist an opaque encrypted controller snapshot."""
        self._backups[str(slot)] = encrypted_backup
        self._schedule_save()

    def backup_for_slot(self, slot: int) -> str | None:
        """Return the latest encrypted backup known for a slot."""
        return self._backups.get(str(slot))

    @callback
    def async_move_backup(self, source_slot: int, target_slot: int) -> None:
        """Move the cached snapshot alongside a firmware slot migration."""
        backup = self._backups.pop(str(source_slot), None)
        if backup is not None:
            self._backups[str(target_slot)] = backup
        self._schedule_save()

    @callback
    def async_swap_backups(self, first_slot: int, second_slot: int) -> None:
        """Exchange cached snapshots alongside a firmware slot swap."""
        first = self._backups.pop(str(first_slot), None)
        second = self._backups.pop(str(second_slot), None)
        if first is not None:
            self._backups[str(second_slot)] = first
        if second is not None:
            self._backups[str(first_slot)] = second
        self._schedule_save()

    @property
    def pending(self) -> dict[int, dict[str, Any]]:
        """Return unfinished commissioning attempts."""
        return {int(slot): value.copy() for slot, value in self._pending.items()}

    @callback
    def async_remember_pending(self, slot: int, draft: dict[str, Any]) -> None:
        """Persist wizard context before or after pairing RF."""
        self._pending[str(slot)] = draft.copy()
        self._schedule_save()

    @callback
    def async_clear_pending(self, slot: int) -> None:
        """Forget a finished or safely discarded commissioning attempt."""
        self._pending.pop(str(slot), None)
        self._schedule_save()

    @callback
    def _schedule_save(self) -> None:
        self._store.async_delay_save(
            self._serialized_store,
            delay=1.0,
        )

    @callback
    def _serialized_store(self) -> dict[str, Any]:
        return {
            "backups": self._backups.copy(),
            "pending": self._pending.copy(),
            "pending_transfer": (
                self._pending_transfer.copy() if self._pending_transfer else None
            ),
            "synced_aliases": sorted(self._synced_aliases),
        }

    @property
    def pending_transfer(self) -> dict[str, Any] | None:
        """Return the unfinished cross-bridge ownership transaction."""
        return self._pending_transfer.copy() if self._pending_transfer else None

    async def async_set_pending_transfer(self, transfer: dict[str, Any] | None) -> None:
        """Durably checkpoint a cross-bridge transfer before its next step."""
        self._pending_transfer = transfer.copy() if transfer else None
        await self._store.async_save(self._serialized_store())

    def service_name(self, suffix: str) -> str:
        """Build ESPHome's generated custom-action name."""
        return f"{self.service_prefix}_somfy_{suffix}"

    def has_service(self, suffix: str) -> bool:
        """Return whether this online ESPHome node exposes an action."""
        return self.hass.services.has_service("esphome", self.service_name(suffix))

    @property
    def available(self) -> bool:
        """Return whether the bridge status transport is currently usable."""
        state = self.hass.states.get(self.status_entity_id)
        return state is not None and parse_status(state.state) is not None

    @property
    def multi_bridge_capable(self) -> bool:
        """Return whether this node advertised the additive multi-bridge API."""
        return self._multi_bridge_caps == 0x0F or all(
            self.has_service(suffix)
            for suffix in ("redundant_control", "relay", "observe", "transfer")
        )

    def has_multi_bridge_capability(self, capability: int) -> bool:
        """Check one advertised capability bit, with service discovery fallback."""
        if self._multi_bridge_caps & capability == capability:
            return True
        inferred = {
            0x02: ("observe",),
            0x04: ("redundant_control", "relay"),
            0x08: ("transfer",),
        }.get(capability)
        return inferred is not None and all(self.has_service(item) for item in inferred)

    @callback
    def _remember_capabilities(self, status: dict[str, Any] | None) -> None:
        if status is None or status.get("multi_bridge") is not True:
            return
        caps = status.get("mb_caps")
        if isinstance(caps, int) and not isinstance(caps, bool):
            self._multi_bridge_caps = max(0, caps)

    async def async_call(
        self,
        suffix: str,
        data: dict[str, Any],
        expected_actions: str | Iterable[str],
        *,
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        """Call a manager action and wait for its matching status event."""
        async with self._service_lock:
            return await self._async_call_unlocked(
                suffix, data, expected_actions, timeout=timeout
            )

    async def _async_call_unlocked(
        self,
        suffix: str,
        data: dict[str, Any],
        expected_actions: str | Iterable[str],
        *,
        timeout: float,
    ) -> dict[str, Any]:
        """Execute one already-serialized manager action."""
        service = self.service_name(suffix)
        if not self.hass.services.has_service("esphome", service):
            raise ManagerUnavailable(f"missing service esphome.{service}")

        expected = (
            {expected_actions}
            if isinstance(expected_actions, str)
            else set(expected_actions)
        )
        before_state = self.hass.states.get(self.status_entity_id)
        before_raw = before_state.state if before_state else None
        before = parse_status(before_raw)
        before_event = before.get("event") if before else None
        # A move can report an error against either endpoint and reports
        # success from the destination. Other actions, including swaps, reply
        # from their requested source slot.
        requested_slots = {
            value
            for key in (("slot", "target_slot") if suffix == "move" else ("slot",))
            if isinstance((value := data.get(key)), int) and value >= 0
        }
        future: asyncio.Future[dict[str, Any]] = self.hass.loop.create_future()

        @callback
        def status_changed(event: Event) -> None:
            new_state = event.data.get("new_state")
            current_raw = new_state.state if new_state else None
            status = parse_status(current_raw)
            if status is None or not status_response_is_new(
                before_raw, before_event, current_raw, status
            ):
                return
            if requested_slots and status.get("slot") not in requested_slots:
                return
            if (
                status.get("action") in expected or status.get("action") == "error"
            ) and not future.done():
                future.set_result(status)

        remove = async_track_state_change_event(
            self.hass, self.status_entity_id, status_changed
        )
        try:
            await self.hass.services.async_call("esphome", service, data, blocking=True)
            current_state = self.hass.states.get(self.status_entity_id)
            current_raw = current_state.state if current_state else None
            current = parse_status(current_raw)
            if (
                current is not None
                and status_response_is_new(
                    before_raw, before_event, current_raw, current
                )
                and (not requested_slots or current.get("slot") in requested_slots)
                and (
                    current.get("action") in expected
                    or current.get("action") == "error"
                )
                and not future.done()
            ):
                future.set_result(current)
            status = await asyncio.wait_for(future, timeout)
        except TimeoutError as err:
            raise ManagerUnavailable("manager status response timed out") from err
        finally:
            remove()

        if status.get("action") == "error":
            raise ManagerRejected(str(status.get("detail") or "unknown_error"))
        return status

    async def async_export_backup(self, slot: int) -> str:
        """Request, read, and persist an encrypted controller backup."""
        await self.async_call(
            "commission",
            {"action": "export", "slot": slot},
            "backup_exported",
        )
        backup = self.backup_for_slot(slot)
        if backup is None:
            raise ManagerUnavailable("encrypted backup entity is unavailable")
        return backup

    async def async_sync_remote_aliases(self) -> None:
        """Restore receive-only group mappings from HA's durable metadata."""
        shutters = {
            str(shutter.get(CONF_SHUTTER_ID)): int(shutter[CONF_SLOT])
            for shutter in self.entry.options.get(CONF_SHUTTERS, [])
            if shutter.get(CONF_STATE) == STATE_ACTIVE and shutter.get(CONF_SHUTTER_ID)
        }
        for alias in self.entry.options.get(CONF_REMOTE_ALIASES, []):
            remote = alias.get(CONF_REMOTE)
            selected = alias.get(CONF_SHUTTER_IDS, [])
            slots = sorted(
                {
                    shutters[shutter_id]
                    for shutter_id in selected
                    if shutter_id in shutters
                }
            )
            if not isinstance(remote, str) or not slots:
                continue
            await self.async_call(
                "remote_alias",
                {
                    "action": "set",
                    "remote": remote,
                    "slots": ",".join(str(slot) for slot in slots),
                },
                "alias_saved",
            )

    async def async_sync_remote_alias_map(self, aliases: dict[str, list[int]]) -> None:
        """Synchronize this bridge's local projection of global group aliases."""
        desired = set(aliases)
        for remote in sorted(self._synced_aliases - desired):
            try:
                await self.async_call(
                    "remote_alias",
                    {"action": "remove", "remote": remote, "slots": ""},
                    "alias_removed",
                )
            except ManagerRejected as err:
                if err.detail != "remote_alias_not_found":
                    raise
        for remote, slots in sorted(aliases.items()):
            await self.async_call(
                "remote_alias",
                {
                    "action": "set",
                    "remote": remote,
                    "slots": ",".join(str(slot) for slot in sorted(set(slots))),
                },
                "alias_saved",
            )
        self._synced_aliases = desired
        self._schedule_save()
