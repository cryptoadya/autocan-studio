# Common window evaluator — M9 second pass

`autocan.window_evaluation` evaluates binary anomaly scores from rules or future
models through the same Python API: precision, recall, F1, binary confusion
counts, Average Precision (AP) and a precision–recall curve. This pass trains no
models, selects no thresholds and adds no ML dependencies or CLI/UI. M8 episode
benchmarks and frozen OOD formats retain their existing behavior.

## Inputs and alignment

Detector inputs, scores and ground truth remain separate:

- `WindowKey(row_index, run_id, split, start_ns, end_ns, eligible)` carries
  metadata from M9 `windows.parquet`. These fields never enter model features.
- `WindowTruth(key, label)` carries the separate boolean/null intervention label
  from `labels/windows.parquet`. The [preparation contract](TRAINING_DATA.md)
  still defines projection and warm-up: eligible labels are boolean, warm-up
  labels are null. This evaluator does not relabel windows or add matching grace.
- `WindowScore(key, score)` carries the score. **Higher means more anomalous**;
  future estimators with the opposite convention must invert scores first.
  Scores need not be probabilities or nonnegative.

Iterables require exactly the same keys in the same order, including warm-up
and unselected partitions. Unequal lengths, changed keys, duplicate row indices
or run/window identities, and a run assigned to multiple splits fail. Missing
eligible scores fail instead of removing difficult windows. Warm-up scores may
be null; supplied scores and thresholds must otherwise be finite numbers.

`evaluate_windows` takes one explicit `split`, `model_id` and previously frozen
`threshold`. It validates all alignment, then pools only that split's eligible
windows, rather than averaging per-run ratios. Results include input, selected,
warm-up-excluded, evaluated, positive and negative window counts. The default
and hard maximum are 100,000 input windows, including excluded rows;
`max_windows` can lower this limit. Iterators are accepted; ranking uses bounded
O(N) storage and O(N log N) sorting.

```python
from autocan.window_evaluation import WindowKey, WindowScore, WindowTruth, evaluate_windows

keys = [WindowKey(i, "demo", "validation", i * 100, (i + 1) * 100) for i in range(4)]
truth = [WindowTruth(k, y) for k, y in zip(keys, [False, True, True, False], strict=True)]
scores = [WindowScore(k, s) for k, s in zip(keys, [0.1, 0.4, 0.35, 0.8], strict=True)]
metrics = evaluate_windows(
    truth,
    scores,
    model_id="demo-scores",
    split="validation",
    threshold=0.4,
)
assert metrics["confusion_matrix"]["counts"] == [[1, 1], [1, 1]]
assert metrics["precision"] == metrics["recall"] == metrics["f1"] == 0.5
```

The library consumes trusted, aligned in-memory rows. Callers joining saved
tables must first verify artifact/source/split lineage, schemas, feature order
and row indices; a key cannot authenticate a dataset. The
[third M9 pass](SAVED_WINDOW_RULES.md) adds source-bound artifact validation and
the `evaluate-rules` report CLI. Integration coverage
uses real freshly prepared Parquet rows, all three saved partitions and warm-up.

## Metrics

Anomaly is positive. Confusion matrix rows are **true** labels and columns are
**predicted** labels, both ordered `[normal, anomaly]`: `[[TN, FP], [FN, TP]]`.
`comparison="ge"` (default) means score >= threshold; `"gt"` means score >
threshold. Precision = TP/(TP+FP), recall = TP/(TP+FN), F1 =
2TP/(2TP+FP+FN). Zero denominators return JSON-safe `None`, including F1 on an
entirely normal set with no predicted anomalies. Missed positives give F1 = 0
even when precision is undefined.

AP is the non-interpolated sum of precision weighted by recall increments,
following the [scikit-learn definition](https://scikit-learn.org/stable/modules/generated/sklearn.metrics.average_precision_score.html).
It is not trapezoidal PR area. Equal scores enter together, so input order cannot
improve AP. Ranking uses continuous scores independently of the supplied
operating threshold and comparison.

`pr_curve` contains increasing distinct `thresholds` with `score >= threshold`
points, followed by a threshold-free `(precision=1, recall=0)` endpoint, matching
the [PR convention](https://scikit-learn.org/stable/modules/generated/sklearn.metrics.precision_recall_curve.html).
With positives, precision/recall arrays have one more item than thresholds.
With no eligible positives (including empty selections), AP is `None` and PR
arrays are empty because recall is undefined. All-positive sets have AP = 1.
These denominator policies are explicit rather than dependency defaults.

## Rules and future models

`baseline_window_scores(raw_features, config)` accepts an **unscaled** numeric
row from `window_features` / `features.parquet`, without keys or labels. It
returns `BaselineWindowScore(score, threshold, comparison)` per rule:

| Rule | Score | Fixed operating decision |
| --- | --- | --- |
| `heartbeat_timeout` | Maximum age/timeout across subscribed CAN IDs | >= 1 |
| `rpm_derivative` | Maximum absolute RPM derivative | > configured RPM threshold |
| `wheel_residual` | Maximum wheel disagreement | > configured residual threshold |

Heartbeat normalization combines channels with different timeout durations
while preserving their timeout decision. Missing RPM/wheel evidence maps to
score **0** (no current anomaly evidence), including silence, which heartbeat
still scores. Every eligible window remains in every rule's denominator. Missing
required columns, invalid/negative measurements and absent heartbeat
subscriptions fail.

Wrap each score in `WindowScore(key, evidence.score)` and pass its `threshold`
and `comparison` to `evaluate_windows`. Future models use the same keys, labels,
split and eligibility. These are instantaneous window decisions, distinct from
M8's latched alert states, where unavailable evidence cannot close an episode.
Event detection, latency and false alarm episodes/hour remain separate
[episode metrics](BENCHMARK.md).

Results record evaluator version, model ID, split and supplied threshold policy.
Published comparisons must also retain dataset/version, split/feature/config
lineage and environment provenance. The API performs no threshold/model search
or final-test scheduling. Validation is the only selection set; test and both
OOD domains remain outside selection.
