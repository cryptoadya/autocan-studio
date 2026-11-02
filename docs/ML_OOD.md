# Frozen model OOD evaluation — M9 sixth pass

`evaluate-ml-ood` scores the two [frozen OOD domains](OOD.md) with the
saved [joint forest experiment](RANDOM_FOREST.md) and three fixed rules.
It never fits an estimator or preprocessor, selects a threshold, changes a
split, or scores the ID test again. Both models retain their independent
validation-selected `score >= threshold` operating points.

## Evaluate existing artifacts

All five inputs must already exist: the original ID Factory collection, its
prepared windows, the trusted joint forest experiment, an ID rule benchmark,
and the frozen OOD suite bound to that benchmark. The source and feature
configuration must agree exactly. A new output directory is required.

```sh
uv run --locked autocan evaluate-ml-ood data/ml/m9-id-seed42 data/datasets/m9-ood --source data/datasets/m9-id-seed42 --reference data/benchmarks/m9-ood-reference --experiment data/experiments/m9-rforest-seed42 --output data/experiments/m9-ood-results
```

There are no seed, threshold, split, window or training options. The sole
optional flag is `--max-bytes`: 16 MiB by default, at most 64 MiB. The input
experiment must use the joint version-1 format; the standalone flat IF bundle
is not accepted as a substitute for the two-model experiment.

The loader first validates the prepared data against its original source and
the suite against its frozen reference, including cross-domain identities,
scenario/observer hashes and actual held-out types/families. It requires the
same feature configuration in the preparation, experiment and OOD protocol.
It then verifies the complete experiment inventory, checksums, saved split,
lineage, model/evaluator versions, training-code fingerprint and exact recorded
package environment. Both models' JSON metadata, preprocessing scopes and
threshold policies must agree with the saved ID experiment before either
pickle is deserialized. Fitted estimator types, parameters and feature counts
are checked after loading. No ID predictions or validation search are rerun.

These checks establish integrity and compatibility, not authenticity or the
history of an experiment. **Use only pickles from your trusted local training
process**: pickle loading can execute code. This CLI adds no model-import API
or live-inference endpoint.

## Metrics and audit artifacts

The original observer stream is replayed through the unchanged causal feature
pipeline. Only numeric features reach the frozen transformations/estimators;
keys, domain, mode and fault labels are never appended to the model input.
Each run's scores finish before its separate fault truth is projected.
Warm-up, missing masks and partial-tail handling match M9 preparation.

- `metrics.json` has ten common window rows (five detectors × two domains),
  separate per-fault-type window rows, PR curves and binary confusion counts.
  Fault-type rows include all eligible windows of runs with that intervention,
  including their negative segments; they are not type-classification scores.
- `normal_false_alarms` reports false-positive windows, alarm episodes, eligible
  normal exposure and episodes/hour. An episode is a contiguous sequence of
  positive eligible windows within one run, with no debounce or cross-run merge.
  All five detectors use that same policy. Exposure excludes warm-up and the
  partial tail. These counts differ from M8's latched, target-aware episodes.
- Normal OOD has no positive truth: recall/AP are undefined. Neither domain is
  pooled with ID or the other domain. No model is selected using these results.
- `features.parquet`, `windows.parquet`, `labels/windows.parquet` and
  `scores.parquet` retain separately aligned raw features, keys, truth and five
  frozen scores/decisions per window, including excluded warm-up for audit.
- `protocol.json`, `split.json` and both models' JSON/preprocessing/threshold
  records are copied byte for byte. Model pickles remain in the original bundle.
  `report.md` provides the comparison and normal false-alarm tables.
- `manifest.json` records hashes for all inputs and outputs, implementation,
  environment and Git provenance; `models_trained`, `thresholds_selected` are
  empty and `id_test_scored` is false.

The existing 100,000-window limit applies across both domains. Scoring batches
contain at most 256 rows; replay buffers at most one bounded run's features,
and AP retains bounded keys/scores. Quota/validation errors clean staging and
publish no destination. Existing outputs are refused. As with the other staged
publishers, a crash during file publication can leave an incomplete destination;
only a complete manifest identifies a finished result. Inputs are never rewritten.

## Observed saved-model evaluation, 2026-11-02

The PR #17 model artifacts were recovered from the previous local pytest run
and preserved with their exact source/preparation under
`data/recovered/m9-pr17-pytest272/`. The models were **not retrained**. They use
the documented 12-run, acceleration-only software fixture (seed 12, eight
seconds/run, three ID fault types, 100 ms windows, model seed 42).

