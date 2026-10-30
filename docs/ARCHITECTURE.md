# Architecture — M2 headless laboratory

Status: M2 adds publication-only Offline, independent freshness timers, controlled
fixed delay/seeded loss, bounded JSON scenarios and streaming trace recording,
verified playback and deterministic rerun. M1's clock, sensor boundary and
CAN-only KOMBI inputs remain intact. API/UI, ML and extended faults are not implemented.

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

## Allowed inputs

| Component | Allowed inputs | Owned output/state |
| --- | --- | --- |
| DriverInput | Validated user/scenario commands applied by core | Requested throttle |
| VehicleModel | DriverInput, fixed physics ticks, its initial state | Physical RPM and engine-running state |
| EngineSensorPort | Read-only access to the explicitly allowed RPM sensor | RPM observation for Engine ECU |
| Engine ECU | EngineSensorPort, publication ticks | Encoded EngineStatus frames |
| BusAdapter | Published frame and core-scheduled receiver delivery | Transport result; no physical state |
| KOMBI | Its received CAN observations, virtual timer callbacks | Last decoded RPM and freshness |
| Future UI | KOMBI state and explicitly selected CAN observer stream | Display only |
| Future detector | Its own observation stream, causal windows | Score and alerts |

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
periodic tick, without sending a backlog. Stopping the physical engine is a
different future command.

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
rerun. M3 adds API/UI. Parquet/SQLite and ML remain later milestones. The future
detector may read only past observations, must tick on an empty stream, and
must be evaluated on both unseen faults and unfamiliar normal driving regimes.
