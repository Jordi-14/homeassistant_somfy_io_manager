# Somfy IO Shutter Manager for Home Assistant

[![Validation](https://github.com/Jordi-14/homeassistant_somfy_io_manager/actions/workflows/validate.yml/badge.svg)](https://github.com/Jordi-14/homeassistant_somfy_io_manager/actions/workflows/validate.yml)
[![GitHub Release](https://img.shields.io/github/v/release/Jordi-14/homeassistant_somfy_io_manager)](https://github.com/Jordi-14/homeassistant_somfy_io_manager/releases/latest)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.7%2B-41BDF5?logo=homeassistant&logoColor=white)](https://www.home-assistant.io/)
[![License](https://img.shields.io/github/license/Jordi-14/homeassistant_somfy_io_manager)](LICENSE)

An unofficial Home Assistant integration for independent, local control of
Somfy io-homecontrol 1W shutters through one or more ESP32 and CC1101 radio
bridges.

The integration provides the graphical commissioning and maintenance layer for
the `somfy_iohc_manager` ESPHome component. Each shutter is represented as its
own Home Assistant device, while Wi-Fi, uptime, commissioning transport, and
other bridge diagnostics remain on the ESPHome device.

This project is not affiliated with or endorsed by Somfy. io-homecontrol is a
registered trademark of its respective owner.

## Status and compatibility

The io-homecontrol **1W roller-shutter path is hardware validated**. Tests cover pairing,
independent open/close/stop/MY control, intermediate positioning, repeated
physical-remote events, slot management, encrypted recovery, and persistence
after reboot.

The Venetian-blind path has also been hardware validated with a Situo 5
Variation A/M io Pure II. It uses the same 1W pairing and controller identity
as an ordinary shutter, with native step-based slat tilt added on top.

The bidirectional 2W path is not currently supported by this manager.

| Integration | Manager API | Home Assistant | ESPHome | Status |
| --- | ---: | ---: | ---: | --- |
| 0.6.x | 1 | 2026.7+ | 2026.7.4 tested | 1W hardware validated |

Validated bridge hardware:

- Seeed Studio XIAO ESP32-S3;
- E07 900-series CC1101 module at the io-homecontrol frequency;
- one or more Somfy/Nice io-homecontrol 1W shutters with an existing remote.

Radio modules sold primarily for another band may sometimes tune to the target
frequency, but sensitivity, filtering, output, and regulatory compliance can
differ. Use a module intended for the local io-homecontrol band in a permanent
installation.

## Features

- One Home Assistant device per physical shutter.
- Native cover controls for open, close, stop, and estimated percentage.
- Native Venetian tilt controls with calibrated detents, direction inversion,
  and endpoint resynchronization.
- Dedicated MY/favourite-position button.
- Friendly physical-remote event sensor showing Open, Close, Stop/MY, or MY.
- Physical-remote synchronization of the estimated cover state.
- Independent opening time, closing time, and MY calibration per shutter.
- Per-Venetian-blind tilt step count, wheel-direction calibration, and saved MY
  slat step.
- Twenty persistent firmware slots per bridge.
- Explicit slot selection when adding or importing a shutter.
- Safe moves to empty slots and direct swaps between occupied slots.
- Import of controller identities already paired through ESPHome YAML.
- AES-GCM encrypted controller recovery snapshots retained in private Home
  Assistant storage.
- Resumable commissioning after Home Assistant or ESP restarts.
- English and Catalan config flows.
- Privacy-preserving downloadable diagnostics.
- Support for multiple bridges when radio range requires them.

## Architecture

The project is deliberately split into two repositories:

| Layer | Repository | Responsibility |
| --- | --- | --- |
| ESPHome | [`Jordi-14/esphome_somfy`](https://github.com/Jordi-14/esphome_somfy/tree/iohc-hardware-control) | Radio framing, receive/transmit, rolling codes, NVS identities, slot state, position timing, encrypted export, and manager API. |
| Home Assistant | This repository | Config and options flows, per-shutter devices and entities, diagnostics, encrypted-snapshot retention, and safe slot workflows. |

The firmware work is proposed upstream in
[`leonardpitzu/esphome_somfy#9`](https://github.com/leonardpitzu/esphome_somfy/pull/9).
HACS default-catalog inclusion is tracked in
[`hacs/default#10085`](https://github.com/hacs/default/pull/10085).

## Requirements

- Home Assistant 2026.7 or newer;
- HACS, or manual custom-component installation;
- an ESP32 running the compatible manager firmware;
- a CC1101 radio suitable for the local io-homecontrol frequency;
- an existing physical remote for each shutter being commissioned;
- physical access to the shutter during pairing and testing.

Pairing creates an additional controller identity. Some motors have a limited
controller table, and removing a controller may require a complete motor reset.
Keep the original remote and motor instructions available.

## Installation

### HACS

Until the repository is merged into the default HACS catalogue:

1. Open **HACS → Integrations**.
2. Open the three-dot menu and choose **Custom repositories**.
3. Add `https://github.com/Jordi-14/homeassistant_somfy_io_manager` as an
   **Integration**.
4. Search for **Somfy IO Shutter Manager** and select **Download**.
5. Restart Home Assistant.

After default-catalog inclusion, the custom repository step will no longer be
needed.

### Manual

Copy this directory into the Home Assistant configuration folder:

```text
custom_components/somfy_io_manager
```

Restart Home Assistant after installing or updating it.

## Firmware setup

Start from [`examples/somfy-io-control.yaml`](examples/somfy-io-control.yaml)
and create the corresponding private values from
[`examples/secrets.example.yaml`](examples/secrets.example.yaml).

The essential manager configuration is:

```yaml
api:
  custom_services: true

external_components:
  - source: github://Jordi-14/esphome_somfy@iohc-hardware-control
    components: [somfy, somfy_iohc_manager]

text_sensor:
  - platform: template
    id: somfy_commissioning_status
    name: "Commissioning Status"
    entity_category: diagnostic
  - platform: template
    id: somfy_encrypted_controller_backup
    name: "Encrypted Controller Backup"
    entity_category: diagnostic

somfy_iohc_manager:
  id: shutter_manager
  somfy_id: iohc_radio
  status_sensor: somfy_commissioning_status
  backup_sensor: somfy_encrypted_controller_backup
  backup_key: !secret somfy_io_backup_key
  max_shutters: 20
```

Generate a unique 16-byte backup key and store it as exactly 32 hexadecimal
characters. Do not reuse a controller AES key, publish the backup key, or place
it directly in a public YAML file.

Flash the firmware, add its ESPHome device to Home Assistant, and confirm the
commissioning status entity reports manager API version 1 before adding this
integration.

## Quick setup

1. Open **Settings → Devices & services → Add integration**.
2. Search for **Somfy IO Shutter Manager**.
3. Select the compatible online ESPHome bridge.
4. Open the new integration entry and choose **Configure**.
5. Pair a new shutter or import an already-paired firmware slot.

One integration entry manages one ESPHome bridge. Repeat the setup for every
additional bridge needed for radio coverage.

## Entities

Each active shutter becomes one Somfy device containing:

| Entity | Purpose |
| --- | --- |
| Cover | Open, close, stop, and estimated position. Venetian devices also expose native slat tilt. |
| MY position button | Recall the motor's native favourite position. |
| Detected remote sensor | Record every decoded physical-remote press with sanitized diagnostic attributes. |

The fixed slot covers generated by ESPHome remain hidden transport entities.
They own timing and radio state but are not intended for dashboards.

## Pair a new shutter

Open **Configure → Pair a new shutter**, select a firmware slot, and enter:

- shutter name and optional Home Assistant area;
- full opening time;
- full closing time;
- approximate MY percentage;
- cover type; for a Venetian blind, the wheel detents that actually move the
  slats between its two endpoints and whether clockwise should decrease the HA
  tilt percentage;
- for a Venetian blind, the saved MY slat step counted clockwise from the
  counterclockwise endpoint. Do not count an extra click used only to verify an
  endpoint.

For Venetian blinds, a 0% or 100% tilt request always performs one complete
calibrated sweep plus a small endpoint margin. This establishes a known
endpoint even after initial pairing or if the restored estimate no longer
matches the physical slats. Intermediate percentages send only the exact
number of required detents, are rounded to the nearest reachable position, and
finish by reporting that reachable position rather than the original request.

The tilt percentage describes the physical wheel range, not how much light is
passing through the slats. Both ends of that range can be closed in opposite
directions; on the tested blind, the horizontal fully open attitude is the
midpoint. For this reason the entity exposes percentage and stop-tilt controls,
but no ambiguous tilt-open or tilt-close action.

Lift movement also changes the physical slats. OPEN immediately puts them in
the horizontal attitude and CLOSE in the clockwise closed attitude; STOP leaves
that new attitude in place. A tilt request during lift movement stops the lift,
applies the tilt, and never resumes the interrupted height movement. Native MY
temporarily uses the appropriate travel attitude and restores the motor's saved
MY tilt after the height movement finishes; the configured MY slat step keeps
Home Assistant's estimate synchronized without extra wheel transmissions.

The wizard then asks you to:

1. briefly press OPEN or CLOSE on the existing physical remote;
2. put only the intended shutter into programming mode with that remote's PROG
   button;
3. confirm the motor jog;
4. allow the bridge to send one pairing transmission;
5. confirm the second motor jog.

Do not press PROG before the wizard asks. Keep the shutter visible and stop if a
different motor jogs.

## Import, move, and swap slots

Use **Import an already-paired slot** for controller identities configured in
ESPHome YAML or restored before Home Assistant adopted them. Import does not
send pairing RF.

Slot numbers are organizational and can be changed later:

- **Move a shutter** transfers it to an empty slot.
- **Swap two shutter slots** exchanges two occupied slots directly.
- names, areas, devices, calibration, and encrypted snapshots follow the
  physical shutters.

Moves and swaps commit through firmware NVS safeguards and send no PROG or
pairing transmissions.

## Position and MY behaviour

One-way motors do not return authoritative position. Position is estimated
from calibrated travel time and corrected at full end stops:

- 100% sends OPEN and lets the physical end stop finish the run;
- 0% sends CLOSE and lets the physical end stop finish the run;
- intermediate percentages move in the required direction and send STOP at the
  estimated target;
- pressing Stop sends only the stopping frame;
- pressing MY sends the complete native MY sequence, so an idle shutter moves
  to its favourite position and a moving shutter first stops cleanly.

If position gradually drifts, run the shutter fully to an end stop and verify
the configured opening and closing times.

## Recovery and ownership

The ESP reserves and persists rolling codes before transmission. After relevant
changes it publishes an AES-GCM encrypted controller snapshot, which Home
Assistant stores as an opaque private value.

Keep regular Home Assistant backups and preserve the ESPHome backup key. Never
power two bridges using the same restored controller identity: a rolling-code
stream must have exactly one active owner.

If commissioning is interrupted after a radio transmission, the options flow
offers a safe resume or discard path. Follow that path instead of manually
repeating pairing frames.

## Troubleshooting

### No compatible ESPHome bridge appears

- Confirm the ESPHome device is online in Home Assistant.
- Confirm both **Commissioning Status** and **Encrypted Controller Backup**
  entities exist.
- Confirm all manager actions are registered by the ESPHome device.
- Recompile and reinstall the current manager firmware after changing the
  external-component source.
- Restart Home Assistant after updating the custom integration.

### Pairing does not produce the expected jog

- Keep only the intended shutter in programming mode.
- Use a short PROG press unless the motor documentation says otherwise.
- Do not repeat the pairing transmission blindly.
- Use the wizard's recovery path after an interrupted or uncertain attempt.

### Commands work but estimated position is wrong

- Measure full travel in both directions and update calibration.
- Complete one full open or close run to correct accumulated drift.
- Remember that movement from the physical remote is observable only when its
  frames are received by the bridge.

### Debug logging

Enable temporary debug logging from the integration entry menu, reproduce the
problem, then disable logging to download the log bundle. Alternatively:

```yaml
logger:
  default: info
  logs:
    custom_components.somfy_io_manager: debug
```

Review all files before sharing them. Never post ESPHome secrets, controller
keys, encrypted recovery values, remote or node identifiers, Home Assistant
backups, local addresses, or Wi-Fi/API/OTA credentials.

### Diagnostics

Open **Settings → Devices & services → Somfy IO Shutter Manager**, open the
integration menu, and choose **Download diagnostics**.

Diagnostics report manager API compatibility, entity and action availability,
anonymous slot calibration, recovery availability, and commissioning health.
They deliberately omit shutter names, areas, internal identities, controller
keys, recovery payloads, rolling codes, remote IDs, node IDs, and generated
service/entity IDs. Review the file before attaching it to an issue.

## Development

```bash
python -m pip install -r requirements_test.txt
ruff check .
pytest -q
python -m compileall -q custom_components scripts tests
python scripts/package_hacs_release.py
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for repository boundaries, testing, and
safe hardware-reporting requirements. Release preparation is documented in
[RELEASE.md](RELEASE.md), and user-visible changes are listed in
[CHANGELOG.md](CHANGELOG.md).

## Security

Use GitHub's private vulnerability-reporting feature for security-sensitive
problems. See [SECURITY.md](SECURITY.md).

## License

MIT. See [LICENSE](LICENSE).
