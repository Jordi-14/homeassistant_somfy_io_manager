# Future project: multi-bridge reliability

Status: the first single-bridge increment now queues complete MY gestures.
General command scheduling, cross-bridge coordination, and receiver diversity
remain design notes only. Nothing in this document authorizes automatic
pairing, controller cloning, or failover transmission.

Larger installations may use several bridges for radio coverage. Two related
features would make that topology safer and more reliable:

1. coordinated transmission when many shutters are commanded together;
2. receiver diversity, where any bridge may hear a physical remote and Home
   Assistant applies the observation to the shutter owned by another bridge.

## Safety boundaries

- A controller identity, AES key, and rolling-code stream must have exactly one
  active transmitting owner. Restoring the same identity onto two live bridges
  is not failover; it risks rolling-code divergence and replay rejection.
- A non-owning bridge may observe a remote but must never transmit as the
  shutter's controller.
- Receiver diversity must send no PROG or pairing frame and consume no motor
  controller slot.
- If an owning bridge is offline, another bridge may keep Home Assistant's
  estimate informed, but it cannot safely take over RF transmission without a
  separate explicit identity-transfer procedure.
- The existing single-bridge path must continue working when Home Assistant or
  another bridge is unavailable.

## Bridge-local transmission queue

One CC1101 can only transmit one packet burst at a time. The manager now queues
simultaneous MY requests, keeps every complete STOP/execute/press/release
gesture together, coalesces duplicate pending MY targets, and begins the next
one after the authenticated release plus a short radio handoff gap. This is
faster and safer than adding fixed delays in Home Assistant.

Ordinary commands are still serialized only implicitly by the ESPHome loop.
A future general queue should treat every complete logical action as one
transaction:

- extend the atomic treatment already used by MY to Venetian gestures;
- serialize commands from every managed slot;
- prioritize STOP over queued movement;
- coalesce superseded queued targets for the same shutter;
- start position timing when the RF transaction actually begins;
- acknowledge that a request was queued separately from reporting that its
  first RF frame was sent;
- enforce a small inter-transaction radio gap;
- retain each shutter's independent rolling-code stream and fail-closed NVS
  consumption.

Whole-house OPEN or CLOSE would then become a predictable short sequence rather
than simultaneous RF. Intermediate-position STOP timers would enter the same
queue, and composite tilt timing could not be stretched by another slot's
frames.

## Coordination between transmitting bridges

Separate ESPs have separate radios and can currently transmit at the same time.
Repeated 1W copies provide some tolerance, but they are not a collision-
avoidance protocol. A Home Assistant coordinator could assign each available
bridge a stable order and short offset for one automation batch. Commands on
each bridge would still use its local transaction queue.

The coordinator must not make local control depend on the network. If Home
Assistant is unavailable, each bridge should continue controlling its own
shutters; only cross-bridge staggering and receiver diversity would be absent.

## Receiver diversity

Every idle bridge already has the physical ability to listen on its calibrated
1W frequency. A future observer mode could publish normalized, receive-only
events such as:

```text
protocol version
bridge identity
physical remote identity
rolling sequence or normalized gesture identity
OPEN / CLOSE / STOP-MY / tilt direction
estimated tilt magnitude
RSSI and radio validity information
local monotonic event time
```

No installation-specific values should enter ordinary logs or public
diagnostics. The remote directory required for filtering can be distributed
privately from Home Assistant to every bridge.

Home Assistant would maintain the authoritative mapping from physical remotes
and group aliases to shutter identities. When several bridges report the same
frame burst, it would:

1. deduplicate by remote identity, rolling sequence, action and a short arrival
   window rather than wall-clock time alone;
2. retain the strongest/most complete observation for diagnostics;
3. emit one logical user action;
4. apply it once to every mapped shutter or group member;
5. forward a receive-only estimator update to the owning bridge, without
   transmitting RF or consuming a rolling code.

This final estimator service is important. Updating only the Home Assistant
entity would leave the owner's ESP position model stale and lose the corrected
state after a reload. The forwarded update must be marked as an observation so
it cannot create a loop or trigger radio transmission.

The first implementation should federate only complete normalized actions that
each bridge decoded independently. A later extension could combine partial
gesture evidence—for example, one bridge hearing a Venetian `D200` prefix and
another hearing the direction event—but that requires a carefully bounded raw-
frame correlation protocol.

## Required validation

At least two physically separated bridges are needed before claiming support.
Tests should cover:

- one physical press heard by one, two and three bridges but applied once;
- the owning bridge missing a press that a non-owner receives;
- repeated identical presses with new rolling sequences remaining distinct;
- sequence rollover, delayed network delivery and bridge reboot;
- Venetian stop-prefix correlation and large-roll magnitude;
- one group remote mapped to any number of shutters across bridges;
- owner offline and reconnect without any non-owner RF transmission;
- simultaneous OPEN/CLOSE, timed positions, STOP, MY and tilt batches;
- STOP priority and cancellation of obsolete queued targets;
- RF collision behavior and suitable inter-bridge offsets;
- preservation of controller identity and rolling-code ownership throughout.

## Suggested implementation order

1. Extend and hardware-test the MY queue as a general atomic RF transaction
   queue.
2. Define a versioned, privacy-reviewed normalized observation schema.
3. Add receive-only estimator updates to the owning bridge.
4. Deduplicate and route observations across Home Assistant config entries.
5. Add cross-bridge transmission staggering.
6. Validate with two bridges, then three, before documenting multi-bridge
   operation as supported.
