"""Source-level regressions for the optional Home Assistant integration."""

import json
from pathlib import Path

FLOW_SOURCE = (
    Path(__file__).parent.parent
    / "custom_components"
    / "somfy_io_manager"
    / "config_flow.py"
).read_text()
RUNTIME_SOURCE = (
    Path(__file__).parent.parent
    / "custom_components"
    / "somfy_io_manager"
    / "runtime.py"
).read_text()
COORDINATOR_SOURCE = (
    Path(__file__).parent.parent
    / "custom_components"
    / "somfy_io_manager"
    / "coordinator.py"
).read_text()
SENSOR_SOURCE = (
    Path(__file__).parent.parent
    / "custom_components"
    / "somfy_io_manager"
    / "sensor.py"
).read_text()
INTEGRATION_ROOT = (
    Path(__file__).parent.parent / "custom_components" / "somfy_io_manager"
)


def test_import_form_does_not_keyword_expand_voluptuous_markers():
    function = FLOW_SOURCE.split("def _slot_details_schema", 1)[1]
    function = function.split("def _validate_details", 1)[0]
    assert "**fields" not in function
    assert "fields.update(_details_schema(defaults).schema)" in function


def test_forms_do_not_request_permanent_entity_ids():
    schema = FLOW_SOURCE.split("def _details_schema", 1)[1]
    schema = schema.split("def _slot_details_schema", 1)[0]
    assert "CONF_COVER_ENTITY_ID" not in schema
    assert "ensure_shutter_id(details)" in FLOW_SOURCE


def test_bridge_discovery_accepts_current_and_legacy_sensor_domains():
    assert 'startswith(("sensor.", "text_sensor."))' in FLOW_SOURCE


def test_manager_status_requires_the_documented_api_version():
    constants = (INTEGRATION_ROOT / "const.py").read_text()
    assert "MANAGER_API_VERSION = 1" in constants
    assert 'status.get("v") != MANAGER_API_VERSION' in RUNTIME_SOURCE


def test_unitless_slot_selector_omits_null_unit_of_measurement():
    function = FLOW_SOURCE.split("def _number_selector", 1)[1]
    function = function.split("def _details_schema", 1)[0]
    assert "unit_of_measurement=unit" not in function
    assert "if unit is not None:" in function
    assert 'config["unit_of_measurement"] = unit' in function


def test_import_can_move_to_a_definitive_slot_without_commissioning_rf():
    function = FLOW_SOURCE.split("async def async_step_import_existing", 1)[1]
    function = function.split("async def async_step_resume_attempt", 1)[0]
    assert '"move"' in function
    assert '"target_slot": target_slot' in function
    assert '"pair"' not in function


def test_uncertain_pairing_retry_reuses_the_preserved_firmware_identity():
    resume = FLOW_SOURCE.split("async def async_step_resume_attempt", 1)[1]
    resume = resume.split("async def async_step_retry_pairing", 1)[0]
    retry = FLOW_SOURCE.split("async def async_step_retry_pairing", 1)[1]
    retry = retry.split("async def async_step_edit_calibration", 1)[0]

    assert "ACTION_RETRY" in resume
    assert 'state != "pair_sent"' in resume
    assert 'status.get("pair_retry") is not True' in resume
    assert '"action": "retry_arm"' in retry
    assert '"action": "pair"' in retry
    assert "CONF_RETRY_CONTROLLER_FAILED" in retry
    assert "CONF_PROGRAM_JOGGED" in retry
    assert '"stage"' not in retry
    assert '"discard"' not in retry


def test_new_pairing_and_future_moves_choose_explicit_slots():
    add = FLOW_SOURCE.split("async def async_step_add_shutter", 1)[1]
    add = add.split("async def async_step_capture_remote", 1)[0]
    move = FLOW_SOURCE.split("async def async_step_move_shutter", 1)[1]
    move = move.split("async def async_step_calibration_values", 1)[0]
    assert "requested_slot" in add
    assert '"target_slot": target_slot' in move


