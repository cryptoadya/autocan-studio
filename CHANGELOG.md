# Changelog

## Unreleased — Reuse immutable M9 test fixtures

- Share session-local ID data, prepared windows, rules/models and OOD score
  artifacts across ML/OOD and preparation tests, with final immutability checks.
- Retain independent second CLI runs for reproducibility and isolated copies for
  corruption cases. Keep all scenarios, assertions and full production validators.
- Guard shared producer calls against preprocessing refits and, for scoring,
  estimator fitting or threshold selection. No persistent cross-run cache is used.

## Unreleased — M9 event metrics from saved scores (seventh pass)

- Add `evaluate-events` for both saved ID and OOD score streams, validating
  lineage, bounded tables, window/model alignment and frozen cutoff decisions.
- Share metadata-only experiment validation without deserializing estimators;
  never fit, recompute features/scores or select settings on test/OOD.
- Compare all five global detectors with fixed temporal one-to-one event matching,
  window-end onsets, one-window grace, original-start latency and explicit censoring.
- Publish split/domain and per-fault detection rate/mean/p50/p95 latency, normal
  false alarms, per-run match evidence and input/code/environment provenance.
- Report preserved-fixture RF OOD detection of 6/6 at p50/p95 60/140 ms alongside
  its unchanged normal false alarms; retain the small-sample/global-alarm limits.

## Unreleased — M9 frozen forest OOD evaluation (sixth pass)

- Add `evaluate-ml-ood` for trusted saved joint experiments, with bounded
  integrity, source/split/feature lineage, environment and frozen policy checks.
- Reuse both estimators, preprocessing and validation thresholds without fitting,
  selecting new cutoffs or rescoring the ID test. Preserve separate OOD domains.
- Compare five detectors through common window metrics, per-fault-type rows and
  normal false-positive windows/contiguous alarm episodes per eligible hour.
- Save aligned features/keys/truth/scores, frozen JSON audits and provenance;
  verify unchanged inputs, repeatability, incompatible artifacts and quota cleanup.
- Recover previous PR #17 models, freeze an authorized compatible OOD suite once,
  and report RF's high fault F1 alongside substantial normal-family false alarms.

## Unreleased — M9 supervised RandomForest comparison (fifth pass)

- Fit a fixed, seeded RandomForestClassifier on eligible labeled train windows
  with frozen all-train preprocessing and train-only balanced class weights.
- Reuse validation-only F1 threshold selection independently for both forests;
  freeze both models/cutoffs before test scoring and never load OOD for tuning.
- Add `train-random-forest` with both models/threshold audits, label fingerprints,
  fifteen common metric rows, scores, report and provenance on saved splits.
- Share bounded orchestration while preserving standalone IsolationForest's
  format, estimator bytes and results. Verify holdout isolation, rule/IF parity,
  reproducibility and quotas. Keep model imports, OOD and final acceptance separate.

## Unreleased — M9 first IsolationForest comparison (fourth pass)

- Fit a seeded, fixed-parameter IsolationForest only on eligible windows from
  entirely normal train runs, reusing the frozen normal-train preprocessor.
- Choose the anomaly-score cutoff by validation-only pooled F1 with explicit
  tied-score/highest-threshold policies; freeze selection before scoring test.
- Add `train-isolation-forest` with saved model/preprocessing/threshold/scores,
  twelve common metric rows, rule comparison report and hashed provenance.
- Lock NumPy/scikit-learn dependencies; verify leakage guards, rule parity,
  threshold optimum, repeatable outputs and quotas. Record the small fixture's
  loss to the wheel-residual baseline honestly. OOD is not loaded or tuned.

## Unreleased — M9 saved window loading and rule CLI (third pass)

- Load existing prepared artifacts with bounded source-bound validation of
  manifests, schemas, physical/semantic hashes, feature order, splits and labels.
- Audit coverage, missing masks, frozen preprocessing scopes/train fingerprints
  and saved transforms without fitting preprocessing or training a model.
- Add `evaluate-rules` on saved splits/configuration, publishing nine common
  window metric rows, PR curves, confusion matrices, a report and provenance.
- Preserve inputs, refuse existing outputs, enforce quotas and stage publication.
  No threshold selection, OOD changes, new dependencies or Studio UI changes.

## Unreleased — M9 common window evaluator (second pass)

