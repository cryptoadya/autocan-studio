# Milestones

This public roadmap describes deliverables, not a command to implement all of
them at once. Local planning/instruction files are not required to use, build,
or test this repository.

| Milestone | Scope |
| --- | --- |
| M0 | Python foundation, environment/lockfile, checks, architecture/data contracts, synthetic EngineStatus DBC |
| M1 | Deterministic clock, throttle/RPM model, Engine ECU, CAN delivery, KOMBI and headless CLI |
| M2 | Offline fault, silent-bus timers, JSONL/manifest, playback and deterministic rerun |
| M3 | Minimal local API, Dashboard, graph, CAN Monitor and reconnect |
| M4 | Browser acceptance and first vertical slice release v0.1 |
| M5–M7 | Additional ECU/controls, fault scenarios and dataset factory; V1 without ML |
| M8–M11 | Causal features, baselines, classic ML, live inference and measured V2 release |
| M12 | Optional extensions after V2 |

The first vertical slice replaces the old initial roadmap. It is not an extra
project before it. M0 specifies timing and isolation; their runtime acceptance
belongs to M1/M2. No UI or ML implementation is included in M0.

## M0 acceptance boundary

Completed locally and published as the foundation commit. M1 extends the
headless runtime while preserving the DBC contract and M0 checks.

- A fresh checkout installs with `uv sync --locked` and runs the README checks.
- EngineStatus has a strictly parsed original DBC, golden payloads, declared
  resolution/range/period, explicit encoding errors, and packaged-resource access.
- Contracts define initial state, allowed ECU inputs, frame/delivery boundaries,
  event order, stale/timeout deadlines, and replay versus rerun.
- Local master-plan and AGENTS.md are ignored and excluded from distributions.
- CI configuration uses the same checks; a remote CI run requires a separately
  authorized GitHub repository. No remote CI success is claimed by local checks.

## M1 acceptance boundary

- Stable nanosecond scheduling, phase/enqueue ordering, bounded event/transport
  queues, pause/resume, explicit stepping, and reset.
- Throttle -> physical RPM -> permitted Engine sensor -> DBC -> VirtualBus ->
  per-receiver delivery -> KOMBI decoding, demonstrated by CLI and tests.
- Physical RPM changes between publications do not refresh KOMBI's measured RPM.
- Two 30-second virtual runs with the same seed and commands produce identical
  canonical events after excluding run identity; no wall-clock delays are used.
- CLI supports finite exact durations, JSON state/events and shell controls.
- ECU Offline, STALE/TIMEOUT, persisted traces and replay remain M2; UI remains M3.

## M2 acceptance boundary

- Offline suppresses publication while physical RPM continues; pending deliveries
  survive Offline and re-enable resumes on the original publication grid.
- KOMBI reaches STALE at last receipt +60 ms and TIMEOUT at +200 ms on a silent
  bus, ignores old deadlines and accepts a delivery exactly at a deadline first.
- Fixed delivery delay and seeded loss reproduce independently of physical RPM.
- Bounded JSON scenarios run through CLI; commands and fault ground truth are
  separate files in a streaming JSONL recording with a provenance manifest.
- Save/load/playback preserves canonical events. Rerun reconstructs them from
  the applied command log and compares semantic hash and final state.
- Recording never overwrites a run; quota/failure/incomplete/corrupt/incompatible
  inputs are rejected explicitly. Existing M0/M1 checks remain applicable.
- M2 excludes API/UI (introduced in M3), Parquet/dataset factory, extended faults
  and ML.

## M3 acceptance boundary

- Local FastAPI REST/WS and React/TypeScript/Vite dashboard, launched from a source
  checkout with `uv run --locked autocan studio`; frontend has a pinned npm lockfile.
- Throttle, publication-only Offline, received KOMBI RPM/freshness, one virtual-time
  chart and a CAN Monitor for delivered frames. No physical RPM substitution.
- Start/pause/save/reset, complete time-zero recordings, verified playback with
  seeking and deterministic rerun through the existing M2 artifact contracts.
- Bounded server/client histories, 50 rendered table rows, 20 Hz live batches,
  snapshot reconnect, explicit quotas and same-origin loopback access.
- API/core/trace integration tests, frontend state/component tests, build/typecheck,
  and a local browser smoke of throttle, STALE/TIMEOUT, save/playback/rerun, reset,
  socket reconnect and a 390 px mobile viewport. Screenshot: `images/m3-dashboard.png`.
- M4 remains the versioned release and repeatable release-acceptance/demo pass.
  No v0.1 release, deployment or remote CI success is claimed by this local work.

## M4 acceptance boundary

