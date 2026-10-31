# M6 fault library — first pass

This pass provides headless fault injection for `democar-network`, strict JSON
scenarios, interactive CLI parameters, applied-command recording and deterministic
rerun. The dashboard still has the M5 publication controls; scenario selection,
fault parameters and a fault timeline in the API/UI are the next M6 pass. M6 is
not yet complete. No dataset, ML detector or raw-payload corruption is included.

## Run three synthetic demonstrations

```sh
uv run --locked autocan run scenarios/abs_missing_frames.json --output data/abs-missing
uv run --locked autocan run scenarios/rpm_spike.json --output data/rpm-spike
uv run --locked autocan run scenarios/timing_jitter.json --output data/timing-jitter
uv run --locked autocan playback data/rpm-spike
uv run --locked autocan rerun data/rpm-spike
```

Each output directory must be new. The existing 16 MiB recording quota applies.
Playback verifies the saved artifacts and displays their merged canonical events;
rerun uses the manifest and applied commands without the original scenario file.

- **ABS missing frames:** the car selects D and accelerates. ABS publication
  stops during `[200, 500)` ms while physics and other ECU continue. The last
  WheelSpeeds delivery is at 180 ms; KOMBI becomes STALE at 240 ms, TIMEOUT at
  380 ms and FRESH at 500 ms. This tests received-data availability.
- **RPM spike:** during `[100, 200)` ms EngineStatus carries 7000 RPM, within
  the DBC's 0–8000 range. The physical RPM continues normally; after clear,
  EngineStatus returns to its measured value. No anomaly detector is claimed.
- **Timing jitter:** EngineStatus uses a 60 ms publication interval and an
  independent uniform integer delivery delay of 0–60 ms during `[100, 300)` ms.
  Frame creation times and payloads remain intact. Clearing preserves the next
  publication already scheduled at 340 ms and deliveries already in flight.

## Schema and parameters

Scenario schema version 1 retains its existing envelope, duration, seed, transport
and driver commands. Two commands are added; their times must align to the 10 ms
input grid and lie within the run:

```json
{"at_ns": 100000000, "kind": "inject_fault", "fault": {
  "fault_id": "rpm-spike", "type": "signal_spike", "target_ecu": "engine",
  "target_message_id": 512, "signal": "EngineRpm", "parameters": {"value": 7000}, "seed": 0
}}
```

```json
{"at_ns": 200000000, "kind": "clear_fault", "fault_id": "rpm-spike"}
```

`fault_id` contains 1–64 ASCII letters, digits, `-`, `_` or `:`. `seed` is an
optional nonnegative integer (default 0); targets are checked against the network
DBC and its publisher. `parameters` is required. Unknown fields, booleans in
numeric fields, nonfinite numbers and invalid signal/message pairs are rejected.
No expressions or executable imports are accepted.

| Type | Parameters | Effect |
| --- | --- | --- |
| `ecu_offline` | `{}` | Suppress publication of the target message, or all of the target ECU's messages when `target_message_id` is `null`. |
| `message_drop` | `probability` in [0, 1] | Independent Bernoulli loss per subscribed receiver, after publication. |
| `message_delay` | integer `min_ns`, `max_ns`, with 0 ≤ min ≤ max ≤ 1 s | Uniform inclusive delay per receiver, added to the configured base delay. Equal bounds give a fixed delay. |
| `signal_spike` | `value` | Replace one analog signal before strict DBC encoding. |
| `signal_stuck` | optional `value` | Hold that value, or capture the signal at its first publication after activation. |
| `signal_bias` | `offset`, optional `rate_per_second` | Add offset + rate × virtual elapsed seconds from activation. Each parameter is finite and in [-10000, 10000]. |
| `message_frequency_anomaly` | integer `period_ns`, 10 ms–5 s, aligned to 10 ms | Override the interval chosen at the next publication callback. |
| `cross_signal_inconsistency` | `offset` in [-10000, 10000] | Offset one WheelSpeeds signal, creating a residual against the other wheels and model speed. |

The signal types require `signal` and a non-null message ID. Supported analog
signals are EngineRpm, Throttle, Brake, CoolantTemperature, FrontLeft, FrontRight,
RearLeft, RearRight, BrakeObserved, ThrottleObserved and EngineRpmObserved, only
where declared in the target DBC message. Enum, link, request-sequence and alive
counter fields are excluded. Other fault types do not accept a signal.

