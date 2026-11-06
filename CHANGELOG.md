# Changelog

## Unreleased

### Added

- Five-ECU DemoCar network with eight DBC messages, per-signal freshness, ignition and engine controls, braking, and gear requests routed over CAN.
- Eight synthetic fault types, three built-in dashboard scenarios, manual injection and clearing, and a timeline shared by live monitoring and playback.
- Verified JSONL/Parquet exchange, receiver-specific observations with separate ground truth, and seeded dataset collections across six driving modes.
- Offline causal feature generation, whole-run splits, and evaluation of fixed rules, Isolation Forest, and Random Forest with validation-selected model thresholds.
- Held-out fault and normal-driving evaluations, event detection and false-alarm analysis, paired run-bootstrap intervals, and frozen inference measurements with a provenance check.
- Seeded [live ML demo](docs/LIVE_DEMO.md) with local Random Forest training, fault injection, and recorded-score playback.

### Changed

- Reorganized the public documentation around operating the simulator, its architecture and data contracts, and the measured offline evaluation. Historical verification records remain available through the documentation archive.

## [0.1.0] — 2026-10-30

### Added

- Deterministic throttle-to-RPM simulation with a synthetic Engine ECU, a packaged DemoCar DBC, a virtual CAN bus, and an independent instrument cluster.
- Headless controls and scenarios with publication-only Engine ECU faults, receiver freshness deadlines, and configurable delivery delay and loss.
- Verified JSONL recordings, playback, and deterministic reruns.
- Local FastAPI and React dashboard with vehicle controls, an RPM chart, a CAN monitor, and saved-run playback and rerun.
- Browser acceptance coverage and a recorded demonstration.

[0.1.0]: https://github.com/cryptoadya/autocan-studio/releases/tag/v0.1.0
