"""Behavioral tests for dependency-free multi-bridge coordination rules."""

import importlib.util
from pathlib import Path

SOURCE = (
    Path(__file__).parent.parent
    / "custom_components"
    / "somfy_io_manager"
    / "multibridge_logic.py"
)
SPEC = importlib.util.spec_from_file_location("somfy_multibridge_logic", SOURCE)
assert SPEC is not None and SPEC.loader is not None
LOGIC = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LOGIC)

cache_after_flush = LOGIC.cache_after_flush
normalize_remote = LOGIC.normalize_remote
merge_completion = LOGIC.merge_completion
observation_key = LOGIC.observation_key
observation_slots = LOGIC.observation_slots
prefer_candidate = LOGIC.prefer_candidate
relay_allowed = LOGIC.relay_allowed
resolve_remote_target_ids = LOGIC.resolve_remote_target_ids
transfer_next_action = LOGIC.transfer_next_action
status_response_is_new = LOGIC.status_response_is_new
terminal_supersedes_prefix = LOGIC.terminal_supersedes_prefix


def test_same_rf_sequence_from_three_bridges_has_one_identity() -> None:
    keys = {
        observation_key("0X123ABC", "open", 65535, 1)
        for _bridge in ("near", "owner", "far")
    }
    assert keys == {("sequence", "0X123ABC", 65535, "open")}
    assert cache_after_flush(next(iter(keys)))


def test_new_sequence_and_rollover_remain_distinct_commands() -> None:
    before = observation_key("0X123ABC", "close", 65535, 1)
    after = observation_key("0X123ABC", "close", 0, 1)
    assert before != after


def test_same_sequence_combines_different_receiver_magnitude_estimates() -> None:
    assert observation_key("0X123ABC", "tilt_clockwise", 41, 2) == observation_key(
        "0X123ABC", "tilt_clockwise", 41, 6
    )


def test_no_sequence_copy_deduplicates_only_in_live_window() -> None:
    key = observation_key("0X123ABC", "stop_my", None, 1)
    assert key == ("window", "0X123ABC", "stop_my", 1)
    assert not cache_after_flush(key)


def test_strongest_receiver_is_retained_for_diagnostics() -> None:
    assert prefer_candidate("", None, "bridge-a", -70.0)
    assert prefer_candidate("bridge-a", -70.0, "bridge-b", -42.0)
    assert not prefer_candidate("bridge-b", -42.0, "bridge-c", -66.0)


def test_individual_and_cross_bridge_group_mappings_are_unioned() -> None:
    shutters = [
        {"shutter_id": "a", "remote": "0x111111"},
        {"shutter_id": "b", "remote": "0x222222"},
        {"shutter_id": "c", "remote": "0x333333"},
    ]
    aliases = [{"remote": "0x111111", "shutter_ids": ["b", "c", "missing"]}]
    assert resolve_remote_target_ids(shutters, aliases, "0X111111") == {
        "a",
        "b",
        "c",
    }


def test_only_idempotent_single_frame_commands_are_relayed() -> None:
    assert all(relay_allowed(command) for command in ("open", "close", "stop"))
    assert not any(
        relay_allowed(command)
        for command in ("my", "position", "tilt_position", "tilt_stop", "pair")
    )


def test_transfer_order_never_activates_two_owners() -> None:
    assert transfer_next_action("prepared") == "import"
    assert transfer_next_action("imported") == "activate"
    assert transfer_next_action("activated") == "commit"
    assert transfer_next_action("committed") == "finalize"
    assert transfer_next_action("active") is None


def test_remote_normalization_rejects_malformed_private_ids() -> None:
    assert normalize_remote("0x12abEF") == "0X12ABEF"
    assert normalize_remote("0x123") == ""
    assert normalize_remote("not-a-remote") == ""


def test_consecutive_compact_relay_offers_without_event_are_new() -> None:
    before = '{"v":1,"action":"relay_offer","relay_envelope":"first"}'
    after = '{"v":1,"action":"relay_offer","relay_envelope":"second"}'
    assert status_response_is_new(
        before,
        None,
        after,
        {"v": 1, "action": "relay_offer", "relay_envelope": "second"},
    )
    assert not status_response_is_new(
        after,
        None,
        after,
        {"v": 1, "action": "relay_offer", "relay_envelope": "second"},
    )


def test_complete_tilt_supersedes_adjacent_incomplete_stop_prefix() -> None:
    assert terminal_supersedes_prefix(120, 121, "tilt_clockwise")
    assert terminal_supersedes_prefix(65535, 0, "tilt_counterclockwise")
    assert terminal_supersedes_prefix(120, 121, "stop_my")
    assert not terminal_supersedes_prefix(120, 122, "tilt_clockwise")
    assert not terminal_supersedes_prefix(120, 121, "open")


def test_terminal_copy_promotes_a_no_sequence_prefix_bucket() -> None:
    assert merge_completion(False, True)
    assert merge_completion(True, False)
    assert not merge_completion(False, False)


def test_slot_list_and_compact_mask_have_the_same_meaning() -> None:
    assert observation_slots({"slots": [0, 3, 19]}) == {0, 3, 19}
    assert observation_slots({"slot_mask": (1 << 0) | (1 << 3) | (1 << 19)}) == {
        0,
        3,
        19,
    }
