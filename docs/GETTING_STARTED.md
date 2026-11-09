# Getting started

## Dashboard

Install Python 3.12, uv 0.13.6, and Node.js 22.12+ with npm. Start Studio
from the root of a [source checkout](../README.md#quick-start).

```sh
uv run --locked autocan studio
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). Pass `--port 8001` for an
alternate local port. The launcher installs locked frontend dependencies and
builds the UI. The Python wheel provides the headless CLI and API. Dashboard
startup requires the frontend sources in the checkout. Use the
[live ML demo](LIVE_DEMO.md) to train and load a model from synthetic data.

- **Run demo** starts a fresh recording. Move and release **Throttle** to apply
  the request. **Take offline** suppresses Engine ECU publication so the cluster
  retains its last CAN RPM as it becomes stale and times out. **Stop engine**
  controls the physical model and leaves publication enabled.
- To move the car, apply at least 10% brake while stopped, select **D**, wait
  for the accepted gear, then release the brake and apply throttle. **R** gives
  negative speed. The model speed panel is separate from received CAN speed.
- **Pause** freezes virtual time. Queued commands apply when the run resumes.
  **Freeze** holds the monitor display while the run and recording continue.
- **Stop & save** finalizes the run. Select it and choose **Load playback** to
  seek through saved events. **Deterministic rerun** recomputes the run and checks
  that events and final state match. **Return to live** restores live controls.
- **Reset · new recording** saves the active run and starts fresh. Recordings
  are stored under ignored `data/runs/`. The dashboard limits each run to
  120 virtual seconds, 256 input commands, and 32 MiB of trace artifacts.
  Studio saves automatically at the duration limit. The run directory accepts
  up to 64 entries. Stop Studio and move completed runs outside the run
  directory to free slots. See the [recordings stress check](benchmarks/m1-recordings-2026-11-06/README.md#archive-local-recordings)
  for the archive commands and measured list behavior.

Use [Simulation and faults](SIMULATION.md) for built-in scenarios, timelines,
and the fault API.

## Headless CLI

```sh
uv run --locked autocan demo --profile democar-network --duration 1 --seed 42
uv run --locked autocan run scenarios/engine_offline.json --output data/runs/engine-offline
uv run --locked autocan playback data/runs/engine-offline
uv run --locked autocan rerun data/runs/engine-offline
```

Choose a new output directory for each recording. The offline scenario disables
Engine ECU publication at 100 ms and restores it at 400 ms. Its last pre-fault
reception is at 80 ms. KOMBI becomes stale at 140 ms, times out at 280 ms, and
returns to fresh at 400 ms. The physical engine continues running. The
[scenario catalog](../scenarios/) also includes engine start/stop, motion
controls, and a normal urban run exercising all five ECUs.

`autocan demo` prints a JSON summary. Add `--events` for a debug event stream
on stdout with the summary on stderr. `autocan shell --profile democar-network`
accepts line-oriented controls such as `throttle 50`, `run 100` (milliseconds),
`offline abs`, `state`, and `quit`. Use `autocan --help` to list commands.

Playback validates the saved artifacts and reads their events. Rerun requires
compatible core/profile hashes and recorded runtime versions. V2 accepts a
prior package version when those compatibility checks pass. Use the original
core and runtime to rerun recordings with older core hashes. See the
[recording contract](DATA_CONTRACTS.md#recording-and-reproduction) and
[Parquet and dataset guide](DATA_FORMATS.md) for storage and generation workflows.

## Tests

```sh
uv run --locked pytest

npm --prefix frontend ci
npm --prefix frontend run typecheck
npm --prefix frontend test
npm --prefix frontend run build
```

The browser suite runs the production frontend against the local API and core
on port 8765. Install its pinned Chromium before running it.

```sh
npm --prefix frontend run test:e2e:install
npm --prefix frontend run test:e2e
```

On Linux, add `-- --with-deps` to the install command if browser system
dependencies are missing. Browser artifacts are written under ignored
`frontend/test-results/`. Keep Chromium's sandbox enabled.
