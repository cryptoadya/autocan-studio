# IsolationForest on saved splits — M9 fourth pass

This first model experiment uses the validated [saved window contract](SAVED_WINDOW_RULES.md)
and the same [binary evaluator](WINDOW_EVALUATION.md) as the three Tier 0 rules.
Only eligible windows from **entirely normal train runs** fit IsolationForest.
The operating threshold uses eligible validation windows only. Test is scored
after both model and threshold are fixed; OOD is not loaded or accepted as an
input to this command. There is no estimator/hyperparameter search in this pass.

## Run

With an existing prepared dataset and its original Factory collection:

```sh
uv sync --locked
uv run --locked autocan train-isolation-forest data/ml/m9-id-seed42 --source data/datasets/m9-id-seed42 --output data/experiments/m9-iforest-seed42 --seed 42
```

Use a new output directory. The command revalidates the prepared artifacts and
source lineage, preserving the saved run/group splits and feature order.
There are no split, window, threshold, contamination, OOD or forest-size flags.
`--seed` is fixed before fitting (default 42, range 0…2³²−1). `--max-bytes` bounds
the entire output (default 16 MiB, maximum 64 MiB). Input limits remain those of
the saved-window loader. No input/preprocessing artifact is modified.

## Fit and threshold policy

The saved `normal_train` median/scaling state is reused without fitting it
again. Its fit-row fingerprint and scope must match the estimator's eligible
normal train subset exactly. Normal segments of fault runs, warm-up, validation,
test and OOD rows never enter estimator fitting. Only transformed numeric
feature tuples enter the estimator; run IDs, splits and labels stay metadata.

The versioned forest settings are fixed: 100 trees, `max_samples="auto"`
(at most 256 rows/tree), `max_features=1.0`, no bootstrap/warm start, one fit
worker and the explicit random state. Anomaly score is **`-score_samples`**,
so larger values mean more anomalous. The `contamination="auto"` offset and
sklearn's default `predict`/`decision_function` operating point are unused.
See the [official estimator contract](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.IsolationForest.html).

Threshold selection maximizes **pooled window F1 on eligible validation rows**,
using `score >= threshold` at every distinct validation score. Equal scores
enter together; equal F1 chooses the highest threshold. Integer comparisons
avoid rounding-dependent ties. Both validation classes are required. The
candidate set, objective and tie policy are fixed before looking at test scores.
The selection records its validation row count, positive count, row-index
fingerprint, candidate count, threshold and achieved validation F1.

After selection, one frozen model/threshold is evaluated on train, validation
and test alongside the unchanged fixed rules. Validation is an in-selection
result, not an unbiased quality estimate. Repeating an unchanged experiment
checks reproducibility; using test results to choose a new seed/model or
parameters would violate this protocol. The loader reads test data to validate
artifact integrity; those values do not enter fitting or threshold selection.

## Artifacts and reproducibility

| File | Contents |
| --- | --- |
| `model.pkl` | Locally trained sklearn estimator |
| `model.json` | Model/algorithm version, fixed parameters, feature order, fit scope/fingerprint and input lineage |
| `preprocessing.json` | Byte-for-byte saved normal-train state |
| `threshold.json` | Frozen validation selection and policy |
| `split.json` | Byte-for-byte saved preparation split |
| `scores.parquet` | Row index, model/rule ID, score, threshold, comparison and decision; no labels/features |
| `metrics.json` | Twelve model/rule × split rows with common precision/recall/F1, AP/PR and confusion matrices |
| `report.md` | Comparison table and protocol notes |
| `manifest.json` | Artifact hashes, data/split/config/code/package provenance, seed, `models_trained: ["isolation_forest"]` and `ood_used: false` |

Scores include retained warm-up rows for audit; metrics exclude them identically
for all four detectors. Rules use raw features and their saved fixed thresholds;
IsolationForest uses frozen normal-train transformations, including imputation
and missing masks. Missing windows are retained in all denominators.

Fit storage is capped at 8,000,000 float64 values (64 MB before sklearn's internal
float32 work arrays), additionally bounded by the 100,000-row input contract.
Scoring uses batches of 256 rows; evaluation retains bounded keys/scores for AP.
The staged publisher refuses existing outputs, enforces quotas and writes the
manifest last. Publication I/O failure can leave an incomplete destination.

The estimator is serialized only from this trusted local training process.
There is no model-import or live-inference endpoint. Pickle loading can execute
code and requires a trusted artifact plus a compatible Python/sklearn environment;
see [model persistence](https://scikit-learn.org/stable/model_persistence.html).
Package versions and hashes are recorded; bitwise reproducibility is checked
within the locked environment, with no claim across platforms/library upgrades.

## Observed regression fixture and limits

The software integration fixture has 12 acceleration runs, three fault types,
seed 12, eight seconds/run and 100 ms windows. Its saved semantic hash is
`5ce5ddcb8eed4ae03ac928135c407fe89edf48d8cb4cd2f7cc14e7ae96735521`.
With model seed 42 the validation-selected threshold is about 0.6412. The frozen
test result was F1 **0.352941**, AP **0.163729** for IsolationForest, compared
with F1 **0.5**, AP **0.399123** for the wheel-residual rule. The model loses
this comparison; defaults were not retuned in response. These small synthetic
fixture results verify the experiment path, not broad generalization or
real-vehicle quality. The integration test independently recomputes the
validation optimum and checks identical rule rows against `evaluate-rules`.

RandomForest, OOD scoring, uncertainty/runtime studies, model import/live
inference and final M9 acceptance remain separate work. No M9 completion or
advantage over rules is claimed.