- Repeatable Chromium acceptance against the built UI and real local API/core:
  throttle -> CAN -> KOMBI, publication-only Offline, retained measurement,
  virtual deadlines in playback, saved recording and matching deterministic rerun.
- Pause/reload snapshot recovery, disabled live controls during playback, reset
  and a 390 px viewport are included in the browser pass.
- A clean source-checkout quickstart, the reproducible engine-offline scenario,
  a short browser recording and a contract-to-check acceptance report.
- Python/frontend checks plus a separate Linux Chromium CI job, with browser
  artifacts retained for inspection. Remote CI requires an authorized push.
- Package version 0.1.0 prepares the local vertical slice for release. Publishing
  a tag/GitHub Release remains a separate action; it is not implied by this work.
- No additional ECU, controls, faults, Parquet, datasets or ML are included.

See [v0.1 acceptance](V0_1_ACCEPTANCE.md) for executed checks and limitations.

## M5 first pass — physical engine controls

- Ignition OFF/ACC/ON/START and explicit physical Start/Stop through the existing
  model/core, strict scenarios, applied command logs, CLI, API and dashboard.
- Stopped RPM coasts to zero with continued FRESH EngineStatus delivery. ECU
  Offline remains publication-only; controls while offline cannot refresh KOMBI.
- Pause/reset, deterministic command ordering, batch quotas, verified playback
  and fresh rerun cover the new controls. A small unchanged v0.1 fixture verifies
  historical playback; incompatible old-core rerun is rejected explicitly.
- `scenarios/engine_start_stop.json` and `images/m5-engine-controls.png` demonstrate
  this pass. Local unit/integration and browser verification accompanies it;
  remote CI requires a separately authorized push.
- This part establishes engine controls; the motion continuation is described
  below. M5 remains in progress until the full ECU network acceptance is complete.

## M5 motion controls

- Deterministic longitudinal motion, brake, P/R/N/D, directional speed limits,
  coasting and speed/gear-dependent running RPM, preserving parked RPM behavior.
- Physical interlocks reject unsafe shifts visibly without stopping simulation;
  controls and accepted/rejected results are recorded on the canonical input grid.
- CLI/API/UI controls, explicit model-speed display, recorded motion samples,
  verified playback without physics and deterministic rerun. KOMBI RPM stays CAN-only.
- `scenarios/motion_controls.json` and `images/m5-motion-controls.png` demonstrate
  this scope; regression/browser checks cover drive/coast/brake/reverse, rejected
  shifts, Offline isolation and saved speed. Remote CI requires an authorized push.
- Still remaining in M5: ABS/DSC, Transmission and Body ECU, expanded DBC and
  subscriptions, CAN speed/temperature/availability in KOMBI, and full-network
  normal urban drive acceptance. This part does not mark M5 complete.

## M5 ECU network — local acceptance complete

- `democar-network` / `democar-0.2.0`: Engine, ABS/DSC, Transmission, Body and
  KOMBI publish/receive eight DBC messages through explicit subscription routes.
  The allowed ports and subscriptions are in [data contracts](DATA_CONTRACTS.md).
- Body publishes driver requests; Transmission applies gear selection after CAN
  delivery and fresh-input/physical interlocks. RPM, brake, throttle and gear
  appear in the declared messages. No ECU reads another ECU's internal state.
- KOMBI derives speed, temperature, gear, ignition and consistency from its own
  delivered CAN data. Separate virtual deadlines detect loss on a mixed or silent
  bus. Engine Start/Stop and publication Offline retain distinct tested effects.
- UI defaults to this network; CLI/API preserve the legacy default. Live and saved
  views use the selected profile, and rerun reproduces delayed/lost deliveries.
- `normal_urban.json` covers start, drive, stop, neutral, another drive, parking
  and engine stop. Acceptance verifies five ECU, all fresh received fields, actual
  motion/stops and identical rerun. `images/m5-ecu-network.png` shows the real UI.
- Local verification: 224 Python tests, 16 frontend tests, 4 Chromium acceptance
  tests, Ruff, mypy, TypeScript and production build passed. Remote CI/release
  remain separate delivery actions. The next milestone is M6 fault scenarios.

## M6 first pass — fault library and CLI scenarios

- Eight fault types for the network profile, strict parameter/target validation,
  explicit in-DBC encoding and bounded active/pending state. See [fault contracts](FAULTS.md).
- Canonical inject/clear, specified composition and independent RNG. Clear/reset,
  pending deliveries, publication gates and physical state have distinct effects.
- Three CLI demonstrations, golden observation projections, saved manual commands,
  verified playback and rerun without the source JSON files. Old playback is retained.
