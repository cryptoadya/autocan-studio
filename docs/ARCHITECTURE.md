# Architecture

AutoCAN Studio runs the deterministic simulator and the local API in one Python
process. The React dashboard consumes a bounded view of the simulation through
REST and WebSocket. The CLI can run the simulator or process saved data without
starting the API or browser.

## Time, physics, and ECU ownership

`SimulationClock` owns integer nanoseconds and orders work by time, phase, and
enqueue sequence. At a shared timestamp, commands and fault transitions precede
physics, publication, delivery, and freshness deadlines. Delivery wins when
it falls exactly on a deadline. The runner paces virtual time using wall time.
Headless execution advances the same clock directly.

`VehicleModel` owns RPM, speed, actual gear, and physical engine state.
`NetworkRuntime` connects the five ECUs through explicit ports and DBC
subscriptions. For example, Body publishes a driver's gear request, Transmission
checks the freshness of the required CAN inputs and calls a narrow gear
actuator, and VehicleModel enforces the physical interlocks. Transport faults
can therefore change the network profile's physical trajectory. The legacy
profile has no CAN feedback.

A published frame is immutable. The virtual transport uses bounded, isolated
python-can TX/RX pairs and records delivery or loss per subscribed receiver.
KOMBI owns its decoded observations and independent freshness timers. It has no
sensor access. A separate all-message receiver feeds the network monitor.
Physical telemetry such as model speed has its own display and trace fields.
See the [ports, subscriptions, and timing contracts](DATA_CONTRACTS.md#versioned-ecu-network).

## Recording, playback, and the local UI

The API owns one active session, a streaming recorder, and a bounded observation
view. Mutations and pacing are serialized on the asyncio loop. The live runner
requests 50 ms of virtual time per pacing tick. Saved-trace verification,
playback, and rerun execute separately from the active simulation.

The recorder starts at virtual time zero and separates canonical events, applied
commands, and fault ground truth into distinct streams with a shared sequence.
Playback validates and projects saved events without running physics. Rerun
replays applied commands into a fresh simulation and compares canonical events
and final state. Run IDs and wall-clock timestamps do not affect the comparison.

Each WebSocket connection starts with a snapshot and continues with ordered
deltas. Reconnection replaces the client's rolling history. The monitor keeps
200 deliveries and renders up to 50 rows. The RPM chart retains up to 400 points.
Display skips are reported separately from recording failures. The launcher
binds to loopback and uses local host/origin checks. It is designed for
single-user operation without authentication. See the
[REST and WebSocket contracts](DATA_CONTRACTS.md#local-api-and-live-display).

## Offline data path

A verified recording can be exported as normalized Parquet or projected into
the observations of one receiver. Dataset Factory composes that flow across
seeded drives and preserves the lineage back to each scenario and recording.
Only observable CAN fields and receiver time feed the feature pipeline. The
separate ground-truth stream supplies labels and evaluation intervals. Features
close on virtual-time boundaries even when no frames arrive. See
[trace and dataset formats](DATA_FORMATS.md) and [ML evaluation](ML_EVALUATION.md).
