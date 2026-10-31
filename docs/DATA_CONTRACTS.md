# Data and timing contracts — DemoCar 0.1.0 / 0.2.0

M0 implements and tests the DBC section. M1 implements the clock, throttle/RPM
model, frames, per-receiver delivery, applied throttle commands, and event
envelope. M2 implements Offline, freshness deadlines and recording/rerun.
Schema version is `1`; profile version is
`democar-0.1.0` for legacy and `democar-0.2.0` for the M5 network. Times are
nonnegative integer nanoseconds, never floats.

## Initial conditions and timing

### M5 versioned ECU network

The original `democar` / `democar-0.1.0` profile stays available for legacy
callers and recordings. `democar-network` / `democar-0.2.0` extends the synthetic
network with five ECU and a separate CAN observer. Both profiles use the same
physical model, integer clock and immutable frame/per-receiver delivery contracts.

| Publisher | Message / ID | Period | Subscribers, excluding the observer |
| --- | --- | --- | --- |
| Engine | EngineStatus / 0x200 | 20 ms | KOMBI, Transmission |
| Engine | EngineTelemetry / 0x201 | 100 ms | KOMBI, Transmission, Body |
| ABS/DSC | WheelSpeeds / 0x210 | 20 ms | KOMBI, Transmission |
| ABS/DSC | AbsStatus / 0x211 | 100 ms | KOMBI |
| Transmission | TransmissionState / 0x220 | 100 ms | KOMBI |
| Body | BodyState / 0x230 | 100 ms | KOMBI, Engine, ABS/DSC, Transmission |
| Body | DriverInput / 0x231 | 20 ms | ABS/DSC, Transmission |
| KOMBI | ClusterHeartbeat / 0x240 | 100 ms | Engine, Body |

Every message is also delivered independently to `observer`. It has no physical
sensor input. ECU ports and subscriptions are explicit:

| ECU | Permitted physical/control port | CAN-only inputs and owned derived state |
| --- | --- | --- |
| Engine | RPM, throttle, coolant temperature, engine-on | Body/cluster link freshness |
| ABS/DSC | Four wheel speeds | Driver brake, Body presence; wheel consistency and input availability |
| Body | Ignition and driver throttle/brake/gear requests | Engine/cluster presence |
| Transmission | Validated physical gear actuator only | RPM, wheel speed, driver request/brake/throttle, Body; shift FSM |
| KOMBI | None | RPM, signed speed, temperature, gear, ignition, consistency and per-message freshness |

Network gear commands are queued driver requests, published by Body and applied
by Transmission only after CAN reception. Fresh required inputs and the physical
stationary/brake interlocks govern P/R/D; N remains freely selectable. Rejections
are explicit results and do not stop the clock. Loss/delay may therefore change
gear application and the physical trajectory in this profile; no such feedback
is introduced into the legacy profile.

Network `InputApplied(set_gear)` records `request_sequence`; `GearShiftEvaluated`
records that sequence, requested/actual gear, acceptance and nullable reason
(`vehicle_moving`, `brake_required`, `inputs_unavailable`). Rejected requests must
be resubmitted with a new sequence; they are not silently retried.
`set_ecu_publishing` requires a known `ecu_id` and boolean `enabled`.

Each subscribed message becomes STALE after 3 periods and TIMEOUT after 10,
independently of other bus traffic. Never-received messages stay UNAVAILABLE
until their initial timeout. Delivery at a deadline wins over that deadline.
Publication-only Offline can target each ECU; Engine Offline suppresses both
its messages while physical RPM/temperature continue. It does not stop ECU RX.
Wheel speeds are equal signed speeds in this straight-line/no-slip model;
coolant warms from 20 toward 90 °C at 0.2 °C/s when running and cools toward
20 °C at 0.05 °C/s when stopped. These are synthetic demonstration parameters.

### M5 motion-control extension

VehicleModel owns signed physical speed (forward positive, reverse negative),
actual P/R/N/D selection and brake input. Initial state is P, 0 km/h and 0% brake;
the original parked throttle/RPM behavior remains unchanged. Default drive
acceleration is 3 m/s² times throttle, brake deceleration is 8 m/s² times brake,
and rolling resistance is 0.15 m/s² opposing movement. N coasts without drive;
P holds still. Braking/drag cannot reverse motion. Forward/reverse limits are
180/30 km/h. A stopped engine supplies no drive; it can still coast and brake.

