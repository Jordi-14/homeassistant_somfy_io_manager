# Changelog

## 0.8.1

- Serialize and deduplicate installation-wide physical group-remote alias
  restoration instead of launching one competing startup task per bridge.
- Wait for an authenticated, usable ESPHome manager status before restoring
  aliases; retry transient failures with bounded backoff and retry again when
  an offline bridge reconnects.
- Cancel alias listeners and in-flight synchronization safely when a bridge is
  unloaded, preventing tasks from retaining an obsolete runtime.
- Translate ESPHome service connection failures into the integration's
  `ManagerUnavailable` error so expected startup races no longer produce
  unhandled task exceptions.
- Replace the deprecated global device-identifier lookup with a config-entry-
  scoped lookup compatible with future Home Assistant releases.
- Preserve user-selected ESPHome transport IDs, names, areas, visibility and
  disabled state during startup; apply a shutter's configured area only when
  its virtual device is first created.
- Reconcile the stable multi-bridge source with the public default branch so
  the repository, release tag and HACS package describe the same implementation.

## 0.8.0

- Coordinate multiple ESPHome radio bridges as one Somfy IO installation,
  including global command serialization and deduplicated physical-remote
  reception across every online receiver.
- Support an optional secondary transmitter for repeat-safe OPEN, CLOSE and
  STOP commands while keeping non-idempotent MY and tilt gestures on the
  primary controller only.
- Add guarded GUI workflows for changing primary bridge ownership, assigning
  secondary bridges and recovering interrupted transfers without retransmitting
  PROG or consuming another shutter pairing.
- Preserve managed cover availability across ESPHome startup ordering, unknown
  post-boot positions and Home Assistant 2026.10 device-area validation.
- Promote the functionality verified through `0.8.0b1`–`0.8.0b5` to the first
  stable multi-bridge release.

## 0.8.0b5

- Restore managed shutter entities on Home Assistant 2026.10 by assigning each
  configured area to the shutter device instead of redundantly assigning it to
  its unnamed child entities. Home Assistant now rejects that entity-level area
  assignment during setup, which left every shutter with an area unavailable.

## 0.8.0b4

- Rebind managed covers when ESPHome transport entities appear after the Somfy
  platform has already loaded, including after hardware replacement and
  config-entry reload ordering changes.
- Use the online manager status transport as a safe control-availability
  fallback while the separate position transport is being discovered.

## 0.8.0b3

- Keep a managed shutter available when its online ESPHome time-based
  transport reports `unknown` because no post-boot position reference exists.
- Reserve the unavailable state for a missing or genuinely disconnected
  transport, while clearing stale position and tilt estimates when position is
  unknown.

## 0.8.0b2

- Finish controller-ownership moves through Home Assistant's supported options
   flow result instead of reloading the source entry underneath its open flow.
- Prevent a successfully completed firmware transfer from appearing as an
  `Unknown error` or leaving the next ownership move blocked.
- Retain the terminal transfer journal until post-reload reconciliation proves
  that the source and destination metadata both describe the new sole owner.
- Automatically clear terminal journals left by 0.8.0b1 when firmware and Home
  Assistant metadata already agree, without transmitting RF or changing a
  controller identity.

## 0.8.0b1

- Coordinate any number of configured ESPHome radio bridges as one installation.
- Deduplicate physical-remote observations across receivers, retain the strongest
  source, and forward only estimator updates when the owning bridge missed RF.
- Add optional exact-frame OPEN/CLOSE/STOP retransmission through one secondary
  bridge without copying controller keys or advancing another rolling stream.
- Add GUI-managed secondary assignments and guarded primary ownership moves with
  firmware-journal reconciliation, pre-transfer STOP, rollback before activation,
  and resumable cleanup afterward.
- Promote physical remote groups to a global cross-entry directory and privately
  hydrate remote IDs missing from legacy Home Assistant metadata.
- Serialize HA-originated RF transactions installation-wide and wait for native
  MY and Venetian tilt completion before another bridge transmits.

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
