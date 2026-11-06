# Reproduce the live ML demo

[Watch the 20-second Studio recording](images/m11-live-ml.webm). It shows a
moving DemoCar, an ABS/DSC publication outage, separate fault and model-alert
tracks, and playback before and after the fault.

## Train a local demo model

Use Python 3.12, uv 0.13.6, and Node.js 22.12+ with npm. Start from the root of
a clean source checkout.

```sh
git clone https://github.com/cryptoadya/autocan-studio.git
cd autocan-studio
uv sync --locked
mkdir -p data/live-demo

uv run --locked autocan generate --output data/live-demo/source \
  --runs 12 --fault-runs 9 --modes acceleration \
  --fault-types ecu_offline signal_spike cross_signal_inconsistency \
  --min-duration 8 --max-duration 8 --seed 42
uv run --locked autocan prepare-ml data/live-demo/source \
  --output data/live-demo/prepared --split-seed 42 --window 0.1
uv run --locked autocan train-random-forest data/live-demo/prepared \
  --source data/live-demo/source --output data/live-demo/experiment --seed 42
```

Each command publishes to a new destination. Move the previous demo outputs
aside to repeat training. The commands generate 12 eight-second synthetic
runs, including nine fault runs, and prepare 960 causal windows. Training
produces the joint experiment. Studio loads its Random Forest and the
validation-selected threshold from the three local directories. Use bundles
you generated or otherwise trust. The dataset, model and recordings stay
under ignored `data/`.

This small recipe demonstrates the workflow. The
[frozen M9 evaluation](ML_EVALUATION.md) uses separately preserved inputs
and reports held-out metrics, false alarms and their limits.

## Drive, inject a fault, and seek

```sh
uv run --locked autocan studio --trusted-model \
  data/live-demo/experiment data/live-demo/prepared data/live-demo/source
```

Open [Studio on loopback](http://127.0.0.1:8000). The launcher installs the
locked frontend dependencies, builds the dashboard and serves it locally.

1. Click **Run demo** to start the network profile with seed **42**. With the
   vehicle stopped and the engine running, apply **Brake 100%**, select **D**,
   wait for the gear to engage, release the brake and set **Throttle 50%**.
2. In **Anomaly detection**, look for **Scoring active** and the score below
   the displayed threshold on the moving baseline. In **ECU network**, click
   **Stop publication** on the **ABS/DSC** card. Watch the score cross the
   threshold. The **Model alert** and **Injected fault** tracks mark the two
   events separately.
3. Click **Stop & save**, choose the run under **Recordings**, and click
   **Load playback**. Confirm **Recorded score** and the saved alert and
   fault tracks. Move **Playback position** to a time on the moving
   baseline before the fault to see the earlier score.

## Repeat the browser check

The test stages the driving controls before the baseline sample, injects
the fault in the UI, and checks save and seek. Playwright records the test
video under `frontend/test-results/`. The short clip above was captured
separately using the normal Studio launcher.

```sh
export npm_config_cache="$PWD/.cache/npm"
npm --prefix frontend ci --no-audit --no-fund
npm --prefix frontend run build
npm --prefix frontend run test:e2e:install
AUTOCAN_E2E_MODEL_ROOT="$PWD/data/live-demo" \
  npm --prefix frontend run test:e2e -- anomaly.spec.ts
```

## Observed on Apple M1

Verified from a clean clone of `b5f608dc3aec256be1d29058d27473e5156c8c40`
on Apple M1, macOS 27.2, Python 3.12.0, Node 22.14.0, and Chromium
156.0.8078.4. Generation, preparation and training took 11.43 s, 4.55 s
and 17.33 s wall time respectively, excluding dependency installation.
The three output directories used about 33 MiB together.

| Stage in the recorded demo | Observed |
| --- | --- |
| Moving baseline at 3.10 s virtual | Score 0.157, threshold 0.547, zero alerts |
| ABS/DSC publication stopped | Ground-truth fault at 4.26 s, model alert starts at 4.30 s virtual |
| Save at 8.05 s virtual | 6,499 canonical events and 80 score samples |
| Load playback | Score 0.927, with the same model provenance and alert state as live |
| Seek to 3.10 s virtual | Score 0.157, with zero fault markers and zero alerts |

The Random Forest SHA-256 in the capture was
`a8fb0792fc64c12c971158bc02d637992745c8e719aea7c591288dbbfd495ffc`.
Regenerating in fresh output directories on the same machine reproduced the
source and prepared-data semantic hashes. The Random Forest file, threshold
and saved score table were byte-identical. The experiment digests differed
because the metrics record the lineage of each set of regenerated files.

The fault and alert timestamps describe the recorded sequence. Interactive
timing can vary with control actions and browser pacing.
