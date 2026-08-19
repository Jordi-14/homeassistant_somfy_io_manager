# Changelog

## 0.7.0b6

- Queue simultaneous MY requests on the bridge so each shutter receives its
  complete authenticated STOP/execute/press/release gesture without another
  shutter's MY frames being interleaved.
- Start the next queued shutter immediately after the release burst and a
  short radio handoff gap, avoiding conservative delays in Home Assistant.
- Coalesce duplicate pending MY targets and accept the firmware's explicit
  queued acknowledgement.

## 0.7.0b5

- Add a protected retry path for an uncertain post-PROG pairing attempt.
- Reuse the preserved controller identity, AES key and rolling-code stream
  instead of creating another controller or consuming another firmware slot.
- Require the user to verify that the preserved slot does not control the
  shutter and to obtain a fresh first PROG jog before each retransmission.
- Detect firmware without same-identity retry support and show a specific
  update message instead of failing silently.

## 0.7.0b4

- Decode the Situo Variation wheel's signed magnitude field so a large
  physical roll is no longer always reported as one tilt step.
- Show the estimated effective step count in friendly detected-remote history,
  while retaining the raw command and numeric step diagnostic attribute.
- Use the matching firmware's hardware-verified two-step compound gestures for
  smoother Home Assistant tilt control without sacrificing reachable-position
  rounding or endpoint margins.

## 0.7.0b3

- Update every assigned shutter's detected-remote sensor from one physical
  group command instead of losing intermediate target updates to ESPHome state
  coalescing.
- Work with the matching firmware's additive `slots` status field while
  retaining compatibility with single-slot version-1 status payloads.
- Correlate the native stop-then-tilt frame sequence in the matching firmware,
  preventing a Venetian wheel gesture from appearing as an extra STOP/MY.
- Hardware-verify All-channels wheel control across two Venetian blinds.

## 0.7.0b2

- Align Venetian tilt with Home Assistant's convention: 100% is fully open and
  0% is fully closed.
- Expose native open-tilt and close-tilt actions now that both endpoints are
  unambiguous.
- Report UP as fully open tilt and DOWN as fully closed tilt for both Home
  Assistant commands and decoded physical-remote commands.
- Improve physical-remote capture reliability with a hardware-validated
  noise-relative carrier threshold, full receiver gain, strict sync, and the
  original 60-byte capture window in the matching ESPHome component.
- Add privacy-safe raw, CRC-valid, and accepted-command counters to downloaded
  diagnostics.
- Add GUI-managed physical group remotes. A group may synchronize any number
  of shutters, and a shutter may belong to multiple overlapping groups.
- Keep group membership attached to permanent shutter identities across slot
  moves and swaps, and restore it automatically after bridge replacement.
- Use the physical source identity and measured RSSI in remote events, including
  events received through a group mapping.
- Keep group discovery and maintenance receive-only: no PROG, pairing slot, or
  transmitted radio frame is required.

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