- Add a shared score/truth contract with strict window identity/order, whole-run
  split, eligibility, finite-score and quota checks, without training models.
- Pool precision/recall/F1 and normal/anomaly confusion counts at an externally
  fixed threshold; compute non-interpolated AP and tie-grouped PR curves.
- Score all three rules from raw M9 numeric features, retaining missing-evidence
  windows with explicit zero scores and preserving rule threshold boundaries.
- Verify prepared tables/splits and warm-up coverage. M8 episode/OOD artifacts
  retain their behavior; artifact import, comparison CLI and fitting remain later work.

## Unreleased — M9 window datasets and train-only preprocessing (preparation)

- Add `prepare-ml` with pure numeric Parquet features/missing masks, separate
  run/window keys and intervention labels, and frozen transformed matrices.
- Preserve duplicate groups while stratifying configured mode/normal/fault
  coverage across train/validation/test; validate eligible-window coverage too.
- Fit JSON median imputers/population scalers only on eligible train windows,
  with separate all-train and entirely-normal-train scopes; never fit held-out data.
- Record window-label/warm-up policies, coverage, fit fingerprints, source/split/
  column lineage, code/environment provenance, quotas and repeatable logical hashes.
- Keep M8 split/references unchanged. No estimators, threshold selection, OOD
  fitting, new ML dependencies or UI changes are included.

## Unreleased — M8 frozen OOD fault and normal sets (third pass)

- Add `generate-ood` to reserve fault types and normal families absent from all
  verified ID runs, freezing settings and source/reference hashes before generation.
- Add `benchmark-ood` with no threshold options, exact reference/protocol binding,
  nested M7 verification and disjoint identities/scenarios/observer inputs.
- Reuse causal replay and episode evaluation for six separate domain/rule rows:
  fault detection/latency and normal false alarm episodes/hour, without OOD tuning.
- Save reproducible protocols, per-run evidence, metrics, reports and provenance.
  Keep synthetic family/type limits, sample sizes, quotas and publication behavior
  explicit. No new physics, parameter-only OOD, ML, threshold fitting or UI changes.

## Unreleased — M8 whole-run splits and fixed-rule benchmark (second pass)

- Freeze seeded train/validation/test partitions before features; keep exact
  scenario/observer duplicates in one group and reject incompatible split reuse.
- Add CLI `benchmark` over verified M7 collections with bounded, staged output:
  split manifest, per-run evidence, nine rule/split metric rows and a readable report.
- Group message alerts per rule and match fault episodes one-to-one with explicit
  target, grace, censoring and latency semantics. Pool detection/precision/F1,
  per-fault-type latency and false alarm episodes/hour on fully normal runs.
- Record dataset/config/code/environment provenance and logical/physical hashes.
  Thresholds remain predeclared; OOD sets, tuning, ML and UI remain subsequent work.

## Unreleased — M8 causal windows and Tier 0 rules (first pass)

- Read verified M7 selected-receiver observations without supplying ground truth
  to the feature pipeline; share receipt-time windows between offline and live inputs.
- Close bounded windows on virtual deadlines, including silence, with explicit
  missing-data, warm-up, boundary and interarrival/derivative semantics.
- Add CLI `baseline` with versioned features, scores and grouped open/resolved
  alerts for heartbeat timeout, absolute RPM derivative and wheel disagreement.
- Compare wheels against their observed median because the current DBC has no
  independent vehicle-speed signal. Run splits, benchmark/OOD evaluation, ML and
  Studio API/UI integration remain subsequent work.

## Unreleased — M7 final acceptance and provenance checks

- Recompute nested Parquet/observer schemas and semantic hashes during collection
  verification, comparing every event, observation and fault label to its source.
- Link saved driving recipes to the actual applied command log, profile and
  transport settings; reject rehashed but internally inconsistent collections.
- Add regression coverage for both verifier defects. Complete local M7 acceptance
  with full generated-collection round trips/reruns, Python/frontend/browser
  checks, built-wheel startup and a current acceptance report/screenshot.
- No ML, version bump or release publication is included.

## Unreleased — M7 driving modes and run collections

- Add seeded idle, stop-and-go, city, cruise, braking and acceleration recipes,
  with balanced mode cycles and varied pedal strengths, timing and durations.
- Generate normal/fault run series through the existing simulator, JSONL recorder,
  normalized Parquet exchange and selected-receiver observation export. Keep
  fault planning independent from driver recipes and support all eight fault types.
