# Contributing

Thanks for improving Somfy IO Shutter Manager.

End-user installation, commissioning, calibration, recovery, and
troubleshooting guidance lives in [README.md](README.md). Release preparation
lives in [RELEASE.md](RELEASE.md).

## Repository boundary

This repository contains the Home Assistant custom integration:

- config and options flows;
- per-shutter devices, covers, buttons, and diagnostics;
- bridge-service coordination;
- encrypted-snapshot retention;
- translations, tests, release packaging, and user documentation.

Radio framing, CC1101 configuration, receive/transmit timing, rolling-code
storage, controller identities, NVS slot transactions, position timing, and
encrypted export belong in
[`esphome_somfy`](https://github.com/Jordi-14/esphome_somfy/tree/iohc-hardware-control).

When a change requires both layers, keep the firmware API versioned and update
the compatibility table in the README.

## Safety and privacy

This integration controls physical shutters and can transmit pairing frames.
Test while the intended shutter is visible, keep the original remote available,
and know how to stop or isolate power safely.

Do not commit or attach:

- controller AES keys or backup keys;
- encrypted recovery blobs or Home Assistant backups;
- rolling-code state;
- physical remote, node, or controller identifiers;
- Wi-Fi, ESPHome API, OTA, or Home Assistant credentials;
- local IP addresses, MAC addresses, or private hostnames;
- raw captures from a private installation.

Use fake values in examples and redact logs before opening an issue.

## Local checks

Run these before opening a pull request:

```bash
python -m pip install -r requirements_test.txt
python -m json.tool custom_components/somfy_io_manager/manifest.json >/dev/null
python -m json.tool custom_components/somfy_io_manager/strings.json >/dev/null
python -m json.tool custom_components/somfy_io_manager/translations/en.json >/dev/null
python -m json.tool custom_components/somfy_io_manager/translations/ca.json >/dev/null
python -m json.tool hacs.json >/dev/null
ruff check .
pytest -q
python -m compileall -q custom_components scripts tests
python scripts/package_hacs_release.py --output /tmp/somfy_io_manager.zip
```

When user-facing text changes, update `strings.json` and every translation.
When the firmware status schema changes, update `MANAGER_API_VERSION`, runtime
validation, tests, and the README compatibility table together.

## Architecture

One config entry represents one ESPHome bridge. The integration locates the
bridge's status and encrypted-backup transport entities plus its generated
manager actions.

```text
config_flow.py
  -> selects a compatible ESPHome bridge
  -> stages/imports/recovers one firmware slot
  -> confirms pairing and calibration
  -> stores anonymous per-shutter metadata in entry options

runtime.py
  -> calls ESPHome manager actions
  -> matches versioned status events
  -> retains opaque encrypted recovery snapshots

cover.py / button.py / sensor.py
  -> create one Somfy device per active shutter
  -> use hidden ESPHome slot covers as position/state transports
```

Important rules:

- A physical controller identity and rolling-code stream must have one active
  bridge owner.
- Pairing RF is sent only from the explicit commissioning step.
- Import, calibration edits, moves, and swaps must not send PROG or pairing RF.
- Full open and close requests must not schedule a timed STOP.
- Intermediate positions use calibrated timing and an explicit STOP.
- Stop and MY remain distinct user intentions.
- Diagnostics must never expose identities, keys, rolling codes, or recovery
  payloads.
- Slot numbers are storage locations, not shutter identity. Moves and swaps
  must preserve the Home Assistant shutter device.

## Adding or changing a config-flow step

1. Define the user-visible labels and errors in `strings.json`.
2. Mirror them in every translation.
3. Validate input before transmitting or committing firmware state.
4. Persist enough pending state to recover from a Home Assistant restart.
5. Distinguish safe local operations from irreversible or uncertain RF steps.
6. Add a regression test covering the successful and interrupted paths.

## Hardware evidence

New radio behavior requires repeatable observations from real hardware. A
useful sanitized report includes:

- ESP32 board and radio-module model;
- motor or shutter family and whether the remote is 1W or 2W;
- ESPHome and integration versions;
- exact starting motor state;
- one action per capture;
- observed motor movement or jog;
- whether the result survives reboot;
- a safe rollback or recovery procedure.

Protocol clues from another project are useful hypotheses, not proof. Do not
add a transmitted frame solely from an unverified public reference.

## Pull requests

Keep changes focused and explain:

- what changed and why;
- whether RF behavior changes;
- which manager API versions are compatible;
- which automated checks passed;
- which real hardware was tested, if applicable;
- how private data was excluded from fixtures and diagnostics.
