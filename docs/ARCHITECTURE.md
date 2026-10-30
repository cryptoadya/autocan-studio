# Architecture — local laboratory

Status: M5 extends the deterministic core and local FastAPI/React dashboard with
five ECU, eight messages, vehicle controls and normal urban drive. The legacy
profile remains available. Extended faults, datasets and ML remain later milestones.

## One process, explicit ownership

The headless simulator runs in one Python process. SimulationClock owns virtual
time and the event queue. VehicleModel owns physical state. The CAN transport
does not own either one. No services, hardware, ML, or frontend are required by
the headless milestones.

```text
DriverInput -> VehicleModel -> EngineSensorPort -> Engine ECU
                                                    |
                                               DBC encoder
                                                    |
                                         immutable CAN Frame
                                                    |
                                   core-scheduled delivery via BusAdapter
                                                    |
                                      DeliveryEvent(receiver=KOMBI)
                                                    |
                                          CAN observation + DBC decode
                                                    |
                                             KOMBI state -> UI
```

KOMBI is the name of a synthetic instrument cluster, not an OEM implementation.

## M5 network ownership

`NetworkRuntime` connects five ECU through narrow sensor/actuator ports, routes
DBC subscriptions, and schedules publication, delivery and deadlines on the same
clock. Each ECU receives only `CanObservation` and owns independent message caches
and timers. A separate observer subscribes to every message for the monitor.
The [port and subscription tables](DATA_CONTRACTS.md#m5-versioned-ecu-network)
define the network contract.

Body publishes driver gear requests with sequence numbers. Transmission receives
RPM, wheels, brake/throttle/request and Body state through CAN, validates a new
request, and calls its narrow gear actuator. VehicleModel owns physical gear and
retains stationary/brake interlocks. Rejections are explicit recorded results.
Network loss/delay can change gear application and the physical trajectory;
the legacy profile has no CAN feedback.

Engine measures RPM/throttle/coolant/engine-on. ABS/DSC measures four wheel speeds
and observes driver/Body messages. Body observes Engine/KOMBI presence; Engine
observes Body/KOMBI presence. KOMBI publishes received link states in a heartbeat.
These are synthetic ECU, not production ABS or automatic transmission algorithms.

The routed adapter uses an isolated python-can TX/RX pair per subscriber, with
capacity preflight before publication. Unsubscribed traffic never fills a queue.
One logical frame has distinct per-receiver delivery/drop records; wire
arbitration and physical bandwidth are not simulated.

## Legacy profile allowed inputs

M5 engine-control pass extends only the physical engine controls. VehicleModel
owns ignition and engine-running state; DriverInput applies validated ignition
and Start/Stop requests at the canonical input phase. Engine ECU still reads only
RPM, and KOMBI still reads only its CAN observations. UI labels ignition/physical
engine state as control/model state, separately from received KOMBI RPM.
The network profile extends these ports as described above.

| Component | Allowed inputs | Owned output/state |
| --- | --- | --- |
| DriverInput | Validated user/scenario commands applied by core | Requested throttle, brake, gear, ignition, physical Start/Stop |
| VehicleModel | DriverInput, fixed physics ticks, its initial state | Physical RPM/speed, actual gear, ignition and engine-running state |
| EngineSensorPort | Read-only access to the explicitly allowed RPM sensor | RPM observation for Engine ECU |
| Engine ECU | EngineSensorPort, publication ticks | Encoded EngineStatus frames |
| BusAdapter | Published frame and core-scheduled receiver delivery | Transport result; no physical state |
| KOMBI | Its received CAN observations, virtual timer callbacks | Last decoded RPM and freshness |
| UI | KOMBI state and explicitly selected CAN observer stream | Display only |
| Future detector | Its own observation stream, causal windows | Score and alerts |

The motion panel is a separate model-telemetry display. PhysicalMotionUpdated
events record signed model speed for playback; they are not CAN observations,
do not refresh KOMBI and must not be treated as future detector input. Gear
requests are validated at their application time against physical speed/brake;
a rejected request is recorded with its reason rather than raising a clock
callback error. Actual physical gear remains owned by VehicleModel. The separate
Transmission/ABS/Body ECU network is isolated in the versioned network profile.

Components must receive narrow interfaces rather than the entire simulator or
VehicleModel. Engine ECU may read RPM through its sensor port. KOMBI cannot
read VehicleModel, EngineSensorPort, Engine ECU internals, or injector flags.
KOMBI's decoded observation, not physical truth, drives the RPM dashboard.
CAN ID can identify a message according to the profile; internal sender metadata
is not an additional sensor. Future ECU connections must extend this table
before implementation.

## Responsibilities and milestone boundaries

- **SimulationClock**: integer `now_ns`, stable event scheduling, step/advance,
  pause, and reset. No wall-clock calculations inside the deterministic core.
- **VehicleModel**: update physics on a fixed tick using the applied input.
- **EngineSensorPort**: read the physical RPM only; no mutation or full state.
- **Engine ECU / scheduler**: sample the sensor on each publication tick and
  encode with the declared DBC. A failed encode produces no frame and an
  explicit error. Do not silently replace a rejected value.
- **BusAdapter**: transport immutable frames using a run-specific virtual bus
  channel and bounded queues. Keep canonical integer times outside python-can's
  floating timestamp. Set `preserve_timestamps=True`; drain transport under
  core control. Queue overflow is a visible run error, not a simulated fault.
- **KOMBI**: decode only its received observations. The receiver rejects malformed
  DLC/payload and out-of-range RPM without refreshing the last valid observation.
  The injected virtual clock schedules freshness checks independently of frames.
  Old deadlines check the current last valid reception before changing status.
- **Recorder (M2)**: stream frames, per-receiver deliveries, commands, and status
  changes; record fault ground truth separately. Preserve an explicit failure
  status if recording cannot continue.

Implemented modules are `clock`, `vehicle`, `events`, `ecu`, `bus`, `simulation`,
`scenarios`, `validation`, `traces` and `cli` under `backend/autocan`. Simulation composes these through a narrow
RPM sensor port and a BusAdapter protocol. Tests use actual VirtualBus transport
without hardware, wall-clock delays, or threads. Clock and physical-model tests
need no I/O. Public behavior follows [DATA_CONTRACTS.md](DATA_CONTRACTS.md).

## Offline ECU and physical engine

In the first slice, `ecu_offline` only suppresses subsequent Engine ECU
publications. It does not stop the physical engine or alter VehicleModel.
Previously published frames keep their scheduled deliveries. KOMBI eventually
becomes STALE/TIMEOUT after its last valid delivery; the injector never sets
KOMBI's status directly. On re-enable, publication resumes at the next original
periodic tick, without sending a backlog. M5's physical Stop is a separate
command: the engine coasts to zero and Engine ECU continues publishing fresh
measurements. The UI labels physical engine/control state explicitly; received
RPM and freshness remain exclusively CAN-driven.

## Time and replay boundaries

Only the runner uses wall-clock pacing. Pause freezes virtual time; speed
changes pacing, not physics or event order. Transport threads/Notifier and
`sleep` cannot drive virtual timers. At equal virtual time, delivery precedes
freshness checks, including a frame arriving exactly at a deadline.

M2 separates playback (display saved events) from rerun (recompute from the
manifest and command log). Neither a shared seed alone nor successful playback
proves deterministic simulation. Physical/scenario/fault RNG streams are
independent and their derivation must be recorded. In the first slice no CAN
feedback changes the physical trajectory, so changing message loss must not
change physics. Event comparison excludes run IDs and wall-clock metadata.

Only delivery loss currently draws random values. Physics and scenario stream
seeds are independently derived and reserved; unused randomness is not invented
to make the seed meaningful. The raw VirtualBus carries frames FIFO; M2 supports
fixed delay, not per-frame jitter/reordering.

The recorder has three disjoint event streams with a single global `event_seq`.
Playback merges them in sequence after validating the whole recording. The
future feature pipeline must use the selected receiver's observations, excluding
commands and fault truth. Rerun submits recorded applied commands in time groups,
then advances the core, avoiding an unbounded queue of future commands.

## Later stages

M1 provides the headless chain; M2 implements offline fault, timers, JSONL and
rerun. M3 implements API/UI. Parquet/SQLite and ML remain later milestones. The future
detector may read only past observations, must tick on an empty stream, and
must be evaluated on both unseen faults and unfamiliar normal driving regimes.

## M3 runner and UI

`api.Session` owns one core simulation, a TraceRecorder and a bounded observation
view. FastAPI mutations and the pacing coroutine run serially on the same asyncio
loop. Every 50 ms of wall time the runner requests another 50,000,000 ns from the
core; the deterministic core remains unaware of wall time. Pause advances nothing.
No catch-up bursts, extra transport threads or independent ECU timers are added.
Expensive saved-trace verification/playback/rerun uses worker threads and separate
core instances, never the active live simulation.

Recordings begin before the first event. Stop finalizes the full run; reset saves
it, closes transport and allocates a new run ID/directory. Shutdown finalizes active
runs when possible. Duration exhaustion saves; other failures leave a failed trace
that playback/rerun rejects. Runs are never implicitly overwritten or deleted.

The live dashboard gets `kombi_rpm` and `kombi_status` from Simulation's KOMBI state.
`physical_rpm` and physical coolant are excluded from the API view. The network
monitor uses observer deliveries; KOMBI fields use only KOMBI deliveries. Live
and saved views share the pure CAN projection. Playback streams validated raw events through a separate DBC observation
view, changes measured RPM only on delivery, and applies recorded status changes.
It does not instantiate a VehicleModel. Its 50 ms graph sampling also runs across
silent intervals, retaining at most the last 400 points.

A WebSocket starts with a complete snapshot and then ordered bounded deltas. Each
connection has its own `seq` starting at 1. Reconnect replaces local history with
a snapshot; gaps or schema mismatch force reconnect. The frontend keeps 200 frames
and 400 points, with at most 50 table rows; Freeze retains one bounded copy. Slow
socket sends time out, allowing a new snapshot rather than accumulating messages.
Display loss is indicated separately from the canonical recording, which always
streams every core event or stops explicitly on failure.

The launcher binds 127.0.0.1 and serves the built frontend on the API origin.
Trusted local hosts and exact Origin checks protect HTTP and WS access; request
bodies, connections, commands, duration, run count and recording bytes are bounded.
This is a single-user local tool with no authentication or remote deployment mode.