def test_remote_capture_queries_persisted_slot_instead_of_latest_status():
    function = FLOW_SOURCE.split("async def async_step_capture_remote", 1)[1]
    function = function.split("async def async_step_prepare_pairing", 1)[0]
    assert '{"action": "query", "slot": self._slot}' in function
    assert 'remote not in {"", "0x000000"}' in function
    assert "self.hass.states.get" not in function
    assert '{"action": "discover", "slot": self._slot}' in function


def test_home_assistant_forms_expose_exactly_slots_1_through_20():
    constants = (
        Path(__file__).parent.parent
        / "custom_components"
        / "somfy_io_manager"
        / "const.py"
    ).read_text()
    assert "MAX_SHUTTER_SLOTS = 20" in constants
    assert "_number_selector(1, 32, 1)" not in FLOW_SOURCE


def test_every_options_menu_entry_is_translated_in_all_catalogues():
    menu_entries = {
        "add_shutter",
        "import_existing",
        "edit_calibration",
        "move_shutter",
        "swap_shutters",
        "add_group_remote",
        "edit_group_remote",
        "remove_group_remote",
        "resume_attempt",
        "assign_secondary_bridge",
        "move_to_bridge",
        "resume_bridge_transfer",
    }
    for relative_path in (
        "strings.json",
        "translations/en.json",
        "translations/ca.json",
    ):
        catalogue = json.loads((INTEGRATION_ROOT / relative_path).read_text())
        labels = catalogue["options"]["step"]["init"]["menu_options"]
        assert set(labels) == menu_entries
        assert all(
            isinstance(labels[key], str) and labels[key].strip() for key in labels
        )


def test_gui_can_swap_two_managed_shutters_without_rf():
    function = FLOW_SOURCE.split("async def async_step_swap_shutters", 1)[1]
    function = function.split("async def async_step_finish_setup", 1)[0]
    assert '"swap"' in function
    assert '"swapped"' in function
    assert "find_transport_cover" in function
    assert "_swap_entity_rows" not in function
    assert "async_swap_backups" in function
    assert '"pair"' not in function


def test_shutter_controls_are_grouped_as_somfy_entities():
    root = Path(__file__).parent.parent
    constants = (
        root / "custom_components" / "somfy_io_manager" / "const.py"
    ).read_text()
    cover_platform = (
        root / "custom_components" / "somfy_io_manager" / "cover.py"
    ).read_text()
    button_platform = (
        root / "custom_components" / "somfy_io_manager" / "button.py"
    ).read_text()
    sensor_platform = (
        root / "custom_components" / "somfy_io_manager" / "sensor.py"
    ).read_text()
    shared_entity = (
        root / "custom_components" / "somfy_io_manager" / "entity.py"
    ).read_text()

    assert "Platform.COVER" in constants
    assert "Platform.BUTTON" in constants
    assert "Platform.SENSOR" in constants
    assert "class SomfyManagedCover" in cover_platform
    assert "class SomfyMyButton" in button_platform
    assert "shutter_device_info(entry, shutter)" in cover_platform
    assert "shutter_device_info(entry, shutter)" in button_platform
    assert "shutter_device_info(entry, shutter)" in sensor_platform
    assert "via_device" not in shared_entity


def test_my_button_uses_installation_coordinator_for_atomic_completion():
    root = Path(__file__).parent.parent
    button_platform = (
        root / "custom_components" / "somfy_io_manager" / "button.py"
    ).read_text()
    assert "coordinator.async_control" in button_platform
    assert 'self._runtime, self._shutter, "my"' in button_platform


