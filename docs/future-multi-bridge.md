# Multi-bridge reliability and ownership

Status: implemented on the multi-bridge beta path and awaiting validation with
two physical ESP32 + CC1101 bridges. The established single-bridge behavior is
retained when no secondary bridge is configured or another bridge is offline.

## User model

Every shutter has two bridge assignments:

- **Primary bridge (mandatory):** the only owner of its controller identity,
  AES key, firmware slot, and rolling-code stream.
- **Secondary transmitting bridge (optional):** a stateless exact-frame relay
  used only for idempotent OPEN, CLOSE, and STOP commands.

Every online bridge is also a passive receiver for every recognized physical
remote. Reception is installation-wide and is not tied to the primary or
secondary transmitting assignment.

The integration continues to use one config entry per ESPHome bridge. A global
runtime coordinator joins those entries without moving credentials or exposing
controller secrets to Home Assistant entities.

## Safety invariants

1. One controller identity has exactly one active owner.
2. The secondary never stores a controller key or advances a rolling code.
3. The secondary never creates a fresh authenticated command. It can transmit
   only the primary's short-lived encrypted exact-frame envelope.
4. MY, percentage, tilt, PROG, pairing, commissioning, and transfer traffic is
   never redundantly transmitted.
5. Observation forwarding changes only the owner's position/tilt estimator and
   has no path to radio transmission or rolling-code storage.
6. A primary move archives and hard-quiesces the source before the destination
   can be activated.
7. If Home Assistant or a secondary fails, the primary retains normal local
   control.

## Redundant transmission

For a shutter with an online secondary, Home Assistant performs one atomic
transaction:

1. Arm the secondary for five seconds and obtain a random one-shot challenge.
2. Ask the primary to send OPEN, CLOSE, or STOP normally, bound to that
   challenge.
3. Read the encrypted relay envelope returned by the primary.
4. Give that envelope to the armed secondary.
5. The secondary authenticates it, verifies the challenge and command allow
   list, and transmits the exact same logical frame.

The envelope is short-lived, replay-bound, and contains no controller key. The
two transmissions carry the same controller identity and rolling sequence; the
secondary does not consume another receiver pairing slot and cannot fork the
rolling-code stream.

If secondary arming fails, the primary sends normally. If the primary succeeds
but relay transmission fails, Home Assistant still treats the user command as
successful and reports the relay failure only in logs and anonymous diagnostic
counters.

An installation-wide RF lock keeps one primary/relay transaction together and
stops two HA-triggered bridges from transmitting over each other. Native MY is
held until the firmware reports the full composite gesture complete, and the
same completion barrier covers all managed Venetian tilt sequences. A timed
intermediate-position STOP is still scheduled in firmware and is not yet a
cross-bridge relay candidate.

Setting a cover to exactly 0% or 100% is normalized to CLOSE or OPEN, so endpoint
requests can use the safe redundant path and the motor is allowed to reach its
physical limit. Middle percentages stay primary-only.

## Receiver diversity

Each bridge publishes normalized, receive-only observations containing a schema
version, remote identity, command, rolling sequence when available, step count,
RSSI, and its locally matched slots. Home Assistant collects copies for 250 ms
and deduplicates by:

```text
physical remote + rolling sequence + normalized gesture
```

The strongest copy is retained for diagnostics, while the largest compatible
Venetian step estimate is retained for the logical gesture. A completed
sequence-backed key remains cached briefly so a late third-bridge copy cannot
produce a second event. A new rolling sequence is always a new physical press,
including across the 65535→0 rollover.

Home Assistant maps the observation to shutters using both their individual
physical remote IDs and every configured group alias across every Somfy manager
entry. Each target's Detected remote sensor updates exactly once and records the
strongest source, RSSI, and number of hearing bridges.

If the owner was among the receivers, no forwarding occurs because it already
updated its estimator locally. If only a non-owner heard the gesture, Home
Assistant calls the owner's estimator-only service once. No RF is transmitted.

Legacy shutter metadata that lacks a physical remote ID is privately hydrated
at startup by querying its owning firmware slot. The ID remains excluded from
integration diagnostics.

## Changing the secondary

In the owning Somfy integration entry, open **Configure → Assign a secondary
transmitting bridge**. Select the shutter and another online bridge, or choose
**No secondary bridge**.

This is an HA metadata change only. It sends no pairing traffic, creates no
controller identity, and can be changed at any time.

## Moving the primary owner

Both bridges must use the same `somfy_io_backup_key`, must run matching
multi-bridge firmware, and must remain online during the transaction. In the
source entry, open **Configure → Move a shutter to another bridge** and choose
the destination and an empty slot.

The integration durably checkpoints this order:

1. **stop:** the current primary (and configured exact-frame secondary) sends a
   harmless STOP and waits briefly, ensuring the physical shutter is idle;
2. **prepare:** source archives and hard-quiesces the controller, then exports a
   token-bound encrypted backup;
3. **import:** destination decrypts the backup into an archived, non-transmitting
   slot;
4. **activate:** destination becomes the sole active owner while source remains
   archived and unable to transmit;
5. **commit:** source erases its archived copy;
6. **finalize:** destination erases its completed transfer journal;
7. Home Assistant moves the shutter metadata and reloads both entries.

Before activation, the wizard may safely abort the destination import and roll
back the source. After activation, rollback is intentionally unavailable: the
destination is already the owner, and recovery only retries source cleanup.

The HA journal is stored immediately before every requested phase. Firmware
journals are also queried during recovery so a lost HA response does not cause
an irreversible action to be guessed or blindly repeated. After a restart,
**Resume an interrupted bridge transfer** offers only actions safe for the
reconciled phase. It never pairs a replacement controller.

Moving ownership intentionally recreates the integration's cover, MY button,
Detected remote sensor, and virtual device under the destination config entry.
The old registry rows are removed first so their friendly object IDs can be
reclaimed. Entity-ID preservation is not guaranteed; review automations after a
move.

## Two-bridge validation checklist

1. Flash both bridges with unique ESPHome node names, unique API/OTA credentials,
   and the same backup/relay key.
2. Add a separate Somfy IO Shutter Manager config entry for each bridge.
3. Leave all existing shutters on the original primary initially.
4. Assign the new bridge as secondary for one easy-to-watch shutter.
5. Test OPEN, STOP, and CLOSE from HA; verify primary-only fallback by taking the
   secondary offline.
6. Confirm MY, middle percentage, and Venetian tilt are sent only by the primary.
7. Press the physical remote where both bridges hear it; verify one sensor event
   and a `heard_by_bridges` value of 2.
8. Shield or move the owner so only the other bridge hears; verify the owning
   cover estimate and Detected remote sensor still update without motor RF from
   the observer.
9. Test a physical group remote whose shutters have different primaries.
10. Move one controller to an empty slot on the new bridge, then test OPEN, STOP,
    CLOSE, MY, position, remote reception, and reboot persistence.
11. Interrupt a second test transfer before activation and verify rollback; then
    interrupt after activation and verify cleanup-only resume.

## Later work

- Validate relay timing and collision margins with three or more bridges.
- Correlate partial Venetian gesture evidence across receivers, rather than only
  complete observations independently decoded by one bridge.
- Move firmware-scheduled intermediate-position STOP operations into a fully
  bridge-aware transaction scheduler.
- Add optional automatic secondary recommendations based on long-term RSSI,
  while keeping every assignment and ownership transfer user-controlled.
- Coordinate several independent Home Assistant instances without weakening the
  single-owner invariant.
