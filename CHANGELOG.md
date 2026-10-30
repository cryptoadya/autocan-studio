# Changelog

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
