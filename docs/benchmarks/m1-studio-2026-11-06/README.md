# M11 Studio performance baseline on Apple M1

Measured on 2026-11-06 at Studio revision
`5c8d7b74f64240997ee8ec87ed381c5fe84b7fce` (main after M10).
This pass measures live M9 scoring near the trace size limit and
interactive playback of a saved recording. It also records what happens
when an unattended run reaches the size limit.

## Workload and environment

- MacBook Air `MacBookAir10,1`, Apple M1 (4 performance + 4 efficiency
  cores), 16 GB RAM, macOS 27.2 (26B123), arm64.
- CPython 3.12.0, scikit-learn 1.9.1, NumPy 2.5.4, PyArrow 26.0.0,
  FastAPI 0.162.0, Uvicorn 0.54.0 and websockets 17.4. Node 22.14.0,
  npm 10.9.2, Playwright 1.64.1 and Chrome 156.0.8078.80.
- One local Uvicorn process on loopback and one headless Google Chrome
  tab at 1440 × 900. The normal `studio` launcher builds and serves the
  production frontend. Measurements start after the server is ready.
- **Run demo** starts `democar-network` (`democar-0.2.0`) with simulation
  seed **42**. The vehicle idles in Park with the engine running. No
  driving controls or faults are applied. The configured live duration
  ceiling is **120 virtual seconds**. The separate trace budget is **16 MiB**
  across the JSONL streams and DBC, with the manifest outside that budget.
- Frozen M9 `random_forest`, training seed **42**, feature schema **1**,
  threshold **0.31373544410909504**. Model SHA-256
  `768317bb24af93fc6eadacb3c285458ab6a82e4750299fce6b52a1d566bd61f6`.
  Experiment manifest SHA-256
  `1f56eb832f69fb745fc4750d114bae8c3301d94bd94797e8c00aee1f67d148f5`.
  The [M9 acceptance record](../../archive/M9_ACCEPTANCE.md) identifies the
  trusted local experiment, prepared inputs and source collection.

## Results

### Unattended run

At **78.22 virtual seconds**, Studio stopped with `trace quota exceeded`.
The failed trace contained **63,403 canonical events** and **782 score
samples**. Budgeted files used **16,777,196 of 16,777,216 bytes**, leaving
20 bytes. The manifest reported `failed` with no end time, the saved-run
list was empty, and direct playback returned HTTP **409** with
`recording is not complete`. This workload reaches the byte quota well
before the duration ceiling.

### Saved run and playback

**77.05 s virtual** took **91.739 s wall time**
(0.840× wall-clock speed). The complete trace contains
**62,453 canonical events** and
**770 score samples**. Budgeted
files total **16,524,577 bytes (98.49% of the quota)**.
The manifest adds 8,042 bytes.

| Live metric | Observed |
| --- | ---: |
| WebSocket messages through save | 1,543 |
| Receive gap, p50 / p95 / max | 56.7 / 66.0 / 831.0 ms |
| JSON payload, p50 / p95 | 38,666 / 41,140 bytes |
| Skipped frames reported by server | 0 |
| Retained frames / signal samples / score samples | 200 / 400 / 240 |
| Server RSS before run / sampled max during live / sampled max during playback | 118.2 / 161.2 / 162.1 MiB |

Wall time runs from the browser automation's **Run demo** action to the
first saved WebSocket snapshot. Receive gaps use the automation client's
frame callbacks. Server RSS uses macOS `ps -o rss=`, sampled before the run,
every five seconds, at save, and after each playback operation.

| Playback action, five trials each | Median to UI confirmation | UI range | Median request to first byte |
| --- | ---: | ---: | ---: |
| Load end | 2.364 s | 2.338–2.735 s | 2.315 s |
| Seek to 60 s | 2.212 s | 2.204–2.219 s | 2.190 s |
| Seek to 76 s | 2.323 s | 2.313–2.493 s | 2.304 s |

The first full load took 2.735 s to UI
confirmation. UI timing includes Playwright dispatch and confirmation of
the displayed position and score. Request-to-first-byte uses the browser
Resource Timing API. All 15 requests returned HTTP 200. The full playback
anomaly snapshot matched live, including model provenance, the last
score (0.446215), and one grouped alert. Seek
responses contained only scores through the requested time. Chrome reported
no page or console errors.

## Repeat the capture

Use macOS with Google Chrome installed and the three original trusted M9
directories. Keep the benchmark driver from this report while running the
measured Studio revision in a separate checkout. The original artifacts are
local inputs to the CLI and must be obtained from the preserved M9 bundle.

```sh
driver="$PWD/docs/benchmarks/m1-studio-2026-11-06/measure.mjs"
m9="$PWD/data/recovered/m9-pr17-pytest272"  # or absolute path to the preserved bundle
work=$(mktemp -d /tmp/autocan-m11.XXXXXX)
git worktree add --detach "$work/studio" 5c8d7b74f64240997ee8ec87ed381c5fe84b7fce
cd "$work/studio"
uv sync --locked --python 3.12.0
npm --prefix frontend ci --no-audit --no-fund

mkdir "$work/capture"
ln -s "$PWD/frontend" "$work/capture/frontend"
node "$driver" "$PWD" "$work/capture" "$m9"
```

The driver calls `.venv/bin/autocan studio --port 18765 --trusted-model
EXPERIMENT PREPARED SOURCE` in the fresh capture directory. Reserve that
loopback port for the measurement. The browser opens Studio, starts
**Run demo**, and clicks **Stop & save** when the UI first shows at least
77.00 virtual seconds. The exact stop time depends on browser and server
pacing. It loads the end of the recording and seeks to 60 s and 76 s,
five times in that order. The driver checks the displayed
time and score at each position, and compares the full-duration playback
score and alert state with the final live snapshot.

`measurement.json`, `studio.log` and `data/runs/` are written below the
capture directory. The committed [measurement](measurement.json) contains
the timings, RSS samples, stream counts and digests, along with the
unattended run outcome. The full traces and model inputs stay local.

### Repeat the unattended quota check

From the measured checkout, after the playback capture exits, start the
same launcher in a new directory. Open `http://127.0.0.1:18765` in Chrome,
click **Run demo**, and leave it running until the quota error appears.

```sh
studio="$PWD"
quota=$(mktemp -d /tmp/autocan-m11-quota.XXXXXX)
ln -s "$studio/frontend" "$quota/frontend"
(cd "$quota" && "$studio/.venv/bin/autocan" studio --port 18765 \
  --trusted-model "$m9/experiment" "$m9/prepared" "$m9/source")
```

In another terminal, query the failed simulation and its playback response.

```sh
base=http://127.0.0.1:18765
current=$(curl -fsS "$base/api/simulations/current")
printf '%s\n' "$current" | jq '{id, sim_time_ns, recording, error}'
curl -fsS "$base/api/runs"
id=$(printf '%s\n' "$current" | jq -r .id)
curl -si "$base/api/runs/$id/playback"
```

## Scope

Each attempt starts with an empty run directory and uses the same
synthetic idle workload. Playback trials share the same browser, server and file
cache. RSS is sampled for Studio's Python server process and excludes
Chrome, npm and other processes. Sampling can miss transient peaks. These
measurements do not establish real-time guarantees, memory stability over
multiple sessions or behavior at the command or connection quotas.
