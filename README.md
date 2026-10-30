# AutoCAN Studio

A local-first, synthetic CAN laboratory with a deterministic simulator and an
independent anomaly-detection pipeline planned for later milestones.

**Current scope: v0.1 — the first vertical slice (M0–M4).** A synthetic throttle/RPM
model publishes EngineStatus through an Engine ECU and an in-process python-can
adapter. KOMBI decodes its received frames independently of physical state.
The dashboard shows received RPM, CAN deliveries and freshness; Engine ECU Offline
stops publication while physics continues. Runs can be saved, played back and
recomputed. This is a synthetic laboratory, not an OEM emulator or a physical
vehicle diagnostic tool. Additional ECU, the fault library, datasets and ML remain
later milestones.

## Run the dashboard

Install Python 3.12, [uv 0.13.6](https://docs.astral.sh/uv/getting-started/installation/)
and Node.js 22.12+ with npm. From a fresh source checkout:

```sh
git clone https://github.com/cryptoadya/autocan-studio.git
cd autocan-studio
uv run --locked autocan studio
```

Open [AutoCAN Studio](http://127.0.0.1:8000). This command installs the locked
frontend dependencies, builds the UI, and serves it with FastAPI on the same
local origin. No API keys or CAN hardware are needed. Use `--port 8001` to choose
another local port. The launcher requires a source checkout; the Python wheel
still provides the headless CLI/API but does not bundle the frontend.

1. Click **Run demo**, move **Throttle**, then release it to apply the command.
2. Click **Take offline**: the received RPM is retained, then marked **STALE** and
   **TIMEOUT**. **Bring online** resumes publication. The engine keeps running.
3. **Pause** freezes virtual time. Commands entered while paused apply when resumed.
4. **Stop & save** finalizes the recording in ignored `data/runs/<run-id>/`.
5. Select the saved run, **Load playback**, and seek through saved virtual time.
   Playback disables live controls and pauses any active simulation.
6. **Deterministic rerun** starts a fresh headless computation and reports whether
   canonical events and the final state match. **Return to live** restores the live
   view; **Reset · new recording** saves the current run and creates a fresh one.

The dashboard uses KOMBI RPM, including when its last observation is stale. It
never substitutes physical model RPM. A reload or WebSocket reconnect restores
state and rolling history from a new snapshot. **Freeze** only freezes the CAN
Monitor display; the simulation and recording continue.

One active simulation is supported. Each API run starts paused and is recorded
from time zero, including manual inputs. Limits: 120 virtual seconds, 256 input
commands, 16 MiB per recording and 64 entries under `data/runs/`. Duration limits
save automatically; recording failures stop the run visibly, and command quotas
reject additional inputs explicitly. Stop and
archive local runs manually to free capacity; the app never deletes them.
The backend/frontend keep at most 200 deliveries and 400 graph points; the table
renders the latest 50 rows. Live updates are capped at 20 Hz. Under load wall-clock
execution may slow; integer virtual time and core calculations remain unchanged.
No real-time performance guarantee is claimed. Local v0.1 acceptance, its scope
and reproducible commands are recorded in [the acceptance report](docs/V0_1_ACCEPTANCE.md).
Release tags and GitHub publication are separate from local acceptance.

[![AutoCAN Studio browser demo](docs/images/m4-demo.gif)](docs/images/m4-demo.webm)

Click the animation for the full-resolution recording of the browser acceptance:
throttle, Offline, save, playback deadlines and a matching rerun. This is a
synthetic run; the recording is not a physical vehicle test.

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

The headless checks require no API keys, CAN hardware, frontend runtime, or local
planning documents. CI is defined in `.github/workflows/checks.yml` for pushes and PRs;
local checks do not imply a successful remote run for an unpushed branch.

For frontend checks:

```sh
npm --prefix frontend ci
npm --prefix frontend run typecheck
npm --prefix frontend test
npm --prefix frontend run build
```

The browser acceptance uses the production build and a real local API/core, with
disposable recordings. Install its pinned Chromium once, then run:

```sh
npm --prefix frontend run test:e2e:install
npm --prefix frontend run build
npm --prefix frontend run test:e2e
```

On Linux append `-- --with-deps` to the install command. The test starts its
own loopback server on port 8765; keep that port free. Videos and failure
screenshots/traces go into ignored `frontend/test-results/`. To produce a slower
demo recording, use `AUTOCAN_DEMO=1 npm --prefix frontend run test:e2e`.
Chromium's own sandbox stays enabled. On macOS, an application-registration
crash in a restrictive execution sandbox requires running the browser test from
a normal terminal or an approved execution environment.

CI runs Python checks, frontend checks and this Chromium acceptance separately.
It uploads browser artifacts; the status of a local run is not remote CI evidence.

For frontend development, serve the API on loopback port 8000 with
`uv run --locked uvicorn autocan.api:create_app --factory --host 127.0.0.1`
and run `npm --prefix frontend run dev` in another terminal. Vite proxies API/WS
requests to the backend. The single-command demo uses a static build instead.

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

M3 adds the local API/UI above the unchanged headless core. M4 remains the
release acceptance and packaging milestone; no v0.1 tag or release is implied.
The fault library, additional ECU, Parquet/dataset factory and ML remain separate
milestones.
