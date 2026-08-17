# Somfy IO Shutter Manager

An unofficial Home Assistant custom integration for independently controlling
Somfy io-homecontrol 1W shutters through an ESP32 and a CC1101 radio.

The integration adds a graphical commissioning and maintenance interface on
top of the `somfy_iohc_manager` ESPHome component. Each shutter appears as its
own Home Assistant device with:

- an open, close, stop, and percentage-position cover;
- a native MY/favourite-position button;
- a friendly physical-remote event sensor;
- independent opening time, closing time, and MY calibration;
- its own controller identity, AES key, and rolling-code stream.

> This project is not affiliated with or endorsed by Somfy. io-homecontrol is a
> registered trademark of its respective owner.

## Status

The io-homecontrol **1W path is hardware validated**. This includes independent
pairing, open, close, stop, native MY, intermediate positioning, physical-remote
synchronisation, repeated-button logging, multiple slots, and reboot
persistence. The bidirectional 2W path is not currently supported by this Home
Assistant manager.

| Integration | Manager API | Tested Home Assistant | Tested ESPHome |
|---|---:|---:|---:|
| 0.6.x | 1 | 2026.7 | 2026.7.4 |

## Requirements

- Home Assistant 2026.7 or newer;
- HACS;
- an ESP32 running the compatible `somfy_iohc_manager` firmware;
- a 868 MHz CC1101 module suitable for io-homecontrol;
- at least one existing physical remote for commissioning each shutter.

The firmware component currently lives in the
[`iohc-hardware-control`](https://github.com/Jordi-14/esphome_somfy/tree/iohc-hardware-control)
branch of `Jordi-14/esphome_somfy`. It is being proposed to the upstream
external-component project.

## Install the firmware

Start from [`examples/somfy-io-control.yaml`](examples/somfy-io-control.yaml)
and create the corresponding secrets from
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
characters. Do not reuse a controller AES key.

## Install through HACS

Until the repository appears in the default HACS catalogue:

1. Open **HACS → Integrations**.
2. Open the three-dot menu and choose **Custom repositories**.
3. Add `https://github.com/Jordi-14/homeassistant_somfy_io_manager` as an
   **Integration** repository.
4. Download **Somfy IO Shutter Manager**.
5. Restart Home Assistant.
6. Open **Settings → Devices & services → Add integration**.
7. Select **Somfy IO Shutter Manager** and choose the online ESPHome bridge.

## Add a shutter

Open the integration and select **Configure → Pair a new shutter**. Choose a
firmware slot and enter the shutter name, area, full opening time, full closing
time, and approximate MY percentage.

The wizard then asks you to:

1. briefly press OPEN or CLOSE on the existing physical remote;
2. put only the intended shutter into programming mode with the remote's PROG
   button;
3. confirm the first motor jog;
4. allow the bridge to send one pairing transmission;
5. confirm the second motor jog.

Do not press PROG until the wizard asks for it. Somfy motors have a limited
number of paired controllers, and removing one may require a complete motor
reset.

## Existing paired controllers

An existing controller identity can be imported by the ESPHome YAML and then
adopted through **Configure → Import an already-paired slot**. Importing,
moving, or swapping slots does not transmit pairing RF.

Never power two bridges using the same restored controller identity. A rolling
code stream must have exactly one active owner.

## Position behaviour

Position is estimated from calibrated travel time because 1W motors do not
return authoritative position feedback:

- 100% sends OPEN and lets the motor stop at its physical end stop;
- 0% sends CLOSE and lets the motor stop at its physical end stop;
- intermediate percentages send OPEN or CLOSE followed by STOP at the
  estimated target;
- selecting the configured MY percentage while idle recalls the native motor
  favourite.

Full end-stop runs correct accumulated position-estimation error.

## Recovery and safety

The ESP persists rolling codes before transmission and publishes an AES-GCM
encrypted controller snapshot after rolling-code changes. Home Assistant keeps
the latest opaque snapshot in private integration storage.

Keep regular Home Assistant backups and preserve the ESPHome backup key. Never
publish controller keys, recovery blobs, real remote identifiers, Wi-Fi
credentials, or API/OTA credentials in an issue.

## Development

```bash
python -m pip install -r requirements_test.txt
ruff check .
pytest -q
```

Issues should include Home Assistant, integration, ESPHome, board, and radio
module versions plus redacted logs.

## License

MIT. See [LICENSE](LICENSE).
