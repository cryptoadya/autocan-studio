# Changelog

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
