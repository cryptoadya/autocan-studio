# Changelog

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