- Save per-run source scenarios and a collection manifest with linked provenance,
  full artifact inventory and a semantic hash that excludes new run identities.
- Add `generate` and `verify-dataset`, collection quotas and staged publication
  without overwriting. Cover physical mode effects, deterministic series, fresh
  reruns, Parquet reload, corruption and failed generation cleanup.
- Feature windows, dataset splits and ML remain M8+. No V1 release is published.

## Unreleased — M7 Parquet exchange and observer datasets (first pass)

- Add streaming typed Parquet frames/deliveries and separate event, command and
  fault tables, preserving the original manifest, DBC, canonical events and hashes.
- Restore verified bundles as new JSONL runs for existing playback and fresh
  deterministic rerun. Keep historical v0.1 playback and strict rerun compatibility.
- Export one receiver's actual wire observations and receipt times, with separate
  fault labels and provenance. Exclude simulator internals from observation columns.
- Bound row groups, import sizes and output quotas; reject corruption and symlink
  artifacts and exclusively reserve new output directories without overwriting.
- Driving-mode/run generation remains the next M7 pass. No ML or V1 release.

## Unreleased — M6 API/UI scenarios, faults and timeline

- Add a built-in scenario catalog, strict REST inject/clear, DBC-derived editor
  metadata and scenario sessions that save at their exact virtual duration.
  Keep publication-gate requests compatible and reserve shared command quotas.
- Add scenario/seed selection, all eight fault editors, active cards with clear,
  and a bounded demo-ground-truth timeline with timestamp seeking in playback.
  Pause, reconnect, reset and saved views preserve canonical application rules.
- Record optional scenario provenance and package the three original JSON demos
  in the wheel. Saved playback and rerun do not require those source files.
- Stream reordered observer deliveries by display sequence and show synthetic
  delivery delays in CAN Monitor. Prevent duplicate panels during run changes.
- Add API/component/browser acceptance for three scenario effects, manual
  injection/clear, saved timelines, deterministic rerun and a mobile viewport.
  M6 local acceptance is documented in `docs/FAULTS.md`; M7 has not started.

## Unreleased — M6 fault library and CLI scenarios (first pass)

- Add eight validated network faults at encoding, scheduling and delivery
  boundaries, with independent seeded RNG, explicit DBC range errors, bounded
  active/pending state and deterministic overlap/clear rules.
- Preserve exact frame identity under reordered delayed deliveries. Keep
  publication gates, physical engine controls and CAN receiver inputs separate.
- Add scenario inject/clear, interactive CLI specification files and optional
  shell recording. Save applied results/specifications for verified playback
  and manifest-based rerun without the source scenario or fault file.
- Add ABS loss, RPM spike and timing jitter scenarios with golden trace
  projections and focused regression/CLI coverage. Historical playback remains
  supported; exact-core rerun uses the new `m6-faults-1` source hash.
- M6 remains in progress: API/UI scenario controls, parameters, timeline and
  browser demonstrations are the next coherent pass. No release is implied.

## Unreleased — M5 ECU network

- Add the versioned DemoCar 0.2 profile: five publishing/subscribing ECU, eight
  original DBC messages, narrow sensor/actuator ports and per-message deadlines.
- Route gear requests through Body CAN and Transmission's fresh-input/interlock
  checks. Add signed wheel speeds, coolant temperature and independent link states.
- Extend KOMBI with CAN-only speed, temperature, gear, ignition and wheel status;
  add per-ECU publication controls and a separate decoded CAN observer monitor.
- Preserve legacy profile/default callers and historical playback. Record and
  verify per-receiver references, gear results and profile snapshots; reconstruct
  live/saved views without physical fallbacks and reproduce the network on rerun.
- Add normal urban drive acceptance and API/CLI/browser regression coverage.
  This completes the local M5 scope; no new release or remote CI result is implied.

## Unreleased — M5 motion controls

- Add brake, P/R/N/D, longitudinal speed, coasting, directional caps and moving
  RPM coupling. Preserve the original parked throttle and physical engine controls.
- Reject unsafe gear requests with recorded results rather than aborting the run;
  validate and reserve whole API input batches with brake applied before gear.
- Record explicit physical speed samples and motion settings; playback restores
  samples without physics and rerun compares the full canonical stream.
- Add CLI/API/UI controls, a separate labelled physical-model panel, an original
  motion scenario, a screenshot and model/core/import/API/browser regression tests.
