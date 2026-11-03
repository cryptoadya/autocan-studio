# Frozen-model inference performance — bounded M9 pass

`benchmark-inference` measures the existing three fixed window rules,
IsolationForest and RandomForest. It loads verified ID windows, saved
preprocessing, trusted local estimator pickles and frozen scores from the joint
RandomForest experiment. It never calls fit, selects thresholds, changes source
artifacts, generates datasets or evaluates detection quality. OOD inputs and
model-selection options are deliberately absent.

## Run against existing artifacts

```sh
uv run --locked autocan benchmark-inference data/ml/m9-id-seed42 --source data/datasets/m9-id-seed42 --experiment data/experiments/m9-rforest-seed42 --output data/benchmarks/m9-inference --windows 256 --warmups 2 --repetitions 5 --threads 1
```

Use a fresh output directory outside all input trees. Output publication is
exclusive and staged; validation or parity failures do not publish a result.
Only trusted local pickles may be used: hashes establish integrity, not trust.
Existing exact package/version, training-implementation, source lineage, schema,
coverage, label, scaling, split and saved-score checks remain mandatory. Both
model inventories are validated before deserializing the selected estimator.
No existing M9 metric, model, preprocessing file, dataset or threshold is edited.

The command emits `benchmark.json` (raw batch durations, run durations, metrics,
parity and environment), `report.md` (readable table and limitations), and
`manifest.json` (complete input-file hashes, implementation hash, Git provenance
and output checksums). The implementation hash covers the Python package source.
Output includes hardware model/chip/memory where available, logical CPU count,
OS/architecture, Python and package versions, clock, thread-pool configuration,
model/preprocessing/data hashes, ordered workload hash, configuration and UTC time.
Hardware collection excludes hostnames, serial numbers and UUIDs.

## Measurement contract

- Workload: the first N **eligible ID windows in persisted row order**, with no
  shuffling, filtering by class/quality, tuning or sampling based on scores.
  All input and saved-score streams are exhausted and verified before taking
  this prefix. Requesting more eligible windows than exist fails. N defaults to
  256, must be a multiple of 256, and is bounded at 4096. The same workload is
  used for every detector and scope. No partial batches or padding are used.
- Batches: 1 and 256, separately. Each detector × batch × scope runs in a fresh,
  sequential Python process. Rules have only the preprocessing/scoring scope;
  their estimator-only rows are explicitly not applicable. Worker PID and a
  unique invocation token are recorded (OS PIDs can be reused).
- Threads: fixed at one. Native thread environment variables and
  `PYTHONHASHSEED=0` are set **before child imports**; `threadpoolctl` applies
  runtime limits and records/validates every detected native pool. Arrow CPU and
  I/O pools use one thread. Saved forests must already have `n_jobs=1`; model
  parameters are never changed.
- Warm-up: 1–100 full, untimed workload sweeps (default 2), followed by 2–100
  measured sweeps (default 5), always in the same order. GC retains Python's
  default behavior. No per-detector adaptive repetitions or time-based stopping.
- Preprocessing + scoring: calls the existing forest `.score` method, including
  frozen transform, matrix construction, estimator call and Python score
  validation. Rules reconstruct the raw feature mapping and call the existing
  `baseline_window_scores` helper. **That helper computes all three rules**;
  each named-rule row includes the shared helper's other rule calculations.
  These numbers do not isolate individual rule arithmetic.
- Estimator-only: constructs matrices with the same frozen transform outside
  timing and invokes `-score_samples` for IsolationForest or `predict_proba` for
  RandomForest's class 1. Sign/class extraction remains timed; preprocessing
  and Python score validation are excluded. The original float64 input format
  and estimator internals remain unchanged. There is no distinct rule estimator.
- Wall-clock: `perf_counter_ns` surrounds each batch call. p50/p95 use linear
  interpolation at `(n−1)q` over all measured batch durations. **Batch latency is
  not per-window latency.** Throughput is measured windows divided by summed
  batch-call seconds; separate per-run elapsed values include loop/timer
  bookkeeping. Loading, verification, matrix preparation for estimator-only,
  parity, warm-up and output I/O are outside inference timing. Durations are
  retained so aggregation is inspectable.
- Parity: the exact workload/batch/scope is compared with verified frozen saved
  scores before warm-up and again after measurement, using absolute tolerance
  1e-12 and zero relative tolerance. Nonfinite, missing or changed scores fail.
  Parity performs no threshold selection and is never timed.
- Memory: worker process high-water RSS sampled after post-run parity, before
  result serialization, from
  `getrusage(RUSAGE_SELF).ru_maxrss`, normalized to bytes (Darwin: bytes;
  Linux: KiB × 1024). It includes imports, validation, the selected model,
  parity, warm-up, prepared matrices and scoring. It is **not incremental
  estimator allocation**. Sibling estimator trees are never deserialized in
  the same worker. Other operating systems are rejected. The parent and child
  launcher are excluded from each worker's RSS.