Explicit spike/stuck values must be exactly representable in the DBC. Bias ramps
use the encoder's normal nearest-step quantization. Every intermediate transformed
value must remain in the declared DBC range; otherwise simulation fails explicitly
and a recording is marked failed. Values are never clipped or wrapped. Synthetic
measurement faults do not change VehicleModel or grant receivers hidden inputs.
Network CAN feedback can affect Transmission's gear decisions as described in the
M5 contracts. ABS's own consistency flag remains its sensor-derived report; the
cross-signal fault changes the transmitted wheel measurement, not that report.

## Simultaneous faults, timing and clear

- Input commands and fault switches apply in canonical input order before
  physics/publication at the same virtual time. Inject then clear at the same
  time leaves no effect on publication. Reversing the commands activates it.
- Signal transformations compose in activation order. Replacements override the
  value at their position; biases add to it. A captured stuck value persists until
  that fault is cleared. Clear removes only the identified transformation.
- Offline effects combine with OR. Existing manual `offline`/`online` switches
  are independent gates: `online` cannot clear a library fault, and clearing a
  library fault cannot reopen a manual gate. Publication flags in state describe
  those manual gates; `active_fault_count` counts library faults only.
- Multiple drop faults combine with OR; all matching fault streams advance even
  when another has already dropped the delivery. Delays add. The most recently
  activated frequency override wins; clearing it reveals an earlier override.
- Changing or clearing a period does not move the callback already scheduled.
  Publication cadence continues through offline intervals. Clearing delay/drop
  or going offline does not cancel or rewrite already scheduled deliveries.
  Jitter may reorder frames; each callback delivers its exact immutable frame.
- Each fault has its own `Random` stream, seeded by SHA-256 over the versioned
  namespace, run seed, fault seed and ID. It shares no RNG with base transport,
  physics, scenarios or other faults. Receiver iteration follows the profile.
- At most 32 library faults may be active. Duplicate active IDs, quota overflow
  and clearing unknown IDs produce `InputApplied` with `accepted: false` and no
  fault marker. They do not stop simulation. IDs may be reused after clearing;
  reactivation starts a new episode and resets its RNG/capture. Reset clears all
  faults, counters and pending deliveries and reseeds the streams.
- There are at most 256 pending deliveries per receiver. Overflow is an explicit
  error, including when large base delays combine with injected delays.

## Manual commands and artifacts

Save the `fault` object from the example as `spike.json`, then run:

```sh
uv run --locked autocan shell --profile democar-network --seed 42 --record data/manual-fault
```

Enter `inject spike.json`, `run 100`, `clear rpm-spike`, `run 20`, then `quit`.
`run` takes milliseconds. Specification files are limited to 4096 bytes and copied
into the applied log, so later edits/deletion do not affect rerun. Invalid manual
commands print an error and leave the session available. Commands queued while
paused apply when virtual time resumes. Only applied commands are recorded; use
`run 0` to execute initial queued inputs. An empty recording cannot be finalized.
Reset during a recording is rejected: exit and start a new recording instead.

`commands.jsonl` stores `inject_fault` with a canonical `fault_json` string and
`clear_fault` with its ID, application time/order and acceptance result. Fault
activation/clear events in `faults.jsonl` include the same specification, targets,
seed and episode boundaries. An uncleared episode remains active through the run
end; saving does not synthesize a clear command. `events.jsonl` holds observations
without these labels. Existing publication-offline commands/markers stay supported.

New runs use core `m6-faults-1`. Manifests include the fault algorithm/RNG version
and hash `faults.py` with the other owning core sources. Historical JSONL playback
remains supported, including the v0.1 fixture; rerun requires the exact core hash,
profile and runtime versions recorded by that run.

Golden observations in `tests/fixtures/m6_faults_golden.json` cover availability
deadlines, raw EngineStatus payloads, publication timing and seeded observer
delivery timing for these scenarios. They are compact trace projections rather
than full run manifests; integration tests additionally record, verify and rerun
each demonstration through the actual CLI.

## Local verification — 2026-10-31

- `uv run --locked ruff check .`, `uv run --locked ruff format --check .`,
  `uv run --locked mypy`, `uv run --locked pytest -q`: passed, 270 Python tests.
- `uv build --no-sources`: source distribution and wheel built successfully.
- `npm --prefix frontend run typecheck`, `npm --prefix frontend test`,
  `npm --prefix frontend run build`: passed, 16 frontend tests.
- `npm --prefix frontend run test:e2e`: all four existing Chromium acceptance
  tests passed against the production build and actual API/core. The missing
  pinned browser was installed in a temporary directory; execution used the
  approved environment outside the restrictive macOS sandbox with Chromium's
  own sandbox enabled. These are M5/vertical-slice regression checks, not M6 UI
  acceptance. Remote CI and M6 API/UI acceptance are not claimed.