The earlier M8 suite was incompatible: it belonged to an 18-run ID collection
and 500 ms windows. With explicit authorization, a compatible ID rule reference
and a new OOD suite were frozen once, before scoring: seed 43, six fault runs and
six fault-free normal runs. No recipe/seed retry or threshold adjustment followed
the results. The fault domain covers message delay, drop, frequency anomaly,
bias and stuck on the ID-known acceleration family. Normal OOD covers idle,
stop-and-go, city, cruise and braking. Each domain contains 456 eligible windows;
fault OOD has 87 positive windows, and normal exposure is only **45.6 seconds**.

| Detector | Threshold | Fault recall | Fault F1 | Fault AP | Normal FP windows | Normal alarm episodes | Normal FA/hour |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Heartbeat timeout | >= 1 | 0.022989 | 0.044944 | 0.523066 | 0 | 0 | 0 |
| RPM derivative | > 20000 | 0.045977 | 0.087912 | 0.357637 | 0 | 0 | 0 |
| Wheel residual | > 5 | 0 | 0 | 0.190789 | 0 | 0 | 0 |
| IsolationForest | >= 0.6411999799881782 | 0.160920 | 0.200000 | 0.192548 | 50 | 15 | 1184.21 |
| RandomForest | >= 0.31373544410909504 | 0.850575 | 0.902439 | 0.962716 | 300 | 10 | 789.474 |

RF detects many unseen-fault windows in this small fixture, but flags 300/456
unseen-normal windows (65.8%). IF flags 50/456 (11.0%). RF's fewer normal
episodes therefore do not mean fewer false-positive windows: its alarms last
longer. Hourly values are exposure-normalized observations from 45.6 seconds,
not measured hour-long rates or operational guarantees. Both models fail this
normal-family shift despite RF's strong fault score. Rules' zero false alarms
come with low fault recall at their fixed operating points.

| Held-out fault | Positive windows | IF recall | RF recall |
| --- | ---: | ---: | ---: |
| message_delay | 12 | 0.583333 | 1 |
| message_drop | 16 | 0.125 | 0.9375 |
| message_frequency_anomaly | 29 | 0 | 0.965517 |
| signal_bias | 14 | 0.214286 | 0.214286 |
| signal_stuck | 16 | 0.125 | 1 |

These are synthetic software-fixture observations, not broad generalization or
real-CAN quality. Parameters were not changed in response. Target-aware fault
event matching/latency, uncertainty and runtime studies, live inference and
final M9 acceptance remain separate work.

### Exact local inputs and reproduction

| Identity | SHA-256 |
| --- | --- |
| Prepared semantic | `5ce5ddcb8eed4ae03ac928135c407fe89edf48d8cb4cd2f7cc14e7ae96735521` |
| Saved experiment semantic | `4f59336939a03b45a485862e410ea846b6bcfed3c3230a193b1db7dc7935b247` |
| IF model.pkl | `e559d9bb40aa3c28a585158584a25c320de38ad28854b06b44b7d90ff9e24999` |
| RF model.pkl | `768317bb24af93fc6eadacb3c285458ab6a82e4750299fce6b52a1d566bd61f6` |
| Frozen OOD suite semantic | `5c6b93c8d7475e9aa3a77a076df8c23096f7a687f84fa4cc6810f2be5c9e4b37` |

With those preserved local inputs, use a fresh output path:

```sh
uv run --locked autocan evaluate-ml-ood data/recovered/m9-pr17-pytest272/prepared data/recovered/m9-pr17-pytest272/ood-suite --source data/recovered/m9-pr17-pytest272/source --reference data/recovered/m9-pr17-pytest272/reference --experiment data/recovered/m9-pr17-pytest272/experiment --output data/experiments/m9-frozen-ood-reproduced
```

The archived result is `data/experiments/m9-frozen-ood-pr17-seed43-final/`.
Local datasets, pickles and generated result directories remain ignored by Git.
Exact reproduction requires those same inputs and compatible recorded versions:
Python 3.12.0, NumPy 2.5.4, scipy 1.18.1, scikit-learn 1.9.1, PyArrow 26.0.0,
joblib 1.6.0, threadpoolctl 3.7.0, cantools 45.1.2 and python-can 4.6.1.
Within that environment, repeated evaluations are checked for byte-identical
artifacts while Git provenance is unchanged. No cross-version/platform bitwise
guarantee is made; the tables above round metrics to six decimal places.