Input fingerprints are compared before and after all workers. No input root
may overlap the destination. Output is diagnostic performance evidence, not a
replacement for frozen detection metrics or a basis for choosing a model from
test/OOD results. There is no MLflow, UI, live inference or new dataset work.

## Local M1 observation and CI

The checked-in [M1 observation](benchmarks/m1-frozen-inference-2026-11-03/report.md)
contains the actual local measurements; [raw measurements](benchmarks/m1-frozen-inference-2026-11-03/benchmark.json)
and [provenance](benchmarks/m1-frozen-inference-2026-11-03/manifest.json) accompany it. It uses the
pre-existing 12-run synthetic acceleration fixture, not a new dataset. Its
prepared semantic hash is
`5ce5ddcb8eed4ae03ac928135c407fe89edf48d8cb4cd2f7cc14e7ae96735521`;
experiment semantic hash is
`0edcf143dc0a0d9a03716f0552d339eb2f65118897642dbb6ed37f1aa1210832`.
The model seed remains the saved 42. Performance settings are fixed before
measuring: 512 eligible windows, batches 1 and 256, 3 warm-up sweeps,
10 measured sweeps, and one thread. The run completed on Apple M1,
MacBookAir10,1, 16 GB RAM, macOS 27.2, arm64, Python 3.12.0; CI was false.
All 14 measured detector/batch/scope combinations matched saved scores exactly
(maximum absolute parity error 0). No source fingerprint changed.

For preprocessing plus scoring, IsolationForest p50/p95 batch latency was
3.112/3.918 ms at batch 1 (303 windows/s), and 10.897/10.989 ms at batch 256
(23,466 windows/s). RandomForest was 2.939/3.637 ms (323 windows/s) and
11.887/14.157 ms (20,882 windows/s), respectively. Across all workers, peak
process RSS ranged from 151.672 to 166.188 MiB. The linked table includes all
three rules and both estimator-only scopes. These are session observations,
not timing guarantees or detection-quality comparisons.

CI exercises correctness and a small subprocess smoke benchmark through
`tests/test_inference_benchmark.py`; it has no timing/RSS acceptance thresholds.
No CI timing observations are included in the M1 table. Remote CI status is
reported separately when available; local Linux-style unit conversion tests
are not Linux performance measurements.

The fixed synthetic prefix and repeated cache-hot calls do not establish timing
for every operating regime. Small batch-256 samples give unstable tail estimates.
OS scheduling, background load, power mode and thermal state remain uncontrolled.
Workload, configuration, hashes and procedure are reproducible; exact timings
are not promised across sessions or machines. Peak RSS is interpreter/workload
memory, not just model size. These measurements exclude CAN ingestion, DBC
decoding, causal feature-window construction, scheduling, threshold decisions
and alert delivery: **they are not full CAN-to-alert latency**. No real-vehicle,
live-inference or complete M9 acceptance claim follows.

## Commands used for this change

Executed from the repository root; the local `.venv` uses the saved models'
verified package environment. The pinned uv binary is installed in `/tmp` on
this machine. The cache override is required by the local filesystem sandbox.

```sh
.venv/bin/python -m pytest tests/test_inference_benchmark.py -q
.venv/bin/python -m pytest
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
UV_CACHE_DIR=/tmp/autocan-uv-cache /tmp/autocan-uv-0.13.6/bin/uv lock --check
UV_CACHE_DIR=/tmp/autocan-uv-cache /tmp/autocan-uv-0.13.6/bin/uv build --no-sources
git diff --check
```

The measurement command uses already existing artifacts and a new destination:

```sh
.venv/bin/autocan benchmark-inference /tmp/autocan-frozen-ood-review/frozen-ml-ood0/prepared --source /tmp/autocan-frozen-ood-review/frozen-ml-ood0/source --experiment /tmp/autocan-frozen-ood-review/frozen-ml-ood0/experiment --output docs/benchmarks/m1-frozen-inference-2026-11-03 --windows 512 --warmups 3 --repetitions 10 --threads 1 > /tmp/autocan-inference-m1-manifest.json
```

An initial uv lock check could not create the default cache inside the sandbox;
the writable-cache commands above resolved that environment failure.
Frontend/browser checks are not run for this backend benchmark-only change.
No remote CI or Linux performance result is claimed.

Final local verification: **484 tests passed in 502.13 seconds**, including all
23 new benchmark tests. Ruff lint and formatting, strict mypy, lockfile
validation, source/wheel builds and `git diff --check` passed. The earlier
standalone focused run passed 20 tests; the three additional regressions passed
in the final full-suite run. No remote CI run was started for this local branch.
The M1 capture records its pre-commit working-tree state (`git_dirty: true`)
and the exact package implementation hash, independently of the later commit ID.
