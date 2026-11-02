# Causal windows and Tier 0 rules — M8 first pass

This headless pass consumes an M7 selected-receiver dataset. It produces causal
feature windows, rule scores and grouped alert transitions as JSONL. It does not
evaluate detection quality: run splits, episode matching, false alarms/hour,
fixed benchmarks and OOD evaluation remain the next M8 pass. No ML is included.

## Run

From the source checkout, using the locked Python environment:

```sh
uv run --locked autocan run scenarios/rpm_spike.json --output data/runs/m8-spike
uv run --locked autocan dataset data/runs/m8-spike --receiver observer --output data/datasets/m8-spike
uv run --locked autocan baseline data/datasets/m8-spike --window 0.1
```

Use new output directories for `run`/`dataset`; existing data is never replaced.
`baseline` reads that dataset without modifying it and prints JSONL to stdout.
The `source` argument is the observer dataset, not a JSONL run or a collection
root. For a generated collection use a run's `observer/` directory. The dataset's
receiver determines subscriptions; unavailable signals stay missing. The legacy
`democar` profile supports `KOMBI`; the network profile supports its routed
receivers, including the independent `observer` that sees all eight CAN IDs.

The first record, `baseline_config`, identifies the profile, selected receiver,
run end, feature schema, detector version and every parameter. Subsequent records:

- `features`: window bounds, total/per-ID counts, last-receipt ages, timeouts,
  interarrival min/median/max and population standard deviation (jitter),
  RPM mean/median/population standard deviation, maximum absolute RPM derivative,
  maximum wheel residual and explicit missing-data flags.
- `score`: rule ID, affected CAN ID, end-of-window `sim_time_ns`, score, threshold,
  `anomalous`, `detector_id: tier0-1` and `feature_schema_version: 1`.
- `alert`: the same evidence with `state: opened` or `state: resolved`.

The thresholds are explicit synthetic demonstration defaults, not thresholds
selected through validation. CLI flags:

| Flag | Default | Meaning |
| --- | --- | --- |
| `--window SECONDS` | `0.5` | Positive duration exact in integer nanoseconds |
| `--heartbeat-periods N` | `5` | Timeout = max(100 ms, N × DBC message period) |
| `--rpm-derivative-threshold VALUE` | `20000` | Absolute RPM/s; strictly greater opens |
| `--wheel-residual-threshold VALUE` | `5` | km/h; strictly greater opens |

Heartbeat scores are ages in nanoseconds and become anomalous at **or above**
the timeout. Ages start at run time zero for a never-seen expected CAN ID.
RPM derivatives use consecutive delivered EngineStatus measurements, including
the preceding window's last sample. Their time difference must be positive and
no larger than that message's timeout; equal-time receipts and long gaps do not
produce a derivative. Interarrival statistics include zero and long gaps and
attribute each interval to the window containing its later receipt.

The current DBC has no independent vehicle-speed reference. `wheel_residual`
therefore compares each of the four values in one WheelSpeeds payload with their
median and takes the largest absolute difference. It detects disagreement among
wheels, including the synthetic single-wheel inconsistency fault. Equal bias on
all wheels is invisible. It does not read simulator speed or the ECU's computed
`WheelsConsistent` status and makes no physical diagnosis.

## Time, missing data and grouping

The first window is `[0, W]`; later windows are `(kW, (k+1)W]`. Deliveries at an
exact deadline run in `SimulationClock`'s DELIVERY phase before window closure in
DEADLINE. A frame created before a window ends but delivered after it ends is
excluded. Only receipt time and wire fields enter `FeaturePipeline`; creation
time, frame/source identities, seeds, scenario names, fault markers, physical
state and labels are absent from its interface and feature records.

Windows close on virtual time, even if no message arrives. Pause freezes those
deadlines; explicit stepping advances them. The first full window is eligible
for detection; heartbeat warm-up lasts until each message's timeout, while RPM
derivatives require two usable receipts. No signal samples means `null`, never
zero or a retained value presented as a current measurement. A single RPM sample
has a standard deviation of zero but may still lack a derivative. Incomplete
trailing windows are omitted rather than padded; a run shorter than one window
emits only its configuration record.

Each rule/CAN ID has at most one open alert. Repeated anomalous windows extend
that episode without repeating its opening. The next available normal score
resolves it; missing evidence cannot clear it. Active alerts remain open at run
end. This first pass has no hysteresis or additional debounce interval. Alert
timestamps are window ends, so detection can lag evidence by up to a window;
brief outages recovered before closure can be missed by the heartbeat rule.

`open_observer` checks M7 bundle integrity, observation schema, ordering,
semantic checksum and compatibility with the packaged DBC. Fault-label files
are checked for file integrity but never decoded or supplied to the pipeline.
Exhausting the observation iterator completes semantic verification. A corrupt
input or exceeded quota returns a nonzero CLI status; streamed output before
such an error is incomplete and must be discarded.

The pipeline stores one window plus each CAN ID's last receipt and the preceding
RPM sample. It caps observations at 20,000 per window and full windows at 10,000;
overflow fails explicitly. Parquet reading uses existing bounded row groups and
the 16 MiB M7 input limit. Offline replay schedules only the next receipt. The
same pipeline accepts live `CanObservation` values with the same clock ordering;
this pass does not attach it to the Studio API, WebSocket or dashboard (M10).
