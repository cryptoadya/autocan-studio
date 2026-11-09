# Changelog

## 2.0.0

### Simulation and recordings

- Drive a synthetic five-ECU DemoCar, inspect receiver-specific CAN measurements and freshness, and run built-in scenarios or inject faults from the dashboard.
- Automatically save Studio runs at their duration limit, up to 120 virtual seconds within the 32 MiB trace budget. Seek through verified JSONL recordings, exchange traces through Parquet, and recompute compatible runs with deterministic rerun.
- Keep the live controls available while Studio verifies and loads the saved-recording catalog in the background. Large catalogs can still take time to list.

### Anomaly detection

- Train or load a trusted local Random Forest to score observer CAN traffic live. Inspect scores and debounced alerts beside injected fault markers, and replay the saved scores and model provenance.
- Generate seeded receiver datasets across six driving modes. Compare three fixed rules, Isolation Forest, and Random Forest on causal windows using whole-run splits and frozen validation thresholds.
- Evaluate held-out faults, unfamiliar normal driving, detection delays, false alarms, and run-level uncertainty. Reproduce the workflow with the [live ML demo](docs/LIVE_DEMO.md) and consult the [frozen results](docs/ML_EVALUATION.md).

### Installation and compatibility

- Install the Python CLI from the source distribution or wheel. Launch the dashboard from a source checkout with Python 3.12, uv 0.13.6, and Node.js 22.12+ with npm.
- Earlier recordings remain playable when their format is supported. Rerun and trusted pre-release models require matching code, data or profile lineage, and runtime dependencies.
- Evaluation uses synthetic data only. The frozen Random Forest produced 10 false-alarm episodes over 45.6 seconds of unfamiliar normal driving. The release makes no claim about performance on physical vehicles.

## [1.0.0] — 2026-11-06

- Introduced the five-ECU DemoCar network, interactive fault injection and verified recordings, Parquet trace exchange, and the seeded Dataset Factory.

## [0.1.0] — 2026-10-30

### Added

- Deterministic throttle-to-RPM simulation with a synthetic Engine ECU, a packaged DemoCar DBC, a virtual CAN bus, and an independent instrument cluster.
- Headless controls and scenarios with publication-only Engine ECU faults, receiver freshness deadlines, and configurable delivery delay and loss.
- Verified JSONL recordings, playback, and deterministic reruns.
- Local FastAPI and React dashboard with vehicle controls, an RPM chart, a CAN monitor, and saved-run playback and rerun.
- Browser acceptance coverage and a recorded demonstration.

[0.1.0]: https://github.com/cryptoadya/autocan-studio/releases/tag/v0.1.0

[1.0.0]: https://github.com/cryptoadya/autocan-studio/releases/tag/v1.0.0