- Keep the EngineStatus DBC and CAN-only KOMBI RPM chain. Additional ECU, CAN
  speed/temperature, expanded DBC and full-network acceptance remain within M5.

## Unreleased — M5 physical engine controls (first pass)

- Add ignition OFF/ACC/ON/START and physical Start/Stop without suppressing Engine
  ECU publication; a stopped engine coasts to zero RPM with FRESH CAN reception.
- Route validated commands through the canonical core input phase, scenarios,
  recording/playback/rerun, CLI and API; reserve multi-input command quotas first.
- Add ignition/engine controls to the dashboard with an explicit physical-state
  label, independent Offline control and playback-disabled inputs.
- Add an original start/stop scenario, compatibility fixture from v0.1.0,
  focused regression tests and browser acceptance alongside the previous slice.
- M5 is not complete: additional ECU, brake/gear/speed, expanded DBC and normal
  urban drive are subsequent passes. No release or remote CI result is implied.

## Unreleased — v0.1.0 / M4 vertical slice acceptance

- Add repeatable Chromium acceptance of the production UI with real API/core,
  disposable recordings, exact freshness deadlines in playback and a fresh rerun.
- Include pause/reload snapshot recovery, reset and a mobile viewport in the pass.
- Add a browser CI job and retained video/trace/screenshot artifacts.
- Pin its runner to Ubuntu 22.04 so Chromium's sandbox works without changing
  host security settings; newer Ubuntu defaults reject the bundled browser.
- Document the clean quickstart, reproducible offline demo and acceptance evidence;
  include a short recording and screenshot from the real browser run.
- Set Python/UI package versions to 0.1.0. This prepares the local release;
  no release tag, GitHub Release or remote CI success is implied.

## Unreleased — M3 first working UI

- Cancel pending playback loads on return to live; ignore stale responses/errors
  so they cannot switch the UI back or interfere with a newer operation.

- Add a loopback FastAPI REST/WebSocket API over the deterministic headless core.
- Add a React/TypeScript/Vite dashboard with received KOMBI RPM/freshness, throttle,
  Engine ECU Offline, a bounded RPM chart and delivered CAN Monitor.
- Record complete manual runs from time zero; save/reset, verify playback with
  seeking and compute deterministic reruns through the existing trace contracts.
- Restore live state/history through reconnect snapshots; cap memory, connections,
  request bodies, run duration/count and commands, with explicit recording failures.
- Add `autocan studio` to build and serve the dashboard locally in one command,
  frontend lockfile/checks and API/UI tests. M4 release acceptance remains separate.

## Unreleased — M2 faults and reproducible traces

- Add publication-only Engine ECU Offline/Online and virtual KOMBI freshness
  deadlines that work while the bus is silent.
- Add fixed delay and independently seeded message loss for transport checks.
- Add bounded declarative JSON scenarios and original normal/offline examples.
- Stream frames/status, applied commands and fault truth into separate JSONL
  files with profile snapshot, provenance manifest and integrity/semantic hashes.
- Add `autocan run`, verified `playback`, and fresh-simulation `rerun`; reject
  overwrites, incomplete/corrupt runs, quota overruns and incompatible reruns.
- Preserve M1 headless controls; API/UI, Parquet and ML remain later milestones.

## Unreleased — M1 headless simulator

- Add integer virtual time with stable event phases and pause/step/reset controls.
- Connect a synthetic throttle/RPM model through Engine ECU, DBC and python-can
  VirtualBus to an independent KOMBI receiver.
- Separate immutable frames, per-receiver delivery records and observed CAN data.
- Add `autocan demo` and line-oriented `autocan shell` with JSON output.
- Cover deterministic 30-second runs, receiver isolation, transport limits, invalid
  inputs and CLI controls. Offline faults, freshness deadlines and recording/replay
  are not part of this milestone.

## 0.0.1 — M0 foundation

- Add a Python package, uv lockfile, lint/type checks, tests, and CI configuration.
- Define synthetic vehicle/ECU access boundaries and virtual-time/event contracts.
- Add the original DemoCar EngineStatus DBC with golden encode/decode coverage.
- Package the DBC so installed distributions load it independently of the cwd.

Simulation, ECU behavior, CAN transport, recording, CLI/API/UI, and ML are later
milestones. This entry does not represent a published release or a remote CI run.