def test_detected_remote_uses_friendly_actions_and_keeps_diagnostics():
    assert '"0X0000": "Open"' in SENSOR_SOURCE
    assert '"0XC800": "Close"' in SENSOR_SOURCE
    assert '"0XD200": "Stop/MY"' in SENSOR_SOURCE
    assert '"remote_id": status.get("remote")' in SENSOR_SOURCE
    assert '"raw_command": raw_command' in SENSOR_SOURCE
    assert '"sequence": status.get("sequence")' in SENSOR_SOURCE
    assert '"strongest_bridge": status.get("source_bridge")' in SENSOR_SOURCE
    assert '"heard_by_bridges": status.get("heard_by")' in SENSOR_SOURCE
    assert "_attr_force_update = True" in SENSOR_SOURCE


def test_group_remote_status_updates_every_target_sensor():
    assert 'target_slots = status.get("slots")' in SENSOR_SOURCE
    assert "self._slot in target_slots" in SENSOR_SOURCE
    assert 'status.get("slot") == self._slot' in SENSOR_SOURCE


def test_moving_a_shutter_disables_the_old_transport_cover():
    function = FLOW_SOURCE.split("def _prepare_transport_entity", 1)[1]
    function = function.split("def _ensure_entity_not_managed", 1)[0]
    assert "configure_transport_cover" in function
    assert "active=False" in function
    assert "self._replacing_slot" in function


def test_move_response_accepts_source_errors_and_destination_success():
    assert '("slot", "target_slot") if suffix == "move"' in RUNTIME_SOURCE
    assert 'status.get("slot") not in requested_slots' in RUNTIME_SOURCE


def test_venetian_gui_configures_native_tilt_without_affecting_normal_shutters():
    cover_source = (INTEGRATION_ROOT / "cover.py").read_text()
    assert '"venetian"' in FLOW_SOURCE
    assert '"venetian_configured"' in FLOW_SOURCE
    assert '"tilt_steps"' in (INTEGRATION_ROOT / "const.py").read_text()
    assert '"my_tilt_step"' in (INTEGRATION_ROOT / "const.py").read_text()
    assert '"my_tilt_step": int(' in FLOW_SOURCE
    assert "CoverEntityFeature.SET_TILT_POSITION" in cover_source
    assert "CoverEntityFeature.STOP_TILT" in cover_source
    assert "CoverEntityFeature.OPEN_TILT" in cover_source
    assert "CoverEntityFeature.CLOSE_TILT" in cover_source
    assert "async_set_cover_tilt_position" in cover_source
    assert 'self._async_control("tilt_position", 100.0)' in cover_source
    assert 'self._async_control("tilt_position", 0.0)' in cover_source
    assert "COVER_TYPE_SHUTTER" in cover_source


def test_venetian_default_matches_ha_open_tilt_convention():
    assert "defaults.get(CONF_TILT_INVERTED, True)" in FLOW_SOURCE
    assert "self._draft.get(CONF_TILT_INVERTED, True)" in FLOW_SOURCE


def test_venetian_physical_remote_events_are_friendly():
    assert '"0XF00D": "Tilt clockwise"' in SENSOR_SOURCE
    assert '"0XF00E": "Tilt counterclockwise"' in SENSOR_SOURCE
    assert 'f"{command_name} ({step_count} steps)"' in SENSOR_SOURCE
    assert '"steps": step_count' in SENSOR_SOURCE


def test_group_remote_aliases_support_any_number_of_shutters_without_pairing():
    add = FLOW_SOURCE.split("async def async_step_add_group_remote", 1)[1]
    add = add.split("async def async_step_capture_group_remote", 1)[0]
    capture = FLOW_SOURCE.split("async def async_step_capture_group_remote", 1)[1]
    capture = capture.split("async def async_step_edit_group_remote", 1)[0]

    assert "multiple=True" in add
    assert '"remote_alias"' in add
    assert '"action": "discover"' in add
    assert '"action": "set"' in capture
    assert '"pair"' not in add
    assert '"pair"' not in capture
    assert "selected.update(alias[CONF_SHUTTER_IDS])" in capture


