# AutoCAN Studio

A local-first, synthetic CAN laboratory with a deterministic simulator and an
independent anomaly-detection pipeline planned for later milestones.

**Current scope: M1 — deterministic headless simulator.** A synthetic throttle/RPM
model publishes EngineStatus through an Engine ECU and an in-process python-can
adapter. KOMBI decodes its received frames independently of physical state.
Fault injection, freshness timers, trace recording/replay, API/UI, and ML remain
later milestones. This is not an OEM emulator or a physical vehicle diagnostic tool.

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
and the summary goes to stderr. This is a debug stream, not the M2 trace format.
Events are not accumulated in memory by the simulator. Seed is retained as run
metadata; M1's noise-free physics consumes no randomness.

The shell accepts one command per line and prints JSON state after each valid
command: `throttle PERCENT`, `run MILLISECONDS`, `pause`, `resume`, `step`,
`reset`, `state`, `quit`. `run` is relative to the current virtual time and does
not advance while paused. `step` advances 10 ms even while paused, preserving
the pause setting. Reset creates a fresh run at time zero, clears queued
commands/events and transport, and restores 800 rpm / 0% throttle / unavailable
KOMBI data. It does not automatically repeat previously submitted commands.

The state output labels `physical_rpm` as model truth and `kombi_rpm` as the
received measurement. Future dashboards must use the latter. At 50% throttle
and 100 ms, both are 1000 rpm and six frames have been delivered (including the
initial frame at 0 ms). Between publications only physical RPM changes.

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

M2 will add ECU Offline, independent STALE/TIMEOUT timers, JSONL recording,
playback, and deterministic scenario rerun. M1 provides deterministic stepping
and event observation without claiming those later features are complete.
