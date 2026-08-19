# Changelog

## 0.7.0b1

- Add GUI-managed Venetian shutters with native Home Assistant tilt controls.
- Add user calibration for effective tilt detents, wheel direction, and the
  saved MY tilt step.
- Quantize intermediate tilt requests to reachable detents and use an endpoint
  margin to resynchronize physical 0% and 100% limits.
- Synchronize tilt estimates from the physical variation wheel and from
  open, close, stop, and MY behavior.
- Preserve roller-shutter behavior and the existing manager API version.

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