def test_group_membership_uses_permanent_shutter_ids_and_can_be_replaced():
    constants = (INTEGRATION_ROOT / "const.py").read_text()
    assert 'CONF_REMOTE_ALIASES = "remote_aliases"' in constants
    assert 'CONF_SHUTTER_IDS = "shutter_ids"' in constants
    assert "CONF_SHUTTER_ID" in FLOW_SOURCE
    targets = FLOW_SOURCE.split("async def async_step_group_remote_targets", 1)[1]
    targets = targets.split("async def async_step_remove_group_remote", 1)[0]
    assert "multiple=True" in targets
    assert "_save_remote_alias" in targets
    assert '"action": "set"' in targets


def test_group_directory_is_global_across_all_bridge_entries():
    aliases = FLOW_SOURCE.split("def _remote_aliases", 1)[1]
    aliases = aliases.split("def _active_shutters", 1)[0]
    assert "self._coordinator.runtimes.values()" in aliases
    assert "merged.setdefault(remote, set()).update" in aliases
    assert "def _save_global_remote_aliases" in aliases
    assert "async_update_entry" in aliases


def test_group_aliases_are_reapplied_after_bridge_replacement():
    init_source = (INTEGRATION_ROOT / "__init__.py").read_text()
    assert "async_sync_remote_aliases" in RUNTIME_SOURCE
    assert '"action": "set"' in RUNTIME_SOURCE
    assert "CONF_SHUTTER_ID" in RUNTIME_SOURCE
    assert "_async_sync_remote_aliases(coordinator)" in init_source


def test_config_entry_v4_adds_bridge_ownership_without_changing_identity():
    init_source = (INTEGRATION_ROOT / "__init__.py").read_text()
    assert "VERSION = 4" in FLOW_SOURCE
    assert "entry.version < 3" in init_source
    assert "options.setdefault(CONF_REMOTE_ALIASES, [])" in init_source
    assert "entry.version < 4" in init_source
    assert "shutter[CONF_PRIMARY_ENTRY_ID] = entry.entry_id" in init_source


def test_secondary_assignment_and_transfer_are_gui_managed():
    constants = (INTEGRATION_ROOT / "const.py").read_text()
    coordinator = (INTEGRATION_ROOT / "coordinator.py").read_text()
    assert 'CONF_SECONDARY_ENTRY_ID = "secondary_entry_id"' in constants
    assert "async_step_assign_secondary_bridge" in FLOW_SOURCE
    assert "async_step_move_to_bridge" in FLOW_SOURCE
    assert "async_step_resume_bridge_transfer" in FLOW_SOURCE
    assert '"redundant_control"' in coordinator
    assert '"relay"' in coordinator
    assert '"transfer"' in coordinator


def test_cross_bridge_move_removes_old_registry_rows_before_reload():
    entity_source = (INTEGRATION_ROOT / "entity.py").read_text()
    transfer = FLOW_SOURCE.split("async def _async_finish_bridge_transfer", 1)[1]
    transfer = transfer.split("async def async_step_add_shutter", 1)[0]
    assert "def remove_shutter_registry_rows" in entity_source
    assert "entity_registry.async_remove" in entity_source
    assert "remove_shutter_registry_rows" in transfer


def test_cross_bridge_move_lets_options_flow_commit_its_own_entry():
    transfer = FLOW_SOURCE.split("async def _async_finish_bridge_transfer", 1)[1]
    transfer = transfer.split("async def async_step_add_shutter", 1)[0]
    assert "destination.entry, options=destination_options" in transfer
    assert "self.config_entry, options=source_options" not in transfer
    assert 'return self.async_create_entry(title="", data=source_options)' in transfer
    assert "async_set_pending_transfer(None)" not in transfer


