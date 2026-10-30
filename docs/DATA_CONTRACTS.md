# Data and timing contracts — DemoCar 0.1.0

M0 implements and tests the DBC section. M1 implements the clock, throttle/RPM
model, frames, per-receiver delivery, applied throttle commands, and event
envelope. Offline commands, freshness deadlines and recording/rerun belong to
M2. Schema version is `1`; profile version is
`democar-0.1.0`. Times are nonnegative integer nanoseconds, never floats.

## Initial conditions and timing

| Parameter | Default |
| --- | --- |
| Virtual time | 0 ns |
| Physical engine | Running |
| Physical RPM | 800 rpm |
| Requested throttle | 0%, finite range [0, 100] |
| Engine ECU publishing | Enabled |
| Physics tick | 10,000,000 ns (10 ms) |
| EngineStatus period | 20,000,000 ns (20 ms) |
| First publication | 0 ns, using initial physical state |
| Base delivery delay | 0 ns, separately applied per receiver |
| STALE age | 60,000,000 ns (60 ms) |
| TIMEOUT age | 200,000,000 ns (200 ms) |
| Encodable RPM bounds | [0, 8000] rpm |
| Model idle / full-throttle target | 800 / 6000 rpm |
| Model slew rate | 2000 rpm per second |
| Seed | 0 unless explicitly provided |

The first physics integration advances [0, 10 ms] at 10 ms. There is no phantom
integration step before the initial frame. Parameters are synthetic defaults,
not real ECU timings. Period must be positive and `0 < stale_age < timeout_age`.
Changes to run parameters require a new recorded configuration, not an
unlogged mutation.

The M1 model uses `target = idle + (maximum - idle) * throttle / 100` and moves
toward it by at most `slew * delta_ns / 1e9` per physics tick, without overshoot.
The engine remains physically running; engine start/stop is a later control.
The model is noise-free. Seed is accepted and reported, but no random stream is
consumed until a feature actually needs randomness.

## Stable scheduling and commands

Queue order is `(sim_time_ns, priority, enqueue_seq)`, with an increasing
per-run sequence to break ties. Equal-time priority order is:

1. Applied user/scenario commands and fault transitions.
2. Physics integration tick.
3. Frame publication.
4. Per-receiver frame delivery.
5. Freshness deadlines and future feature window closure.
6. State snapshot.

A callback cannot schedule an earlier priority phase at its own timestamp or
an event in the past. A command arriving after a phase has executed is assigned
the next valid application boundary, not retroactively inserted. Commands
received while paused are queued for the next valid boundary upon step/resume;
pause/resume/reset are runner controls and must not depend on a ticking clock.
The command log records canonical application time and sequence, independent
of network arrival time. Replay uses these applied values.

An applied input record carries `command_seq`, `applied_at_ns`, `command_kind`,
and validated parameters. In the first slice the domain commands are
`set_throttle(percent)` and `set_engine_publishing(enabled)`; offline intervals
use [start, end), with enable/disable applied in the command phase. Invalid
commands fail visibly without modifying simulation state. Duplicate commands
at the same time use their logged order.

M1 implements only `set_throttle`. Explicit application times must align with
the 10 ms physics grid; past or already completed earlier phases are rejected.
If no time is specified, the core chooses the next valid input boundary and
returns it. This prevents a mid-tick command from rewriting part of the already
integrated physical interval. M1 exposes applied commands as events to an
optional observer; a persisted command log is an M2 feature.

The event queue is bounded (1024 pending events by default). Callback failures
stop advancement until reset; reentrant advancement is rejected. Pause does
not prevent controls or explicit `step_to` from operating. Simulation's `step`
advances a 10 ms interval; reset rebuilds the model, ECU state, transport and
counters and clears pending commands. Previous commands must be resubmitted.

## Immutable frame and per-receiver delivery

**Frame** has `frame_id`, `source_ecu`, `channel`, `can_id`, `is_extended_id`,
`dlc`, immutable `payload: bytes`, and `created_at_ns`. `frame_id` is a positive,
monotonically allocated integer inside its run. Creation and publication are
one phase in v0.1; there is no separate `sent_at_ns`. Frame contents cannot be
changed by delivery or a downstream consumer.

**DeliveryEvent** has `frame_id`, `receiver_id`, and `received_at_ns`, plus its
event envelope. One frame can be delivered at different times to different
receivers. Delivery must not precede creation. In the first slice, each
scheduled receiver delivery occurs at most once; a dropped delivery produces
no received observation.

