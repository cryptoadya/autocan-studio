# AutoCAN Studio

A local-first, synthetic CAN laboratory with a deterministic simulator and an
independent anomaly-detection pipeline planned for later milestones.

**Current scope: M2 — offline faults and reproducible traces.** A synthetic throttle/RPM
model publishes EngineStatus through an Engine ECU and an in-process python-can
adapter. KOMBI decodes its received frames independently of physical state.
Engine publication can be disabled without stopping physical RPM. Independent
virtual timers expose stale/time-out data. JSON scenarios can be recorded,
verified, played back, and recomputed. API/UI, ML, and the expanded fault library
remain later milestones. This is not an OEM emulator or a physical vehicle diagnostic tool.

## Development setup

Use Python 3.12 (the tested minor version) and
[uv 0.13.6](https://docs.astral.sh/uv/getting-started/installation/).
The project supports Python >=3.12; additional versions are not yet verified.
`.python-version` selects 3.12 and `uv.lock` pins dependencies.

From the repository root:

```sh
uv sync --locked
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy
uv run --locked pytest
uv build --no-sources
```

No API keys, CAN hardware, frontend runtime, or local planning documents are
required. CI is defined in `.github/workflows/checks.yml` for pushes and PRs;
local checks do not imply a successful remote run for an unpushed branch.

## Headless demo

```sh
uv run --locked autocan demo --duration 1 --throttle 50 --seed 42
uv run --locked python -m autocan demo --duration 0.1 --throttle 50 --events
uv run --locked autocan shell --seed 42
```

Demo duration is in seconds; execution is faster than real time. The default
output is one JSON summary. With `--events`, canonical events stream to stdout
and the summary goes to stderr. This is a debug stream; use `run` below for a
complete recording. Events are not accumulated in memory by the simulator.
Physics is noise-free; controlled delivery loss uses its own seeded RNG stream.

The shell accepts one command per line and prints JSON state after each valid
command: `throttle PERCENT`, `run MILLISECONDS`, `offline`, `online`, `pause`, `resume`, `step`,
`reset`, `state`, `quit`. `run` is relative to the current virtual time and does
not advance while paused. `step` advances 10 ms even while paused, preserving
the pause setting. Reset creates a fresh run at time zero, clears queued
commands/events and transport, and restores 800 rpm / 0% throttle / unavailable
KOMBI data. It does not automatically repeat previously submitted commands.

The state output labels `physical_rpm` as model truth and `kombi_rpm` as the
received measurement. Future dashboards must use the latter. At 50% throttle
and 100 ms, both are 1000 rpm and six frames have been delivered (including the
initial frame at 0 ms). Between publications only physical RPM changes.

## Record, play back, and rerun a scenario

```sh
uv run --locked autocan run scenarios/engine_offline.json --output data/runs/engine-offline
uv run --locked autocan playback data/runs/engine-offline
uv run --locked autocan rerun data/runs/engine-offline
```

Use a new output directory each time: an existing run is never overwritten.
`scenarios/normal.json` is a second small synthetic example. JSON scenarios
accept only declared commands/parameters, up to 256 commands and 64 KiB; they
cannot execute Python. Times are integer nanoseconds on the 10 ms input grid.

The offline example starts at 800 rpm, applies 50% throttle, disables Engine ECU
publication at 100 ms, and enables it at 400 ms. The last pre-offline reception
is at 80 ms: KOMBI becomes STALE at 140 ms, TIMEOUT at 280 ms, and FRESH again
at 400 ms. Physical RPM continues increasing throughout.

Each run contains:

- `events.jsonl`: immutable frames, deliveries and ECU status changes;
- `commands.jsonl`: applied commands with their canonical times and order;
- `faults.jsonl`: offline intervals and actual drop markers, separate from observations;
- `profile.dbc`: the profile snapshot;
- `manifest.json`: configuration/initial/final state, versions, RNG seed derivation,
  provenance, artifact checksums and a semantic event hash.

Playback validates the complete recording and prints the saved stream, with no
physics execution. Rerun creates a fresh simulation from the applied command
log and compares canonical events and final state; it does not need the
original scenario file. Run IDs are excluded from the semantic hash. Rerun
requires compatible core/profile hashes and exact recorded Python/package
versions; a saved recording can still be played back if the current core differs.

Recording artifacts have a 16 MiB default/hard maximum (a lower `--max-bytes`
can be set, minimum 4096); manifest and JSONL line sizes are capped at 64 KiB.
Quota or run failures leave a failed/incomplete recording that playback/rerun
reject. A recording covers one fresh run; reset needs a new recording/directory.
Generated data stays under ignored `data/`.

For transport contract checks, a scenario may declare
`"transport": {"delay_ns": 30000000, "drop_probability": 0.4}`. Delay is fixed,
per-receiver delivery time changes without changing frame creation time, and
loss is deterministic for the seed. Previously published frames still arrive
after Offline. Variable jitter and a full fault editor are later features.

## DemoCar DBC

The original, MIT-licensed synthetic profile is packaged at
`backend/autocan/profiles/democar.dbc`. This single canonical copy is included in
the wheel and source distribution; it is not an extracted OEM DBC.

```python
from autocan.dbc import load_democar_database

message = load_democar_database().get_message_by_name("EngineStatus")
payload = message.encode({"EngineRpm": 800}, strict=True)
assert payload.hex() == "800c"
assert message.decode(payload)["EngineRpm"] == 800
```

Golden bytes, encoding boundaries, rounding, and decoding are covered by tests.
The profile specifies a synthetic CAN ID, not a real vehicle mapping.

## Contracts and next milestone

- [Architecture and access boundaries](docs/ARCHITECTURE.md)
- [Data, timing, and profile contracts](docs/DATA_CONTRACTS.md)
- [Milestone status](docs/ROADMAP.md)
- [Change history](CHANGELOG.md)

M3 adds a minimal local API and UI on top of this headless chain. The later
fault library, additional ECU, Parquet/dataset factory and ML remain separate
milestones; M2 does not implement them.
