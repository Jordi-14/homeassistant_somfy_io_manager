"""Installation-wide coordination for multiple Somfy IO radio bridges."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

from homeassistant.core import HomeAssistant, callback

from .const import (
    CONF_PRIMARY_ENTRY_ID,
    CONF_REMOTE,
    CONF_REMOTE_ALIASES,
    CONF_SECONDARY_ENTRY_ID,
    CONF_SHUTTER_ID,
    CONF_SHUTTER_IDS,
    CONF_SHUTTERS,
    CONF_SLOT,
    CONF_STATE,
    OBSERVATION_COMMANDS,
    STATE_ACTIVE,
)
from .multibridge_logic import (
    cache_after_flush,
    merge_completion,
    normalize_remote,
    observation_key,
    observation_slots,
    prefer_candidate,
    relay_allowed,
    resolve_remote_target_ids,
    terminal_supersedes_prefix,
)
from .runtime import ManagerError, ManagerRejected, ManagerUnavailable

_LOGGER = logging.getLogger(__name__)

OBSERVATION_COLLECTION_SECONDS = 0.25
MY_RADIO_GUARD_SECONDS = 1.35
TRANSFER_STOP_SETTLE_SECONDS = 0.25


@dataclass(slots=True)
class PendingObservation:
    """Copies of one physical RF gesture heard by one or more bridges."""

    remote: str
    command: str
    sequence: int | None
    steps: int
    status: dict[str, Any]
    sources: set[str] = field(default_factory=set)
    complete_sources: set[str] = field(default_factory=set)
    source_slots: dict[str, set[int]] = field(default_factory=dict)
    strongest_entry_id: str = ""
    strongest_rssi: float | None = None
    complete: bool = True


class SomfyIOMultiBridgeCoordinator:
    """Route RF observations and safe transmissions across config entries."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.runtimes: dict[str, Any] = {}
        self._pending_observations: dict[tuple[Any, ...], PendingObservation] = {}
        self._observation_subscribers: dict[
            str, set[Callable[[dict[str, Any]], None]]
        ] = defaultdict(set)
        self._rf_lock = asyncio.Lock()
        self._transfer_lock = asyncio.Lock()
        self._transfer_start_lock = asyncio.Lock()
        self._completed_observations: dict[tuple[Any, ...], float] = {}
        self._recent_terminals: dict[tuple[str, int], float] = {}
        self._observations_received = 0
        self._observations_delivered = 0
        self._duplicates_collapsed = 0
        self._estimator_forwards = 0
        self._relay_attempts = 0
        self._relay_successes = 0
        self._relay_failures = 0
        self._last_strongest_entry_id: str | None = None
        self._last_strongest_rssi: float | None = None

    @callback
    def register(self, runtime: Any) -> None:
        """Register one loaded bridge runtime."""
        self.runtimes[runtime.entry.entry_id] = runtime

    @callback
    def unregister(self, entry_id: str) -> None:
        """Remove one unloaded bridge runtime."""
        self.runtimes.pop(entry_id, None)

    def runtime_for(self, entry_id: str | None) -> Any | None:
        """Return a loaded bridge by config-entry ID."""
        return self.runtimes.get(entry_id or "")

    def bridge_options(self, *, exclude: str | None = None) -> list[dict[str, str]]:
        """Return loaded bridges as selector options."""
        return [
            {
                "value": entry_id,
                "label": runtime.entry.title or runtime.device_name,
            }
            for entry_id, runtime in sorted(
                self.runtimes.items(),
                key=lambda item: (item[1].entry.title or item[1].device_name).lower(),
            )
            if entry_id != exclude
        ]

    def active_shutters(self) -> list[tuple[Any, dict[str, Any]]]:
        """Return every active shutter with its authoritative runtime."""
        result: list[tuple[Any, dict[str, Any]]] = []
        seen: set[str] = set()
        for entry_id, runtime in self.runtimes.items():
            for value in runtime.entry.options.get(CONF_SHUTTERS, []):
                shutter = dict(value)
                shutter_id = shutter.get(CONF_SHUTTER_ID)
                if (
                    shutter.get(CONF_STATE) != STATE_ACTIVE
                    or not isinstance(shutter_id, str)
                    or not shutter_id
                    or shutter_id in seen
                ):
                    continue
                owner = shutter.get(CONF_PRIMARY_ENTRY_ID, entry_id)
                if owner != entry_id:
                    continue
                seen.add(shutter_id)
                result.append((runtime, shutter))
        return result

    def find_shutter(self, shutter_id: str) -> tuple[Any, dict[str, Any]] | None:
        """Resolve one permanent shutter identity to its only active owner."""
        owners = self._owners_for(shutter_id)
        return owners[0] if len(owners) == 1 else None

    def _owners_for(self, shutter_id: str) -> list[tuple[Any, dict[str, Any]]]:
        """Return every metadata record claiming active ownership."""
        owners = []
        for entry_id, runtime in self.runtimes.items():
            for value in runtime.entry.options.get(CONF_SHUTTERS, []):
                shutter = dict(value)
                if (
                    shutter.get(CONF_STATE) == STATE_ACTIVE
                    and shutter.get(CONF_SHUTTER_ID) == shutter_id
                    and shutter.get(CONF_PRIMARY_ENTRY_ID, entry_id) == entry_id
                ):
                    owners.append((runtime, shutter))
        return owners

    async def async_control(
        self,
        owner: Any,
        shutter: dict[str, Any],
        command: str,
        position: float = 0.0,
    ) -> dict[str, Any]:
        """Send through the owner and optionally relay its exact RF frame."""
        async with self._rf_lock:
            return await self._async_control_locked(owner, shutter, command, position)

    async def _async_control_locked(
        self,
        owner: Any,
        shutter: dict[str, Any],
        command: str,
        position: float = 0.0,
    ) -> dict[str, Any]:
        """Execute one command while the installation RF lock is held."""
        slot = int(shutter[CONF_SLOT])
        shutter_id = str(shutter.get(CONF_SHUTTER_ID) or "")
        resolved = self.find_shutter(shutter_id)
        if resolved is None or resolved[0] is not owner:
            raise ManagerRejected("duplicate_or_missing_owner")
        secondary_id = shutter.get(CONF_SECONDARY_ENTRY_ID)
        secondary = self.runtime_for(
            secondary_id if isinstance(secondary_id, str) else None
        )
        if (
            not relay_allowed(command)
            or secondary is None
            or secondary is owner
            or not secondary.available
            or not owner.has_service("redundant_control")
            or not secondary.has_service("relay")
        ):
            return await self._async_primary_control(owner, slot, command, position)

        self._relay_attempts += 1
        try:
            armed = await secondary.async_call(
                "relay",
                {"action": "arm", "payload": ""},
                "relay_armed",
                timeout=5.0,
            )
            token = str(armed.get("relay_token") or "")
            if not token:
                raise ManagerUnavailable("secondary returned no relay token")
        except ManagerError:
            self._relay_failures += 1
            _LOGGER.warning(
                "Secondary Somfy bridge unavailable; sending %s only from %s",
                command,
                owner.device_name,
            )
            return await self._async_primary_control(owner, slot, command, position)

        try:
            offered = await owner.async_call(
                "redundant_control",
                {"slot": slot, "command": command, "relay_token": token},
                "relay_offer",
            )
        except ManagerError:
            await self._async_cancel_relay(secondary, token)
            raise

        envelope = str(offered.get("relay_envelope") or "")
        if not envelope:
            self._relay_failures += 1
            await self._async_cancel_relay(secondary, token)
            _LOGGER.warning(
                "Primary Somfy bridge sent %s but supplied no relay envelope",
                command,
            )
            return offered
        try:
            await secondary.async_call(
                "relay",
                {"action": "send", "payload": envelope},
                "relay_sent",
                timeout=5.0,
            )
        except ManagerError:
            # The primary transmission has already happened. Do not report
            # the user command as failed or generate a fresh replacement.
            self._relay_failures += 1
            _LOGGER.warning(
                "Primary Somfy command succeeded, but its exact-frame relay failed"
            )
        else:
            self._relay_successes += 1
        return offered

    async def _async_primary_control(
        self, owner: Any, slot: int, command: str, position: float
    ) -> dict[str, Any]:
        """Send primary-only traffic while retaining composite MY isolation."""
        complete_ack = command in {"my", "tilt_position"} and owner.multi_bridge_capable
        status = await owner.async_call(
            "control",
            {
                "slot": slot,
                "command": command,
                "position_percent": position,
            },
            "command_complete" if complete_ack else "command_sent",
            timeout=30.0 if command in {"my", "tilt_position"} else 10.0,
        )
        if command == "my" and not complete_ack:
            # Current firmware acknowledges the pre-stop/acceptance before the
            # full native MY gesture ends. Keep other bridges quiet through the
            # remaining fixed protocol envelope.
            await asyncio.sleep(MY_RADIO_GUARD_SECONDS)
        return status

    async def _async_cancel_relay(self, secondary: Any, token: str) -> None:
        with suppress(ManagerError):
            await secondary.async_call(
                "relay",
                {"action": "cancel", "payload": token},
                "relay_cancelled",
                timeout=3.0,
            )

    @callback
    def receive_observation(self, runtime: Any, status: dict[str, Any]) -> None:
        """Collect one normalized observation without acting immediately."""
        if status.get("observation_v") != 1:
            return
        remote = normalize_remote(status.get("remote"))
        command = str(status.get("command") or "")
        if not remote or command not in OBSERVATION_COMMANDS:
            return
        has_sequence = status.get("has_sequence") is True
        raw_sequence = status.get("sequence")
        sequence = (
            int(raw_sequence) & 0xFFFF
            if has_sequence
            and isinstance(raw_sequence, int)
            and not isinstance(raw_sequence, bool)
            else None
        )
        raw_steps = status.get("steps", 1)
        steps = (
            max(1, min(254, int(raw_steps)))
            if isinstance(raw_steps, int) and not isinstance(raw_steps, bool)
            else 1
        )
        complete = status.get("complete") is not False
        # A no-sequence identity lives only for this collection bucket. A
        # later identical physical press must remain a new observation.
        key = observation_key(remote, command, sequence, steps)
        now = self.hass.loop.time()
        for completed_key, expires in tuple(self._completed_observations.items()):
            if expires <= now:
                self._completed_observations.pop(completed_key, None)
        for terminal_key, expires in tuple(self._recent_terminals.items()):
            if expires <= now:
                self._recent_terminals.pop(terminal_key, None)
        if (
            not complete
            and sequence is not None
            and (remote, (sequence + 1) & 0xFFFF) in self._recent_terminals
        ):
            self._duplicates_collapsed += 1
            return
        if (
            complete
            and sequence is not None
            and command
            in {
                "stop_my",
                "tilt_clockwise",
                "tilt_counterclockwise",
            }
        ):
            self._recent_terminals[(remote, sequence)] = now + 2.0
            for pending_key, candidate in tuple(self._pending_observations.items()):
                if (
                    candidate.remote == remote
                    and not candidate.complete
                    and terminal_supersedes_prefix(
                        candidate.sequence, sequence, command
                    )
                ):
                    self._pending_observations.pop(pending_key, None)
                    if cache_after_flush(pending_key):
                        self._completed_observations[pending_key] = now + 2.0
                    self._duplicates_collapsed += 1
        if sequence is not None and key in self._completed_observations:
            self._duplicates_collapsed += 1
            return
        rssi = _number_or_none(status.get("rssi"))
        pending = self._pending_observations.get(key)
        self._observations_received += 1
        if pending is None:
            pending = PendingObservation(
                remote=remote,
                command=command,
                sequence=sequence,
                steps=steps,
                status=dict(status),
                complete=complete,
            )
            self._pending_observations[key] = pending
            self.hass.loop.call_later(
                OBSERVATION_COLLECTION_SECONDS,
                self._schedule_flush,
                key,
            )
        else:
            self._duplicates_collapsed += 1
            pending.steps = max(pending.steps, steps)
        entry_id = runtime.entry.entry_id
        pending.sources.add(entry_id)
        pending.source_slots.setdefault(entry_id, set()).update(
            observation_slots(status)
        )
        if complete:
            pending.complete = merge_completion(pending.complete, complete)
            pending.complete_sources.add(entry_id)
        if prefer_candidate(
            pending.strongest_entry_id,
            pending.strongest_rssi,
            entry_id,
            rssi,
        ):
            pending.strongest_entry_id = entry_id
            pending.strongest_rssi = rssi
            pending.status = dict(status)

    @callback
    def _schedule_flush(self, key: tuple[Any, ...]) -> None:
        self.hass.async_create_task(
            self._async_flush_observation(key),
            "route deduplicated Somfy IO remote observation",
        )

    async def _async_flush_observation(self, key: tuple[Any, ...]) -> None:
        pending = self._pending_observations.pop(key, None)
        if pending is None:
            return
        if cache_after_flush(key):
            self._completed_observations[key] = self.hass.loop.time() + 2.0
        targets = self._targets_for_remote(pending)
        self._last_strongest_entry_id = pending.strongest_entry_id or None
        self._last_strongest_rssi = pending.strongest_rssi
        for owner, shutter in targets:
            shutter_id = str(shutter[CONF_SHUTTER_ID])
            delivered = {
                **pending.status,
                "remote": pending.remote,
                "command": pending.command,
                "sequence": pending.sequence,
                "steps": pending.steps,
                "source_entry_id": pending.strongest_entry_id,
                "source_bridge": self._bridge_title(pending.strongest_entry_id),
                "heard_by": len(pending.sources),
                "rssi": pending.strongest_rssi,
                "complete": pending.complete,
            }
            for subscriber in tuple(self._observation_subscribers.get(shutter_id, ())):
                subscriber(delivered)
            self._observations_delivered += 1
            if owner.entry.entry_id in pending.complete_sources:
                continue
            try:
                await owner.async_call(
                    "observe",
                    {
                        "slot": int(shutter[CONF_SLOT]),
                        "command": pending.command,
                        "steps": pending.steps,
                    },
                    "observation_applied",
                    timeout=5.0,
                )
            except ManagerError:
                _LOGGER.warning(
                    "Could not forward a receive-only Somfy observation to %s",
                    owner.device_name,
                )
            else:
                self._estimator_forwards += 1

    def _targets_for_remote(
        self, pending: PendingObservation
    ) -> list[tuple[Any, dict[str, Any]]]:
        targets: dict[str, tuple[Any, dict[str, Any]]] = {}
        all_shutters = self.active_shutters()
        by_id = {
            str(shutter[CONF_SHUTTER_ID]): (runtime, shutter)
            for runtime, shutter in all_shutters
        }
        all_aliases = []
        for runtime in self.runtimes.values():
            all_aliases.extend(runtime.entry.options.get(CONF_REMOTE_ALIASES, []))
        for shutter_id in resolve_remote_target_ids(
            (shutter for _runtime, shutter in all_shutters),
            all_aliases,
            pending.remote,
        ):
            target = by_id.get(shutter_id)
            if target is not None:
                targets[shutter_id] = target

        # Preserve compatibility for records imported before their physical
        # remote identity was stored in HA options.
        for source_id, slot_set in pending.source_slots.items():
            source = self.runtime_for(source_id)
            if source is None:
                continue
            for runtime, shutter in all_shutters:
                if runtime is source and int(shutter[CONF_SLOT]) in slot_set:
                    targets[str(shutter[CONF_SHUTTER_ID])] = (runtime, shutter)
        return list(targets.values())

    @callback
    def subscribe(
        self, shutter_id: str, subscriber: Callable[[dict[str, Any]], None]
    ) -> Callable[[], None]:
        """Subscribe an entity to installation-wide physical remote events."""
        self._observation_subscribers[shutter_id].add(subscriber)

        @callback
        def remove() -> None:
            subscribers = self._observation_subscribers.get(shutter_id)
            if subscribers is None:
                return
            subscribers.discard(subscriber)
            if not subscribers:
                self._observation_subscribers.pop(shutter_id, None)

        return remove

    async def async_sync_remote_aliases(self) -> None:
        """Project global group mappings into every owning bridge's slots."""
        aliases: dict[str, set[str]] = defaultdict(set)
        for runtime in self.runtimes.values():
            for alias in runtime.entry.options.get(CONF_REMOTE_ALIASES, []):
                remote = normalize_remote(alias.get(CONF_REMOTE))
                if not remote:
                    continue
                aliases[remote].update(
                    str(item) for item in alias.get(CONF_SHUTTER_IDS, [])
                )
        for runtime in tuple(self.runtimes.values()):
            local = {
                str(shutter[CONF_SHUTTER_ID]): int(shutter[CONF_SLOT])
                for owner, shutter in self.active_shutters()
                if owner is runtime
            }
            mapping = {
                remote: sorted(local[item] for item in members if item in local)
                for remote, members in aliases.items()
            }
            mapping = {remote: slots for remote, slots in mapping.items() if slots}
            try:
                await runtime.async_sync_remote_alias_map(mapping)
            except ManagerError:
                _LOGGER.warning(
                    "Could not synchronize global remote aliases to %s",
                    runtime.device_name,
                )

    async def async_remove_remote_alias(self, remote: str) -> None:
        """Remove one passive alias projection wherever it currently exists."""
        for runtime in tuple(self.runtimes.values()):
            if not runtime.available:
                continue
            try:
                await runtime.async_call(
                    "remote_alias",
                    {"action": "remove", "remote": remote, "slots": ""},
                    "alias_removed",
                )
            except ManagerRejected as err:
                if err.detail != "remote_alias_not_found":
                    raise
            except ManagerUnavailable:
                # HA metadata remains authoritative; the normal reload sync
                # removes the stale projection when this bridge returns.
                _LOGGER.warning(
                    "Could not remove a Somfy remote alias from offline bridge %s",
                    runtime.device_name,
                )

    async def async_start_transfer(
        self,
        source: Any,
        shutter: dict[str, Any],
        destination: Any,
        destination_slot: int,
    ) -> dict[str, Any]:
        """Serialize new transfers across the whole installation."""
        async with self._transfer_start_lock:
            return await self._async_start_transfer(
                source, shutter, destination, destination_slot
            )

    async def _async_start_transfer(
        self,
        source: Any,
        shutter: dict[str, Any],
        destination: Any,
        destination_slot: int,
    ) -> dict[str, Any]:
        """Archive one owner and begin a crash-recoverable identity transfer."""
        if source is destination:
            raise ManagerRejected("same_bridge")
        shutter_id = str(shutter[CONF_SHUTTER_ID])
        resolved = self.find_shutter(shutter_id)
        if resolved is None or resolved[0] is not source:
            raise ManagerRejected("duplicate_or_missing_owner")
        await self._ensure_destination_empty(destination, destination_slot)
        # Hard-quiescing firmware intentionally emits no RF. Stop the motor
        # first so ownership cannot move while the physical shutter continues
        # travelling, then archive the post-STOP rolling-code snapshot.
        async with self._rf_lock:
            await self._async_control_locked(source, shutter, "stop")
            await asyncio.sleep(TRANSFER_STOP_SETTLE_SECONDS)
            transfer = {
                "phase": "preparing",
                "source_entry_id": source.entry.entry_id,
                "source_slot": int(shutter[CONF_SLOT]),
                "destination_entry_id": destination.entry.entry_id,
                "destination_slot": destination_slot,
                "shutter": dict(shutter),
                "token": "",
                "encrypted_backup": "",
                "next_action": "prepare",
            }
            # This checkpoint precedes the first firmware mutation. If HA dies
            # after the source archives itself, transfer=query can reconstruct
            # the response without guessing a destination or new identity.
            await source.async_set_pending_transfer(transfer)
            prepared = await source.async_call(
                "transfer",
                {
                    "action": "prepare",
                    "slot": int(shutter[CONF_SLOT]),
                    "transfer_token": "",
                    "encrypted_backup": "",
                },
                "transfer_prepared",
            )
        token = str(prepared.get("transfer_token") or "")
        backup = source.backup_for_slot(int(shutter[CONF_SLOT])) or ""
        if not token or not backup:
            raise ManagerUnavailable("transfer preparation returned no recovery data")
        transfer["phase"] = "prepared"
        transfer["token"] = token
        transfer["encrypted_backup"] = backup
        transfer.pop("next_action", None)
        await source.async_set_pending_transfer(transfer)
        return await self.async_resume_transfer(source, transfer)

    async def async_resume_transfer(
        self, source: Any, transfer: dict[str, Any]
    ) -> dict[str, Any]:
        """Continue an interrupted transfer from its last durable phase."""
        async with self._transfer_lock:
            destination = self.runtime_for(transfer.get("destination_entry_id"))
            if destination is None:
                raise ManagerUnavailable("destination bridge is offline")
            transfer = await self._async_reconcile_transfer(
                source, destination, transfer
            )
            phase = str(transfer.get("phase"))
            source_slot = int(transfer["source_slot"])
            destination_slot = int(transfer["destination_slot"])
            if phase == "preparing":
                transfer["next_action"] = "prepare"
                await source.async_set_pending_transfer(transfer)
                prepared = await source.async_call(
                    "transfer",
                    {
                        "action": "prepare",
                        "slot": source_slot,
                        "transfer_token": "",
                        "encrypted_backup": "",
                    },
                    "transfer_prepared",
                )
                transfer["token"] = str(prepared.get("transfer_token") or "")
                transfer["encrypted_backup"] = source.backup_for_slot(source_slot) or ""
                if not transfer["token"] or not transfer["encrypted_backup"]:
                    raise ManagerUnavailable(
                        "transfer preparation returned no recovery data"
                    )
                transfer["phase"] = phase = "prepared"
                transfer.pop("next_action", None)
                await source.async_set_pending_transfer(transfer)
            token = str(transfer.get("token") or "")
            backup = str(transfer.get("encrypted_backup") or "")
            if phase != "active" and not token:
                raise ManagerUnavailable("transfer token is unavailable")
            if phase == "prepared":
                transfer["next_action"] = "import"
                await source.async_set_pending_transfer(transfer)
                await destination.async_call(
                    "transfer",
                    {
                        "action": "import",
                        "slot": destination_slot,
                        "transfer_token": token,
                        "encrypted_backup": backup,
                    },
                    "transfer_imported",
                )
                transfer["phase"] = phase = "imported"
                transfer.pop("next_action", None)
                await source.async_set_pending_transfer(transfer)
            if phase == "imported":
                transfer["next_action"] = "activate"
                await source.async_set_pending_transfer(transfer)
                await destination.async_call(
                    "transfer",
                    {
                        "action": "activate",
                        "slot": destination_slot,
                        "transfer_token": token,
                        "encrypted_backup": "",
                    },
                    "transfer_activated",
                )
                transfer["phase"] = phase = "activated"
                transfer.pop("next_action", None)
                await source.async_set_pending_transfer(transfer)
            if phase == "activated":
                transfer["next_action"] = "commit"
                await source.async_set_pending_transfer(transfer)
                await source.async_call(
                    "transfer",
                    {
                        "action": "commit",
                        "slot": source_slot,
                        "transfer_token": token,
                        "encrypted_backup": "",
                    },
                    "transfer_committed",
                )
                transfer["phase"] = phase = "committed"
                transfer.pop("next_action", None)
                await source.async_set_pending_transfer(transfer)
            if phase == "committed":
                transfer["next_action"] = "finalize"
                await source.async_set_pending_transfer(transfer)
                await destination.async_call(
                    "transfer",
                    {
                        "action": "finalize",
                        "slot": destination_slot,
                        "transfer_token": token,
                        "encrypted_backup": "",
                    },
                    "transfer_finalized",
                )
                transfer["phase"] = "active"
                transfer.pop("next_action", None)
                await source.async_set_pending_transfer(transfer)
            return dict(transfer)

    async def async_rollback_transfer(
        self, source: Any, transfer: dict[str, Any]
    ) -> None:
        """Roll back a transfer only while the archived source still exists."""
        async with self._transfer_lock:
            destination = self.runtime_for(transfer.get("destination_entry_id"))
            if destination is None:
                raise ManagerUnavailable("destination bridge is offline")
            transfer = await self._async_reconcile_transfer(
                source, destination, transfer
            )
            phase = str(transfer.get("phase"))
            if phase == "preparing":
                await source.async_set_pending_transfer(None)
                return
            if phase not in {"prepared", "imported"}:
                raise ManagerRejected("transfer_already_committed")
            token = str(transfer["token"])
            if phase == "imported":
                await destination.async_call(
                    "transfer",
                    {
                        "action": "abort",
                        "slot": int(transfer["destination_slot"]),
                        "transfer_token": token,
                        "encrypted_backup": "",
                    },
                    "transfer_aborted",
                )
            await source.async_call(
                "transfer",
                {
                    "action": "rollback",
                    "slot": int(transfer["source_slot"]),
                    "transfer_token": token,
                    "encrypted_backup": "",
                },
                "transfer_rolled_back",
            )
            await source.async_set_pending_transfer(None)

    async def async_reconcile_runtime_transfer(self, source: Any) -> None:
        """Reconcile a stored transfer journal with durable firmware journals."""
        transfer = source.pending_transfer
        if transfer is None or not source.has_service("transfer"):
            return
        destination = self.runtime_for(transfer.get("destination_entry_id"))
        if destination is None or not destination.has_service("transfer"):
            return
        async with self._transfer_lock:
            reconciled = await self._async_reconcile_transfer(
                source, destination, transfer
            )
            await source.async_set_pending_transfer(reconciled)

    async def _async_reconcile_transfer(
        self, source: Any, destination: Any, transfer: dict[str, Any]
    ) -> dict[str, Any]:
        """Infer a safe HA phase from both firmware transfer journals."""
        source_slot = int(transfer["source_slot"])
        destination_slot = int(transfer["destination_slot"])
        source_state = await self._async_query_transfer(source, source_slot)
        destination_state = await self._async_query_transfer(
            destination, destination_slot
        )
        tokens = {
            str(value)
            for value in (
                transfer.get("token"),
                source_state.get("transfer_token"),
                destination_state.get("transfer_token"),
            )
            if value
        }
        if len(tokens) > 1:
            raise ManagerRejected("transfer_token_mismatch")
        if tokens:
            transfer["token"] = tokens.pop()

        source_role = source_state.get("role")
        source_phase = source_state.get("phase")
        destination_role = destination_state.get("role")
        destination_phase = destination_state.get("phase")
        if destination_role == "destination" and destination_phase == "active":
            transfer["phase"] = (
                "committed"
                if source_role in {None, "none"}
                and source_phase in {None, "none", "empty"}
                else "activated"
            )
        elif (
            destination_role in {None, "none"}
            and destination_phase == "active"
            and source_role in {None, "none"}
            and source_phase in {None, "none", "empty"}
        ):
            transfer["phase"] = "active"
        elif destination_role == "destination" and destination_phase == "archived":
            transfer["phase"] = "imported"
        elif source_role == "source" and source_phase == "archived":
            transfer["phase"] = "prepared"
        elif transfer.get("phase") not in {"preparing", "active"}:
            raise ManagerRejected("transfer_journal_missing")

        if transfer.get("phase") in {"prepared", "imported"} and not transfer.get(
            "encrypted_backup"
        ):
            # Repeating prepare is convergent and republishes the slot-tagged
            # encrypted backup while keeping the source archived.
            prepared = await source.async_call(
                "transfer",
                {
                    "action": "prepare",
                    "slot": source_slot,
                    "transfer_token": "",
                    "encrypted_backup": "",
                },
                "transfer_prepared",
            )
            transfer["token"] = str(
                prepared.get("transfer_token") or transfer.get("token") or ""
            )
            transfer["encrypted_backup"] = source.backup_for_slot(source_slot) or ""
        transfer.pop("next_action", None)
        return transfer

    async def _async_query_transfer(self, runtime: Any, slot: int) -> dict[str, Any]:
        """Read one firmware journal without changing ownership."""
        return await runtime.async_call(
            "transfer",
            {
                "action": "query",
                "slot": slot,
                "transfer_token": "",
                "encrypted_backup": "",
            },
            "transfer_state",
        )

    async def _ensure_destination_empty(self, destination: Any, slot: int) -> None:
        status = await destination.async_call(
            "commission", {"action": "query", "slot": slot}, "slot"
        )
        if status.get("state") not in {None, "", "empty"}:
            raise ManagerRejected("target_slot_not_empty")

    async def _best_effort_transfer(
        self,
        runtime: Any,
        action: str,
        slot: int,
        token: str,
        backup: str,
        expected: str,
    ) -> None:
        try:
            await runtime.async_call(
                "transfer",
                {
                    "action": action,
                    "slot": slot,
                    "transfer_token": token,
                    "encrypted_backup": backup,
                },
                expected,
            )
        except ManagerError:
            _LOGGER.exception("Could not perform transfer recovery action %s", action)

    def diagnostics_for(self, entry_id: str) -> dict[str, Any]:
        """Return anonymous installation-wide counters for diagnostics."""
        return {
            "loaded_bridges": len(self.runtimes),
            "observations_received": self._observations_received,
            "observations_delivered": self._observations_delivered,
            "duplicates_collapsed": self._duplicates_collapsed,
            "estimator_forwards": self._estimator_forwards,
            "relay_attempts": self._relay_attempts,
            "relay_successes": self._relay_successes,
            "relay_failures": self._relay_failures,
            "last_strongest_was_this_bridge": self._last_strongest_entry_id == entry_id,
            "last_strongest_rssi": self._last_strongest_rssi,
        }

    def _bridge_title(self, entry_id: str) -> str:
        runtime = self.runtime_for(entry_id)
        return runtime.entry.title if runtime is not None else "Unknown bridge"


def _number_or_none(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None