The event envelope is `run_id`, positive per-run `event_seq`, `sim_time_ns`,
`event_kind`, `schema_version`, and `profile_version`. `event_seq` is assigned
in execution order, distinct from the queue's `enqueue_seq`. For
`FramePublished`, event time equals `created_at_ns`; for `FrameReceived`, it
equals `received_at_ns`. Other event kinds include `EcuStatusChanged`,
`InputApplied`, `FaultActivated`, and `FaultCleared`.

Classic CAN constraints: integer ID [0, 2047], `is_extended_id=False`,
`0 <= dlc <= 8`, `len(payload) == dlc`. JSONL represents IDs as integers and
payload as lowercase hex without `0x`, exactly `2 * dlc` characters. No CAN FD,
remote frames, bit arbitration, CRC/ACK simulation, or OEM routing is implied.

## Observation boundary

A receiver sees only its CAN ID, flags, channel, DLC/payload, and its virtual
receive time. Internal `source_ecu`, frame creation time, scheduled deliveries,
injector status, physical truth, and fault labels are not supplied as hidden
inputs. The recorder can correlate observations with internal events through
frame IDs; the future feature pipeline cannot use these IDs as features.

## KOMBI freshness

Before the first valid EngineStatus, RPM is unavailable (`null`) with status
`UNAVAILABLE`. Never fabricate a measured 0 rpm from initial physical state.
After a valid frame with receive time `r`:

- `FRESH` while `now_ns - r < 60,000,000`;
- `STALE` while `60,000,000 <= now_ns - r < 200,000,000`;
- `TIMEOUT` when `now_ns - r >= 200,000,000`.

Keep the last valid RPM and its receive time for inspection, with its status;
it must not appear to be fresh or be replaced with a zero. A valid subsequent
delivery restores FRESH. Older scheduled deadline callbacks must check the
current last receive time and cannot invalidate a newer observation. Because
delivery precedes deadlines at equal time, a valid frame arriving exactly at
the threshold restores freshness before a stale/timeout transition. Timers
must execute when the entire bus is silent. Invalid data does not refresh RPM.

## DBC wire contract (implemented in M0)

Canonical file: `backend/autocan/profiles/democar.dbc`, packaged with the Python
module. `load_democar_database()` returns a fresh, strict cantools CAN database;
it never depends on the working directory or mutates a shared cached database.

| Field | Value |
| --- | --- |
| Message | EngineStatus |
| CAN ID | 0x200 (512), synthetic choice, no OEM mapping |
| Sender / receiver | Engine / KOMBI |
| DLC | 2 bytes |
| Publication period attribute | 20 ms |
| Signal | EngineRpm |
| Start / width | Bit 0 / 16 bits |
| Byte order / signedness | Little-endian / unsigned |
| Factor / offset | 0.25 / 0 |
| Unit / allowed physical range | rpm / [0, 8000] |

Encode physical values with `strict=True`. Range validation happens before
quantization: even 8000.01 rpm is rejected. Finite, in-range values are rounded
to the nearest raw integer with ties to even (0.25 rpm resolution). For example,
800.125 becomes 800.0; 800.375 becomes 800.5. Sensor data outside the declared
range, missing signals, or extra misspelled signal names cause an explicit
encoding error; do not clamp or wrap them silently. Nonfinite sensor values
are invalid and must produce an explicit failure, never a valid frame.

For receiver decoding, require the message's declared DLC and disable
truncated/excess payload acceptance. Decode does not itself establish physical
plausibility: a decoded EngineRpm outside [0, 8000] must be rejected by future
receiver validation before refreshing KOMBI. Physically implausible but
representable values (for example 7500 rpm at idle throttle) remain valid wire
data for later fault scenarios. Raw corruption is a separate mechanism.

| RPM | Raw integer | Payload hex |
| --- | --- | --- |
| 0 | 0 | 0000 |
| 800 | 3200 | 800c |
| 1234.25 | 4937 | 4913 |
| 3000 | 12000 | e02e |
| 8000 | 32000 | 007d |

## Reproduction contract (M2)

A manifest records schema/profile/core versions, package versions, git commit
when available, DBC/config hashes, initial physical/ECU state, seed/RNG stream
derivation, applied command/fault log, and artifact checksums. Reset restores
initial state, clock, queues, counters, and RNG streams; it starts a new run
without overwriting an existing recording.

Playback reads saved events without physics. Rerun computes a new stream and
compares canonical virtual times, order, frames, deliveries, and state changes.
Exclude new run IDs and wall-clock timestamps; preserve all behaviorally
relevant fields. Artifact checksums check bytes; semantic hashes check canonical
content. Future transport and physics randomness use independent streams.
