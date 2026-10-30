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
- M3 API/UI, Parquet/dataset factory, extended faults and ML are not implemented.
