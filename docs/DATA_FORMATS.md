# Trace and dataset formats

A run starts as canonical JSONL plus its manifest and DBC snapshot. The CLI
can convert this recording to lossless Parquet, project one receiver's CAN
observations, or generate collections that preserve all three representations.

## JSONL recordings

The dashboard and CLI stream separate event, command, and fault files. The
manifest records configuration, compatibility metadata, checksums, and a
semantic event hash. See the [recording contract](DATA_CONTRACTS.md#recording-and-reproduction).

## Parquet exchange and observer datasets

### Round-trip example

From a source checkout with the locked Python dependencies installed:

```sh
uv run --locked autocan run scenarios/timing_jitter.json --output data/runs/jitter
uv run --locked autocan trace-export data/runs/jitter --output data/parquet/jitter
uv run --locked autocan trace-import data/parquet/jitter --output data/runs/jitter-restored
uv run --locked autocan playback data/runs/jitter-restored
uv run --locked autocan rerun data/runs/jitter-restored
uv run --locked autocan dataset data/runs/jitter-restored --receiver observer --output data/datasets/jitter
```

Every output must be a new directory. There is no overwrite switch. CLI failures
return a nonzero exit code with an error on stderr. These operations do not
start an API server or require the browser. The dashboard uses JSONL recordings.

### Exchange format v1

Each bundle represents one run (use separate run directories when collecting
multiple runs). Its manifest identifies `autocan-parquet`, format version 1 and
complete status. It embeds the original trace manifest without changing run
identity, configuration, initial/final state, seed, RNG streams, scenario
provenance, core/package/profile versions, checksums or semantic hash.

| Artifact | Content |
| --- | --- |
| `frames.parquet` | Typed immutable publications: event sequence/time, frame ID, source ECU, channel, CAN ID, flags, DLC, binary payload and creation time |
| `deliveries.parquet` | Typed per-receiver receipts: event sequence/time, frame ID, receiver ID and receipt time |
| `events.parquet` | Other canonical events (including physical samples and ECU status transitions), as sequence plus lossless canonical JSON |
| `commands.parquet` | Applied commands, as sequence plus canonical JSON |
| `labels/faults.parquet` | Fault activation/clear and transport-drop ground truth, separately from observations |
| `profile.dbc` | Exact saved DBC snapshot |

Tables use explicit nonnullable schemas and integer nanoseconds. They are written
without compression or dictionaries, in groups of at most 256 rows. Export
streams the existing event files and never builds a whole-run dataframe. Empty
tables retain their schemas. Tables have physical SHA256/byte/record metadata
and a semantic SHA256 over ordered canonical rows (binary payloads represented
as lowercase hex). The original canonical event hash is independent of the
Parquet container bytes.

Import first validates the manifest, fixed artifact paths, physical sizes and
checksums. It rejects symlink artifacts, unsupported schemas, null values,
oversized row groups/decoded data, invalid event ordering and altered semantic
content. It merges table iterators by canonical sequence into staged JSONL and
runs the existing trace verifier before publishing the restored run. This also
checks original JSONL checksums, frame/delivery references and the complete
canonical semantic hash. Playback does not simulate physics. A separate rerun
constructs a fresh simulation and compares events/final state; it requires the
exact supported core, profile and runtime versions.

JSONL format/schema version 1 remains supported for both DemoCar profiles
(`democar-0.1.0` and `democar-0.2.0`). Historical runs can be exchanged and played
back even when the current core cannot rerun them. Exchange does not upgrade or
rewrite historical provenance.

### Observer dataset v1

`dataset` consumes a verified JSONL run, selecting `--receiver` explicitly
(default `KOMBI`). The network profile supports its six declared receivers,
including the independent `observer`; the legacy profile supports `KOMBI`.
Unknown receivers fail before producing an output.

`observations.parquet` contains only these fields:

| Field | Meaning |
| --- | --- |
| `received_at_ns` | This receiver's virtual delivery time |
| `channel` | Observed CAN channel |
| `can_id` | Standard 11-bit arbitration ID |
| `is_extended_id` | CAN ID flag (false for the supported profiles) |
| `dlc` | Payload byte count |
| `payload` | Raw binary payload |

Rows follow receipt order, including reordered delayed frames. Publications
that are dropped, still pending at the end, or not subscribed by this receiver
produce no observation. A bounded frame lookup is retired on each selected
receipt/drop; it does not retain the whole trace. An entirely silent receiver
produces a valid empty table.

`labels/faults.parquet` contains fault activation/clear events with their original
specifications and times, including faults still active when the run ends. It
does not invent cleared intervals. The dataset manifest stores selected receiver,
source provenance, end time, schema version, file checksums and semantic hashes.
These are metadata for reproducibility, **not feature columns**. Frame creation
time, source ECU, frame/event IDs, physical state, driver commands, faults,
scenario names, seeds and labels do not enter the observation table. The
[offline pipeline](ML_EVALUATION.md) prepares features and splits separately.

Factory verification also reads the real saved tables, recomputes their semantic
hashes and compares bundle events and observer observations/labels with the
verified source run. This checks the selected receiver's actual deliveries rather
than trusting declared metadata. See the [trace and dataset verification record](archive/M7_ACCEPTANCE.md).

### Bounds and publication

`--max-bytes` accepts 4096 bytes through 16 MiB. The default is 16 MiB. Export
counts its manifest and all artifacts; import bounds both the complete bundle
and reconstructed trace artifacts. Parquet footers and decoded table sizes
are bounded separately (64 MiB decoded per table, at most 256 rows per group).
Studio JSONL recordings can reach 32 MiB. Parquet conversion requires source
JSONL artifacts of at most 16 MiB. An export may need more space than its source
JSONL because of table metadata and provenance. Record a shorter run if the
source or export exceeds the exchange limit.

Conversion stages in a private temporary sibling directory and cleans staging
on failure. The destination is reserved exclusively only after successful
conversion/verification; its complete manifest is published last. No existing
file/directory is replaced, and quota failure publishes no destination. Temporary
disk use includes the source and one staged output; a failed export can briefly
exceed its output quota by buffered row groups/footers before it is removed.

## Dataset Factory

### Generate and verify

```sh
uv run --locked autocan generate --output data/datasets/driving-seed42
uv run --locked autocan verify-dataset data/datasets/driving-seed42
uv run --locked autocan playback data/datasets/driving-seed42/runs/0000/trace
uv run --locked autocan rerun data/datasets/driving-seed42/runs/0000/trace
```

The defaults produce 24 runs, cycling evenly through six driving modes, with
seed 42, 8–12 virtual seconds per run, six fault runs and a 256 MiB total limit.
The selected receiver defaults to `observer`, which receives all eight network
messages. Each fault run has one activation and one explicit clear. Default
fault types are ABS `ecu_offline`, Engine RPM `signal_spike` and Engine
`message_delay`, balanced across the selected fault runs. Normal and fault runs
use the same driving generator.

For a smaller collection, or a particular mode/receiver/fault mix:

```sh
uv run --locked autocan generate --output data/datasets/drive-small --runs 6 --fault-runs 2 --seed 7
uv run --locked autocan generate --output data/datasets/kombi-normal --runs 4 --modes city cruise --receiver KOMBI --fault-runs 0
uv run --locked autocan generate --output data/datasets/drive-faults --runs 8 --fault-runs 8 --modes acceleration --fault-types ecu_offline message_drop message_delay signal_spike signal_stuck signal_bias message_frequency_anomaly cross_signal_inconsistency
```

CLI stdout is a single JSON completion record with run count and semantic hash;
errors go to stderr with a nonzero exit status. Outputs must be new directories.
Factory operations are CLI-only; there is no dashboard generation/upload flow.

### Driving behavior

All recipes use the existing `democar-network` profile, initial state and physical
parameters. An initial brake/Drive request travels through Body and Transmission
CAN before the first throttle application. Controls align to the 10 ms physics
grid and stay within the strict scenario schema and command quota.

| Mode | Nominal behavior without injected faults |
| --- | --- |
| `idle` | Running engine, Park, no motion |
| `stop_and_go` | Three acceleration/braking cycles with complete stops |
| `city` | Acceleration, a steady segment, stop, Neutral/Drive selection, another drive and stop |
| `cruise` | Accelerate, then hold constant speed with model-specific 5% throttle |
| `braking` | Accelerate, apply variable braking strength and finish stopped |
| `acceleration` | Drive continuously with a later increase in throttle |

Cruise is an open-loop demonstration of the simple resistance model, not a
speed controller. Initial temperature, vehicle load and base message periods
are fixed by the current core; the recipes vary controls and timing.
Faults can alter network feedback and actual vehicle behavior. Mode names
describe the driver recipe, not a promise that faulty runs follow the normal
trajectory. A recorded fault is ground truth about the intervention; it is not
automatically a detectable anomaly in every mode or receiver's stream.

### Reproducibility and variation

`m7-driving-1` uses independently derived RNG streams for mode ordering, per-run
driver commands/durations, fault-run selection and fault parameters/timing.
Each complete mode cycle is shuffled, so mode counts differ by at most one.
Each run has its own simulation seed. Pedal strengths, transition times,
durations and fault parameters vary reproducibly. Fault starts/durations are
sampled on the physics grid inside the trip, rather than always at one fixed
timestamp. All eight existing fault-library types can be selected.

Changing `--fault-runs` or `--fault-types` leaves driving commands, durations and
simulation seeds unchanged for the same remaining config. CAN feedback can
still change the resulting trajectory. Changing the master seed changes the
recipes. A source scenario is saved for every run, so playback/rerun never
depends on reconstructing a recipe from the current generator.

### Files and provenance

```text
manifest.json
runs/0000/
  scenario.json
  trace/       # canonical JSONL, commands, ground truth, DBC and manifest
  parquet/     # normalized frames/deliveries and lossless exchange tables
  observer/    # observations.parquet, labels/faults.parquet and manifest
runs/0001/
  ...
```

The root manifest records the generator version, full normalized configuration,
run index/path/identity, mode, seed, duration, fault type, scenario checksum and
trace/observation semantic hashes. Its artifact inventory covers every generated
file under each run, including nested manifests. Those manifests retain the
core source hash, profile snapshot/version, package versions, configuration,
initial/final states, independent RNG seeds, git commit/dirty flag and original
event/artifact hashes. A dirty flag means generation used an uncommitted checkout.

The collection semantic hash combines configuration, generator version and each
run's recipe/trace/observation semantic hashes. It excludes newly assigned run IDs,
paths and physical container/metadata bytes. Repeating the same config on the
same compatible core/runtime preserves this hash, even though run IDs and
physical checksums can change. Different output directories do not affect it.

`verify-dataset` checks bounded manifests, the fixed inventory, all physical
checksums/sizes, nested Parquet schemas/semantic hashes and their event/observation/
label equivalence with the verified JSONL source. It checks the seeded recipes
against applied commands, profile/transport settings, linked provenance and the
collection semantic hash. It rejects missing/corrupt artifacts, symlink path
components, unexpected inventory paths, unsupported versions and recipe changes.
It reads the saved files without running physics. Verification is an integrity
check, not a digital signature or a fresh simulation rerun. To recompute events,
run the separate `rerun` command on a run's `trace` directory; to exercise Parquet
reload, use `trace-import` on its `parquet` directory.

Only receipt time and observed CAN fields enter `observations.parquet`.
Fault intervals remain activation/clear ground-truth events under `labels/`;
mode, seed, scenario, driver commands and physics/provenance stay outside input
columns. The [offline pipeline](ML_EVALUATION.md) assigns splits and window
labels separately.

### Limits and failure behavior

- 1–300 runs; a unique, nonempty selection of modes and fault types.
- `--fault-runs` is between zero and the run count; omitted means `runs // 4`.
- `--min-duration`/`--max-duration` are seconds, aligned to 10 ms, with
  `8 <= min <= max <= 30`. Master seed is a nonnegative signed 64-bit integer.
- `--max-bytes` applies to the entire collection, including every manifest and
  artifact; default 256 MiB, supported range 4096 bytes–1 GiB. Each underlying
  JSONL/Parquet/observer output keeps the existing 16 MiB per-output cap.
- Runs are generated sequentially with bounded record/Parquet buffers. The
  collection manifest is limited to 2 MiB; no whole-run dataframe is retained.
- Generation stages privately beside the requested directory and publishes its
  complete root manifest last, after all runs succeed. Quota exhaustion or a
  failed run removes staging and publishes no collection. Existing destinations
  are never replaced. A process crash can leave an incomplete staging directory;
  there is no automatic deletion of other local data.

Peak disk use includes the staged collection and temporary per-export buffers;
quota enforcement can briefly overshoot by those bounded buffers/manifests.