Changing to P, R or D requires speed magnitude <=0.5 km/h and brake >=10%; N is
always selectable. Repeating the current gear is idempotent. Accepted P/R/D
selection settles the stationary residual speed to zero. Rejected shifts leave
gear/speed unchanged, log an explicit result and do not stop simulation time.
Running RPM in D/R targets idle + |speed km/h| × 40/80 + throttle × 8, capped at
the existing maximum and approached with the existing slew rate. These are
synthetic coefficients, not an automatic gearbox or a physical torque model.

`set_brake(percent)` and `set_gear(gear)` use the canonical input grid. Gear
InputApplied records in the legacy profile contain `accepted` and nullable `reason`.
`PhysicalMotionUpdated` records `physical_speed_kph` when it changes; it is model
telemetry, not a CAN observation. The dashboard labels this speed separately,
while received KOMBI RPM still comes only from CAN. Playback reads recorded
speed samples without computing physics. The manifest records motion settings
and initial physical state. The current RPM DBC/profile remains unchanged.

| Added control/state | Owner | Permitted access |
| --- | --- | --- |
| Brake and requested gear | DriverInput | VehicleModel through queued validated commands; explicit applied-result log |
| Actual gear and physical speed | VehicleModel | Physical-model display/trace only in this pass; no direct access by KOMBI |
| Running RPM coupled to movement | VehicleModel | Existing permitted Engine RPM sensor -> EngineStatus -> KOMBI |

### M5 engine-control extension

Initial ignition is ON with the physical engine running at 800 rpm. Supported
ignition requests are OFF/ACC/ON/START: OFF and ACC stop the physical engine;
ON supplies ignition without starting; START starts immediately and returns ON
(synthetic behavior, with no starter/cranking model). Explicit Start engine also
sets ignition ON; Stop engine leaves ignition unchanged. Requests are idempotent.
With the engine stopped, physical RPM moves toward zero at the existing 2000
rpm/s slew rate, independently of throttle. This does not disable Engine ECU:
its RPM publications continue and zero RPM is a valid FRESH CAN measurement.

`set_engine_running` uses boolean `enabled`; `set_ignition` uses string `state`.
Both follow the existing input grid, event ordering and applied-command log.
Unknown states, wrong types or extra fields are rejected. Reset restores the
original running-engine state. The RPM DBC and trace envelope remain unchanged;
new command kinds extend schema 1, and older recordings remain playable. Rerun
still requires an exact compatible core hash/runtime rather than rewriting data.

| Added control/state | Owner | Permitted access |
| --- | --- | --- |
| Ignition request / current ignition | DriverInput / VehicleModel | Control UI, applied-command playback; not a CAN measurement |
| Physical Start/Stop / engine-running | DriverInput / VehicleModel | Explicitly labelled physical engine state; Engine ECU continues to measure RPM only |
| Zero or coasting RPM after Stop | VehicleModel | Engine sensor -> existing EngineStatus -> KOMBI; no direct physical RPM in UI |

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
M5 adds the explicit engine/ignition controls described above; absent these
and motion commands, the original parked throttle trajectory remains unchanged.
The model is noise-free. Seed is accepted and reported, but no random stream is
consumed by physics. M2 uses `sha256("autocan-rng-v1:{seed}:{stream}")` to derive
independent `physics`, `scenario` and `delivery` seeds. Only delivery loss uses
Python Random, seeded with its derived integer. Fixed delay draws no randomness;
the initial seeds and derivation algorithm are stored in each manifest.

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

The runtime supports `set_throttle` and `set_engine_publishing`. Explicit application times must align with
the 10 ms physics grid; past or already completed earlier phases are rejected.
If no time is specified, the core chooses the next valid input boundary and
returns it. This prevents a mid-tick command from rewriting part of the already
integrated physical interval. Applied commands are events to an optional observer;
M2 persists them separately from frame observations.

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

## Reproduction contract (implemented in M2)

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

### Scenario and transport bounds

Scenarios use JSON schema version 1: `duration_ns`, optional nonnegative `seed`,
`commands`, and optional `transport` (`delay_ns`, `drop_probability` in [0, 1]).
Each command has `at_ns`, `kind`, and either `percent` or boolean `enabled`.
Unknown fields/kinds, nonfinite values, booleans used as integers, duplicate JSON
keys, commands after the end time and non-grid command times are rejected.
Scenario files are capped at 64 KiB / 256 commands. No executable expressions.

