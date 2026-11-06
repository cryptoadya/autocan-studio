# M11 recordings stress check on Apple M1

Measured at `c5bdd8d674931575a56ec378e058d52538bf2803` on the Apple M1
MacBook Air with 16 GB RAM, macOS 27.2, CPython 3.12.0 and Node 22.14.0.
Studio ran with the frozen M9 Random Forest and the browser used a single
headless Chrome tab on loopback. Simulation seed was 42.

## Workload

- Copy a verified 120-second network recording into 63 UUID-shaped directories
  in an isolated run root. The source is the saved trace from the
  [M1 playback benchmark](../m1-studio-2026-11-06/README.md).
  The copies have distinct directory IDs and identical trace content.
- Open Studio, wait for all 63 entries in **Saved recording**, and measure
  the browser request and time to display. Create and save the 64th run.
  Try to start one more and check the HTTP response and directory count.
- Shut Studio down and move 63 directories to a sibling archive on the same
  volume. Verify one archived long trace with CLI playback. Restart Studio
  and open the list with one remaining recording.
- In that same restarted server process, perform ten sequential API sessions
  with live scoring, targeting two virtual seconds before stopping. For each,
  verify full playback and a seek to 0.50 s. Check that the list exposes all
  newly saved recordings and sample server RSS at the end of each cycle.

## Results

| Browser startup workload | Navigation to displayed list | Request to first byte | Response body |
| --- | ---: | ---: | ---: |
| 63 long recordings | 82.527 s | 82.338 s | 4,348 bytes |
| One long recording after archive and restart | 1.582 s | 1.441 s | 70 bytes |

The 63 copied traces occupied 1.51 GiB
in logical file sizes. Most of the delay occurred before the first byte of
`GET /api/runs`. The server verifies each eligible trace sequentially before
returning the list, and the frontend waits for that list during startup.

The 64th run saved and played back. The next creation request returned
**HTTP 409** with `run quota reached (64)`, and the directory stayed at 64
entries. Moving 63 directories to the archive took 6.268 ms
for the same-volume renames, excluding shutdown, verification and restart.
The move took 1.49 GiB of logical files out
of the active root. CLI playback of one archived trace succeeded.

All ten post-archive cycles saved, loaded and sought successfully, and the
final list contained **11 recordings**. Each new run recorded 20 score
samples. Create-to-save wall time had a median of 2.574 s
(range 2.457–2.651 s). In the restarted server,
RSS was 129.7 MiB after the initial
list load, reached 167.5 MiB at the sampled cycle endpoints, and
ended at 167.2 MiB after the final list
and a one-second wait.

The [measurement](measurement.json) includes the Chrome version, model
and trace hashes, list timings and per-cycle samples.

## Repeat

Use the three trusted frozen M9 inputs and the complete 120-second recording
from the [M1 capture instructions](../m1-studio-2026-11-06/README.md#follow-up-at-the-32-mib-studio-limit).
Start from a source checkout with Python 3.12, uv 0.13.6, Node.js 22.12+, npm
and Google Chrome. Run from the repository root with port 18767 free.

```sh
uv sync --locked
npm_config_cache="$PWD/.cache/npm" npm --prefix frontend ci --no-audit --no-fund

fixture=/absolute/path/to/the/complete-120-second-run
m9="$PWD/data/recovered/m9-pr17-pytest272"
work=$(mktemp -d /tmp/autocan-m11-recordings.XXXXXX)
node docs/benchmarks/m1-recordings-2026-11-06/measure.mjs \
  "$PWD" "$work/capture" "$fixture" "$m9"
```

The driver requires the capture path to be new. It invokes the normal Studio
launcher in that directory and writes `studio.log` and `measurement.json`.
Allow roughly 2 GiB for copied data and local build/cache artifacts.
The driver retains both the live run directory and the archive for inspection.

## Archive local recordings

Stop Studio before moving saved runs. Move the completed directories you want
to retain outside the active run root, then restart Studio. For a single
completed run, use its directory name in the following commands.

```sh
run_id=YOUR_SAVED_RUN_ID
mkdir -p data/archive
mv -n "data/runs/$run_id" data/archive/
uv run --locked autocan playback "data/archive/$run_id" >/dev/null
```

Restart Studio after the move. The archive remains available to the CLI.
A same-volume move frees run slots. Reclaiming space on the Studio volume
requires moving the archive to a different volume or removing files there.

## Scope

The two browser list loads are single observations from fresh pages and
separate server starts. Browser timings exclude Studio startup. The
63-recording case intentionally repeats one long synthetic trace, so OS
cache and storage behavior can differ for independent recordings. The
session cycles use one API client and short scored runs. Sampled RSS
describes this sequence and excludes the browser. It cannot establish
long-term memory stability or concurrent-client behavior.
