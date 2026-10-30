# v0.1 vertical slice acceptance

Local acceptance on 2026-10-30 covers M0–M4. Python and UI package versions are
0.1.0; no Git tag, published GitHub Release or remote CI result is claimed here.
The simulator uses an original synthetic DemoCar profile and no vehicle hardware.

## Contract evidence

| Required contract | Executed checks |
| --- | --- |
| State isolation | `test_simulation.py`, `test_faults.py`, `test_api.py`: physical RPM continues changing after Offline; KOMBI retains its last delivered measurement. Browser acceptance checks retained RPM and unchanged publication count while time advances. |
| Silent-bus timers | `test_faults.py`: exact +60 ms STALE and +200 ms TIMEOUT deadlines, including delivery exactly at a deadline and invalidated old timers. Browser playback checks +50/+60/+200 ms from the last reception. |
| Delivery timing | `test_can_chain.py`, `test_faults.py`: immutable frames, distinct receiver times without float conversion, pending delayed delivery survives Offline. |
| Deterministic rerun | `test_clock.py`, `test_simulation.py`, `test_traces.py`: stable ordering, pause/reset and command boundaries, saved-log rerun. Browser saves a manually controlled run and obtains a matching fresh computation. |
| Independent randomness | `test_faults.py`, `test_traces.py`: repeatable loss/delay with unchanged physical results when loss parameters change. |
| DBC validation | `test_democar_dbc.py`, `test_can_chain.py`: explicit errors for invalid/nonfinite/out-of-range input; representable anomalies and golden payloads round-trip. |
| End to end | `frontend/e2e/vertical-slice.spec.ts`: browser input -> real API/core -> Engine ECU -> CAN delivery -> KOMBI display, Offline/TIMEOUT, save, verified playback and deterministic rerun. No API/core mocks. |

The browser also checks pause/reload snapshot recovery, disabled live controls
during playback, reset, no page errors and no horizontal page overflow at 390 px.
Exact freshness deadlines are checked in saved virtual time rather than relying
on the browser catching a brief wall-clock interval. Proof that physical RPM
changes independently comes from core/API tests; physical RPM is excluded from
the dashboard API by design.

## Executed commands and results

```sh
uv sync --locked
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy
uv run --locked pytest
uv build --no-sources
npm --prefix frontend ci
npm --prefix frontend run typecheck
npm --prefix frontend test
npm --prefix frontend run build
npm --prefix frontend run test:e2e:install
npm --prefix frontend run test:e2e
```

Python checks: 94 tests passed; lint, formatting and strict mypy passed.
Frontend: 9 tests passed; typecheck and production build passed. Chromium:
1 complete acceptance test passed with the browser sandbox enabled. Source and
wheel distributions built successfully. The local development environment used
Python 3.12.0, Node 22.14.0, npm 10.9.2 and uv 0.13.6.

A second source copy contained only Git-eligible project files, with no `.git`,
virtual environment, `node_modules`, local data, AGENTS.md or local master-plan.
`uv sync --locked` created a new environment (Python 3.12.8); then
`uv run --locked autocan studio --port 8766` installed frontend dependencies,
built and served the UI. Both `/` and `/api/health` returned HTTP 200. Browser
acceptance passed on the same clean copy using its disposable test server.
This verifies a clean source-copy launch, not an installation from a published tag.

On this macOS host, browser processes ran in an approved execution environment
outside the restrictive agent sandbox; Chromium's own sandbox remained enabled.
Linux Chromium CI is configured in `.github/workflows/checks.yml` but has not
been run remotely for this branch. Local results do not establish Linux results.

## Reproduce the engine-offline demo

```sh
uv run --locked autocan run scenarios/engine_offline.json --output data/runs/v01-offline
uv run --locked autocan playback data/runs/v01-offline
uv run --locked autocan rerun data/runs/v01-offline
```

Choose a new output directory if it already exists. The original synthetic
scenario uses seed 42, 50% throttle, Offline at 100 ms and Online at 400 ms.
Last pre-offline reception: 80 ms; STALE: 140 ms; TIMEOUT: 280 ms; FRESH again:
400 ms. The run finishes at 600 ms with 16 publications/deliveries and 2000 rpm.
Playback validation and rerun both succeeded in the development and clean-copy
environments; rerun reported 41 canonical events and `matches: true`.

Both executions produced this semantic SHA-256 (run identity/provenance excluded):

```text
0d6bae00cc964c40627a39fcbbac28367fe0501061379d83cb4283ea2ec51c5c
```

Rerun requires the exact recorded Python/package versions and compatible core,
so reproduce and rerun within the same environment. Matching semantic hashes
across these two Python patch versions do not relax that compatibility policy.
Generated recordings remain local and ignored.

## Visual demonstration and limits

[Browser demo GIF](images/m4-demo.gif), [full-resolution video](images/m4-demo.webm)
and [playback/rerun screenshot](images/m4-playback-rerun.png) come from the passing
clean-copy browser run. The recording shows manual controls, saved-time deadline
checks and a matching rerun; its action timings are not the fixed CLI scenario.
Regenerate it with `AUTOCAN_DEMO=1 npm --prefix frontend run test:e2e`; videos and
screenshots are written to ignored `frontend/test-results/`.

This acceptance establishes the bounded one-signal vertical slice. It does not
establish real CAN timing, OEM behavior, a real-time performance guarantee,
other ECU/controls, additional faults, datasets, Parquet or ML accuracy.