Offline emits InputApplied and FaultActivated; enable emits FaultCleared for
the same deterministic fault ID. Repeated identical enable/disable commands are
logged without restarting a fault interval. An active interval at run end is
bounded by manifest `end_time_ns`, without pretending it was explicitly cleared.
Default receipt delay is zero; configured delay is fixed and applies to every
nondropped frame. A drop produces FrameDropped ground truth after FramePublished.
The bus holds at most 256 pending frames per receiver. The importer permits one
additional provisional publication while determining whether it was dropped.

### Run directory, integrity and lifecycle

Format version 1 contains `events.jsonl`, `commands.jsonl`, `faults.jsonl`,
`profile.dbc`, and `manifest.json`. The first three streams partition canonical
events: InputApplied goes to commands, FaultActivated/FaultCleared/FrameDropped
to faults, and frame/delivery/status events to events. Sequences are increasing
per file and contiguous after merging; event time is monotonic. Each receiver
delivery/drop references an already published frame. Data is streamed with
bounded pending-reference validation, rather than loading a whole run in RAM.

The manifest records format/schema/core/profile versions, core source hash,
profile snapshot hash, exact runtime package/Python versions, Git commit/dirty
state when available, configuration including RNG derivation, initial/final
state, virtual end time, per-artifact bytes/count/checksum and the semantic hash.
The configuration checksum also covers initial state. Artifact checksums cover
file bytes; semantic hashing uses sorted compact JSON plus newline, omitting
only run identity. Global event order includes commands and fault markers.

Directory creation is exclusive. Manifest status begins `recording`, becomes
`complete` only after finalization, and becomes `failed` on a caught recording
failure. A process interruption leaves `recording`, which is rejected as
incomplete. The recorder checks run identity/event count to reject reset during
a recording; existing runs are never overwritten. Artifact bytes are capped at
16 MiB (configurable downward); manifest/JSONL line caps are 64 KiB. Import uses
fixed artifact names, rejects symlinks and validates sizes, checksums, schema,
ordering and semantic hash before playback yields any events.

Playback does not instantiate Simulation and tolerates a different current core.
Rerun requires matching core/profile hashes and runtime versions, replays the
applied command stream without the original scenario, and compares regenerated
canonical event hash and final state. Final pause state is restored without
introducing wall-clock input. Ending with delayed frames still in flight is
valid: their scheduled future receptions are outside that recorded time range.

## M3 local API and live display

The recording and API/WS envelope schema remains `1`; new recordings use core
`m5-network-1` and the selected profile version. The API exposes both DemoCar
profiles, throttle/brake/gear, ignition/physical
engine controls and the Offline publication fault. ML and profile editing are
not exposed.

| Endpoint | Behavior |
| --- | --- |
| `GET /api/health`, `/api/profiles` | Local readiness and DemoCar identity |
| `POST /api/simulations` | `{profile_id: "democar-network", seed: 42}`; fresh paused recording; one active run; omitted profile defaults to legacy `democar` |
| `GET /api/simulations/current` | Current snapshot or `null`, for page reload |
| `GET /api/simulations/{id}/state` | Current display snapshot, excluding physical RPM |
| `POST /api/simulations/{id}/start`, `/pause`, `/stop`, `/reset` | Resume, pause, finalize, or finalize and allocate a fresh run |
| `PATCH /api/simulations/{id}/inputs` | Nonempty subset of `{throttle: 0, brake: 100, gear: "D", ignition: "ON", engine_running: true}`; validated controls, returns canonical `applied_at_ns` |
| `POST /api/simulations/{id}/faults` | `{offline: true, ecu_id: "abs"}`; publication only, returns canonical `applied_at_ns`; omitted ECU targets Engine |
| `GET /api/runs` | Up to 64 complete, verified server-style UUID recordings |
| `GET /api/runs/{id}/playback?until_ns=N` | Bounded display snapshot through N (default: recorded end); verified saved events only |
| `POST /api/runs/{id}/rerun` | Fresh computation using M2 compatibility/integrity checks |

Payloads reject unknown fields, boolean seeds/throttle and non-boolean Offline.
Inputs are queued, not applied by HTTP receipt; a paused run applies them after
resume. Explicit null inputs and empty requests are rejected. A multi-field
batch applies throttle, brake, gear, ignition, then physical engine control at the same input
boundary; the entire command quota is reserved before any part is queued. Each
field counts as one applied command. Stop finalizes only applied commands. A never-started run processes its
first 10 ms step before saving so the recording contains initial events. Reset
retains seed/profile, restores initial conditions and returns a new run identity.

