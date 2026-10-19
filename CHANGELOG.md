# Changelog

## 0.6.3

- Preserve ESPHome entity IDs, names, areas, visibility, and user-disabled state
  during startup and reload. Keep delayed transport discovery read-only.
- Limit transport preparation to explicit commissioning and preserve user
  customizations there too. Stop deleting ESPHome button entries.
- Apply a shutter's initial area only when its device is first created.
- Report unavailable bridges and rejected actions through translated Home
  Assistant errors.

## 0.6.2

- Update GitHub Actions checkout and Python setup to version 7.

## 0.6.1

- Add privacy-preserving Home Assistant diagnostics.
- Add a HACS release ZIP and automated release-asset publishing.
- Expand installation, commissioning, troubleshooting, safety, and development documentation.
- Add structured project tooling and feature-request guidance.

## 0.6.0

- Add graphical commissioning for independent io-homecontrol 1W shutters.
- Add per-shutter cover, native MY button, and physical-remote diagnostic.
- Add calibrated percentage positioning and physical-remote state sync.
- Add safe import, move, and occupied-slot swap operations.
- Add encrypted controller backup and interrupted-commissioning recovery.
- Add English and Catalan interfaces.
