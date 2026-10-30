# Architecture — M0 contract

Status: specified, not implemented by M0. The only runtime functionality in M0
is loading the packaged synthetic DBC. This document fixes boundaries for M1
and M2; it does not claim that the simulator already satisfies them.

## One process, explicit ownership

The future simulator runs in one Python process. SimulationClock owns virtual
time and the event queue. VehicleModel owns physical state. The CAN transport
does not own either one. No services, hardware, ML, or frontend are required by
M0.

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

## Responsibilities and future interfaces

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
- **KOMBI**: decode only its received observations; schedule freshness deadlines
  independently of further frames. Reject malformed DLC/payload without
  refreshing the last valid observation.
- **Recorder (M2)**: stream frames, per-receiver deliveries, commands, and status
  changes; record fault ground truth separately. Preserve an explicit failure
  status if recording cannot continue.

Exact Python classes and scheduling implementation belong to M1. Their public
behavior must follow [DATA_CONTRACTS.md](DATA_CONTRACTS.md).

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

M2 will separate playback (display saved events) from rerun (recompute from the
manifest and command log). Neither a shared seed alone nor successful playback
proves deterministic simulation. Physical/scenario/fault RNG streams are
independent and their derivation must be recorded. In the first slice no CAN
feedback changes the physical trajectory, so changing message loss must not
change physics. Event comparison excludes run IDs and wall-clock metadata.

## Later stages

M1 implements the headless chain. M2 adds offline fault, timers, JSONL, and
rerun. M3 adds API/UI. Parquet/SQLite and ML remain later milestones. The future
detector may read only past observations, must tick on an empty stream, and
must be evaluated on both unseen faults and unfamiliar normal driving regimes.
