"""Pure, dependency-free helpers for multi-bridge coordination."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def normalize_remote(value: Any) -> str:
    """Normalize a 24-bit physical remote identity."""
    text = str(value or "").strip().upper()
    if len(text) != 8 or not text.startswith("0X"):
        return ""
    try:
        int(text[2:], 16)
    except ValueError:
        return ""
    return text


def observation_key(
    remote: str, command: str, sequence: int | None, steps: int
) -> tuple[Any, ...]:
    """Build the RF identity used for a short cross-bridge collection bucket."""
    if sequence is not None:
        return ("sequence", remote, sequence & 0xFFFF, command)
    return ("window", remote, command, steps)


def cache_after_flush(key: tuple[Any, ...]) -> bool:
    """Only sequence-backed identities are safe to cache beyond one bucket."""
    return bool(key and key[0] == "sequence")


def prefer_candidate(
    current_entry_id: str,
    current_rssi: float | None,
    candidate_entry_id: str,
    candidate_rssi: float | None,
) -> bool:
    """Return whether a candidate is the best diagnostic observation copy."""
    return (
        current_entry_id == ""
        or candidate_rssi is not None
        and (current_rssi is None or candidate_rssi > current_rssi)
    )


def resolve_remote_target_ids(
    shutters: Iterable[dict[str, Any]],
    aliases: Iterable[dict[str, Any]],
    remote: str,
) -> set[str]:
    """Resolve individual and group remotes to permanent shutter identities."""
    normalized = normalize_remote(remote)
    available = {
        str(shutter["shutter_id"])
        for shutter in shutters
        if isinstance(shutter.get("shutter_id"), str)
    }
    result = {
        str(shutter["shutter_id"])
        for shutter in shutters
        if normalize_remote(shutter.get("remote")) == normalized
        and str(shutter.get("shutter_id")) in available
    }
    for alias in aliases:
        if normalize_remote(alias.get("remote")) != normalized:
            continue
        result.update(
            str(shutter_id)
            for shutter_id in alias.get("shutter_ids", [])
            if str(shutter_id) in available
        )
    return result


def relay_allowed(command: str) -> bool:
    """Limit redundant RF to commands whose exact duplicate is harmless."""
    return command in {"open", "close", "stop"}


def transfer_next_action(phase: str) -> str | None:
    """Return the safe next step in the single-owner transfer transaction."""
    return {
        "prepared": "import",
        "imported": "activate",
        "activated": "commit",
        "committed": "finalize",
        "active": None,
    }.get(phase)


def status_response_is_new(
    before_raw: str | None,
    before_event: Any,
    current_raw: str | None,
    current_status: dict[str, Any],
) -> bool:
    """Detect compact status responses that intentionally omit event counters."""
    current_event = current_status.get("event")
    if before_event is not None and current_event is not None:
        return current_event != before_event
    return current_raw != before_raw


def terminal_supersedes_prefix(
    prefix_sequence: int | None,
    terminal_sequence: int | None,
    terminal_command: str,
) -> bool:
    """Correlate a D200 prefix with its adjacent terminal gesture frame."""
    return (
        prefix_sequence is not None
        and terminal_sequence is not None
        and terminal_command in {"stop_my", "tilt_clockwise", "tilt_counterclockwise"}
        and ((prefix_sequence + 1) & 0xFFFF) == terminal_sequence
    )


def merge_completion(current: bool, candidate: bool) -> bool:
    """A terminal copy promotes a no-sequence bucket heard as a prefix elsewhere."""
    return current or candidate


def observation_slots(status: dict[str, Any]) -> set[int]:
    """Decode either the list or compact bit-mask form of matched slots."""
    slots = status.get("slots")
    if isinstance(slots, list):
        return {
            value
            for value in slots
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0
        }
    slot_mask = status.get("slot_mask")
    if isinstance(slot_mask, int) and not isinstance(slot_mask, bool):
        return {slot for slot in range(32) if slot_mask & (1 << slot)}
    return set()
