# Seeded driving modes and Dataset Factory

The headless factory builds small synthetic run collections for a specific CAN
receiver. It reuses the existing network simulator, applied-command recorder,
[Parquet exchange and observer export](PARQUET.md). It does not train a model or
construct feature windows, class labels or train/validation/test splits.

## Generate and verify

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

## Driving behavior

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
are fixed by the current core; this pass does not invent new physics parameters.
Faults can alter network feedback and actual vehicle behavior. Mode names
describe the driver recipe, not a promise that faulty runs follow the normal
trajectory. A recorded fault is ground truth about the intervention; it is not
automatically a detectable anomaly in every mode or receiver's stream.

## Reproducibility and variation

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

## Files and provenance

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
columns. Future M8 processing must split by whole runs before feature fitting;
this factory does not assign splits or window/frame anomaly classes.

## Limits and failure behavior

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
No SQLite catalog, ambient-temperature/load randomization, ML, release tag or
publication is part of this pass.

## Initial implementation acceptance

The implementation pass ran `uv run --locked pytest` (324 passed), Ruff lint
and formatting checks, strict mypy and `uv build --no-sources` successfully.
Focused coverage includes physical modes at both 8 s and 30 s, all eight fault
types with fresh rerun, repeated-series semantic equality, Parquet reload,
receiver input separation, failed-quota cleanup, corruption and symlink rejection.
The default 24-run collection was also generated and verified: four trips per
mode, six fault runs, 384 artifacts and 68,080,321 bytes including its manifest.
Generated data stays ignored under `data/`. Frontend/browser checks were not run
for this CLI-only pass; no remote CI or release result is claimed.

The subsequent [M7 final acceptance](M7_ACCEPTANCE.md) adds the provenance fixes,
full collection round trips/reruns and current Python/frontend/browser results.