- This pass leaves API/UI fault parameters, scenario selection, timeline and the
  M6 visual/browser demonstrations for the next task. M6 is not complete; M7
  datasets and ML work have not started. No external delivery is implied.

## M6 API/UI — local acceptance

- Built-in ABS loss, RPM spike and timing jitter run from CLI and dashboard,
  auto-save at their exact duration and reproduce from their applied logs.
- Strict REST injection/clear shares command quotas with scheduled inputs.
  DBC-derived choices drive the eight-type editor; M5 publication controls remain
  independent. Invalid definitions, stopped runs and conflicting profiles are rejected.
- A labelled ground-truth timeline retains the latest 100 events, with independent
  active-fault state, reconnect snapshots, reset and causal playback/time seeking.
  Observer delivery order remains correct under jitter, with visible delay values.
- The wheel carries the original demos; optional scenario provenance survives
  recording. Local verification and the screenshot are in [fault scenarios](FAULTS.md).
- This completes the local M6 acceptance boundary. M7 datasets, ML, remote CI,
  publication and a new release are separate work.

## M7 first pass — Parquet exchange and observer datasets

- Typed streaming frames/deliveries, lossless auxiliary event/command tables,
  physically separate ground truth, original DBC and verifiable provenance.
- JSONL -> Parquet -> JSONL retains canonical events and supports existing
  playback and a separate fresh rerun. Historical JSONL v0.1 playback remains.
- A selected receiver's observation table uses only wire fields and receipt time;
  drops, pending deliveries and other subscriptions cannot leak into its input.
- New-directory-only output, bounded row groups/imports, checksums/semantic hashes
  and explicit quotas. See [formats and CLI examples](PARQUET.md).
- M7 remains in progress: multiple driving modes and reproducible multi-run
  Dataset Factory generation are the next coherent pass. SQLite is deferred until
  metadata queries require an index. ML starts in M8; no V1 release is implied.

## M7 Dataset Factory — local storage/generation acceptance

- Six nominal driving modes with verified physical motion/stops, balanced mode
  cycles, varied durations/commands and independent fault planning from one seed.
- Sequential generation of normal/fault runs with JSONL, normalized Parquet,
  selected-receiver observations and physically separate fault truth. All eight
  library fault types are available; original scenarios are saved per run.
- Root artifact inventory, linked provenance and a reproducible collection hash;
  `verify-dataset`, existing playback and separate fresh rerun retain their roles.
- Per-output and collection quotas, bounded run counts/durations/row groups and
  new-directory-only staged publication. No metadata index is currently needed.
- See [Dataset Factory](DATASET_FACTORY.md) for commands, formats and limitations.
  This implements the M7 headless storage/generation boundary. V1 release
  acceptance/publication and M8 features/splits/baselines remain separate work.

## M7 final acceptance — complete locally

- Verified every M7 criterion, retained historical JSONL playback and completed
  24 generated-run Parquet imports/playbacks plus separate fresh reruns covering
  203,527 canonical events. Checksums, source linkage and semantic hashes agree.
- Corrected collection verification to read/recompute derived tables and compare
  observer rows/labels and actual applied commands with their recorded sources.
- Current Python, frontend and eight real Chromium E2E checks passed. A built
  wheel also runs the headless generator outside the source checkout.
- [Acceptance report](M7_ACCEPTANCE.md) records evidence, reproduction, the browser
  screenshot and explicit limits. No M7-local acceptance work remains; M8 and
  release preparation/publication are separate actions.

## M9 preparation and common evaluation

- [Window datasets](TRAINING_DATA.md) freeze stratified whole-run/group splits,
  separate numeric inputs from truth/keys and fit preprocessing only on train.
- The [shared window evaluator](WINDOW_EVALUATION.md) adds precision/recall/F1,
  AP/PR and binary confusion counts for rules and future scores with strict
  alignment, eligibility and explicit threshold policies.
- [Saved artifact loading and rule CLI](SAVED_WINDOW_RULES.md) validate source,
  schemas, lineage, frozen transforms and coverage, then evaluate fixed rules
  on the saved preparation splits with reproducible metrics/reports.
- The [first IsolationForest experiment](ISOLATION_FOREST.md) fits eligible
  normal train windows, selects its cutoff only on validation and compares
  the frozen detector against rules on the same saved partitions.
- [RandomForestClassifier and joint comparison](RANDOM_FOREST.md) add supervised
  labeled-train fitting with train-only class weights, frozen all-train
  preprocessing and independent validation-only threshold selection.
- OOD model evaluation, event/fault analysis and broader uncertainty/runtime
  acceptance remain subsequent M9 work; this is not the whole milestone's completion.