def test_active_transfer_journal_clears_only_after_metadata_converges():
    coordinator = (INTEGRATION_ROOT / "coordinator.py").read_text()
    reconcile = coordinator.split("async def async_reconcile_runtime_transfer", 1)[1]
    reconcile = reconcile.split("async def _async_reconcile_transfer", 1)[0]
    assert 'reconciled.get("phase") == "active"' in reconcile
    assert "transfer_metadata_committed(" in reconcile
    assert "await source.async_set_pending_transfer(None)" in reconcile


def test_legacy_missing_remote_is_privately_hydrated_from_owner_slot():
    init_source = (INTEGRATION_ROOT / "__init__.py").read_text()
    hydrate = init_source.split("async def _async_hydrate_remote_metadata", 1)[1]
    hydrate = hydrate.split("def _valid_remote", 1)[0]
    assert '{"action": "query", "slot": int(shutter[CONF_SLOT])}' in hydrate
    assert "shutter[CONF_REMOTE]" in hydrate
    assert "_LOGGER" in hydrate
    assert "remote," not in hydrate


def test_exact_frame_relay_uses_one_shot_token_and_never_relays_my_or_tilt():
    assert '{"action": "arm", "payload": ""}' in COORDINATOR_SOURCE
    assert '"relay_token": token' in COORDINATOR_SOURCE
    assert '{"action": "send", "payload": envelope}' in COORDINATOR_SOURCE
    assert '{"action": "cancel", "payload": token}' in COORDINATOR_SOURCE
    assert "relay_allowed(command)" in COORDINATOR_SOURCE


def test_transfer_contract_and_order_preserve_single_owner():
    resume = COORDINATOR_SOURCE.split("async def async_resume_transfer", 1)[1]
    resume = resume.split("async def async_rollback_transfer", 1)[0]
    assert '"transfer_token": token' in resume
    assert resume.index('"action": "import"') < resume.index('"action": "activate"')
    assert resume.index('"action": "activate"') < resume.index('"action": "commit"')
    assert resume.index('"action": "commit"') < resume.index('"action": "finalize"')
    assert "_async_reconcile_transfer" in resume
    start = COORDINATOR_SOURCE.split("async def _async_start_transfer", 1)[1]
    start = start.split("async def async_resume_transfer", 1)[0]
    assert start.index("async_set_pending_transfer") < start.index(
        '"action": "prepare"'
    )


def test_transfer_recovery_queries_firmware_instead_of_guessing_lost_response():
    reconcile = COORDINATOR_SOURCE.split("async def _async_reconcile_transfer", 1)[1]
    reconcile = reconcile.split("async def _async_query_transfer", 1)[0]
    query = COORDINATOR_SOURCE.split("async def _async_query_transfer", 1)[1]
    query = query.split("async def _ensure_destination_empty", 1)[0]
    assert 'destination_phase == "active"' in reconcile
    assert 'destination_phase == "archived"' in reconcile
    assert 'source_phase == "archived"' in reconcile
    assert '"action": "query"' in query
    assert '"transfer_token": ""' in query


def test_receive_diversity_handles_late_copies_prefixes_and_slot_masks():
    assert "OBSERVATION_COLLECTION_SECONDS = 0.25" in COORDINATOR_SOURCE
    assert "_completed_observations" in COORDINATOR_SOURCE
    assert "terminal_supersedes_prefix" in COORDINATOR_SOURCE
    assert 'status.get("complete") is not False' in COORDINATOR_SOURCE
    assert "owner.entry.entry_id in pending.complete_sources" in COORDINATOR_SOURCE
    assert "observation_slots(status)" in COORDINATOR_SOURCE


def test_multibridge_my_waits_for_firmware_completion_under_global_lock():
    assert "self._rf_lock = asyncio.Lock()" in COORDINATOR_SOURCE
    assert (
        '"command_complete" if complete_ack else "command_sent"' in COORDINATOR_SOURCE
    )
    assert "MY_RADIO_GUARD_SECONDS" in COORDINATOR_SOURCE