Live and saved display state additionally includes `brake_percent`, actual `gear`,
nullable `shift_rejection`, and signed `physical_speed_kph`. The last field is
explicitly physical model telemetry, never a KOMBI/CAN speed measurement. Model
speed is integrated in m/s rounded to 12 decimal places; canonical km/h samples
are rounded to 9 places. Playback consumes only saved samples and command results.
Older recordings without motion commands retain their original stationary P
state. New recordings use core version `m6-faults-1` and store the motion model
version/settings in their manifest; old-core rerun still requires the old core.
Saved-run identifiers are UUID hex strings, not filesystem paths. Failed or
incomplete recordings are omitted from the saved-run selector; they still consume
local disk capacity and are never deleted automatically.

`/ws/simulations/{id}` sends `snapshot` followed by `frames_batch`, each including
`schema_version`, `seq`, `sim_time_ns`, `id`, `mode`, `state`, `frames`, `signals`,
`recording`, `stopped`, `error`, and `skipped_frames`. A snapshot contains the full
bounded view; subsequent batches contain only newer delivered frames and sampled
points plus current state. `seq` restarts at 1 per connection, while frame records
retain their canonical publication `event_seq` and `frame_id`. Frame display time
is receiver delivery time, not frame creation time. The `rpm` column is decoded
from the delivered payload (nullable for non-RPM messages). Network rows also
contain `message_name`, `decoded`, `receiver_id` and contiguous `display_seq` for
observer deliveries. Publication ID gaps caused by transport loss do not count
as live display skips. Network state adds CAN speed, temperature, gear, ignition,
consistency and independent freshness statuses, plus publication flags/counters.
These flags never supply availability to the ECU.
Signals contain virtual time, last received RPM and
freshness status; stale values are drawn with dashed lines.

There is no per-client event backlog. At most 8 WS clients are allowed; a send
blocked for 1 s disconnects. A client must replace its view with the reconnect
snapshot. The server reports any skipped display deliveries; it does not drop
canonical recording events. Commands/fault ground truth remain in their separate
M2 artifacts. A recording quota failure stops the run rather than losing events.

API sessions are limited to 120 virtual seconds, 256 commands and the existing
16 MiB trace quota. Creation stops at 64 entries under the configured run root.
HTTP POST/PATCH bodies are capped at 4096 bytes. The source launcher bounds
concurrent connections to 32 and incoming WS messages/queues to 4096 bytes / 8.
There is no arbitrary upload, export format, scenario editor or remote API in M3.

## M6 fault commands and timeline

The network profile accepts validated `inject_fault` and `clear_fault` core,
CLI and API/UI commands. They use the same input phase, canonical time/order and separate
commands/ground-truth artifacts as existing controls. Publication, encoding,
delivery, overlap/clear, RNG and compatibility rules are specified in
[fault contracts](FAULTS.md). M5 publication requests remain compatible.

Creation optionally selects a whitelisted built-in `scenario_id`; the session
reserves its commands, uses its transport and completes at its exact duration.
The manifest may include validated scenario provenance; actual applied commands
and end time remain authoritative for rerun and early stop.

Live/WS/playback projections add a labelled demo `timeline` (latest 100 events),
`timeline_truncated`, `active_faults` (up to 32 independent active library episodes)
and optional scenario metadata. Each WS update replaces these small projections.
Timeline samples are bounded by the playback position and never enter CAN payloads.
Observer `display_seq` determines WS deltas even when delay reorders frame IDs.
Displayed network frames include `created_at_ns` for the synthetic delay column;
it remains internal provenance, excluded from the allowed future detector inputs.

## M7 Parquet exchange and observer datasets

The additive headless formats are specified in [Parquet storage](PARQUET.md).
Normalized frame publications and per-receiver deliveries preserve the JSONL
canonical sequence; hidden simulator provenance is excluded from the selected
receiver's observation table. Fault truth is stored separately. Existing JSONL
version 1, playback and exact-version rerun contracts remain unchanged.

The [Dataset Factory](DATASET_FACTORY.md) composes these formats into a bounded
collection of seeded driving runs. Its root manifest binds each saved scenario,
source trace, Parquet bundle and selected-receiver dataset through a physical
inventory and a logical collection hash. Run IDs and physical container bytes
are excluded from that logical hash; hidden recipe/physics/fault metadata remain
outside the observer input columns. No feature/window labels or splits are added.
