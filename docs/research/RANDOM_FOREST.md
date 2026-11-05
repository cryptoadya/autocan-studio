# RandomForestClassifier comparison — M9 fifth pass

This pass adds a supervised binary classifier to the existing
[IsolationForest experiment](ISOLATION_FOREST.md), using the same validated
saved windows, whole-run/group splits and common window evaluator. It compares
five detectors: RandomForest, IsolationForest and the three fixed rules.

## Run

```sh
uv run --locked autocan train-random-forest data/ml/m9-id-seed42 --source data/datasets/m9-id-seed42 --output data/experiments/m9-rforest-seed42 --seed 42
```

The prepared directory and original Factory collection are required. Use a new
output directory. There are no split, feature, tree-size, threshold or OOD flags.
Seed is fixed before fitting (default 42, unsigned 32-bit range). Output defaults
to 16 MiB; `--max-bytes` can increase it to 64 MiB. The existing saved-window
input limits, checksums, source/label/coverage validation and exclusive staged
publication apply. Inputs and saved preprocessing are never rewritten/refitted.

## Training and selection protocol

| Detector | Fit subset | Frozen preprocessing | Operating point |
| --- | --- | --- | --- |
| RandomForestClassifier | All eligible, boolean-labeled train windows | `all_train` | Maximum pooled eligible validation F1 |
| IsolationForest | Eligible windows of entirely normal train runs | `normal_train` | Same validation-only F1 protocol |
| Three rules | No fit | Raw features | Existing saved fixed thresholds |

RandomForest includes positive windows and negative segments of fault runs.
Warm-up, validation, test and OOD values/labels do not enter its fit. Only numeric
feature tuples enter the estimator. Fit metadata records the ordered row-index
fingerprint, a separate row+label fingerprint, class counts and derived weights.
The fit subset must exactly match the saved all-train preprocessing fingerprint.

Settings were fixed before evaluation: 100 trees, Gini splits, depth at most 12,
at most 256 leaves/tree, minimum leaf size 2, square-root feature sampling,
bootstrap, no OOB selection or warm start, one worker and the explicit seed.
`class_weight="balanced"` derives weights solely from train labels as
N/(2 × class count). There is no search over these settings and no cross-split
oversampling. The anomaly score is `predict_proba` for **class 1**, using the
estimator's class order. Its default `predict` cutoff is unused; the score is
not a calibrated estimate of operational anomaly prevalence. See the
[official classifier contract](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.RandomForestClassifier.html).

Each model independently selects `score >= threshold` from its distinct
eligible validation scores. Equal scores enter together; equal pooled F1 keeps
the highest cutoff. Both training and validation classes are required for the
supervised experiment. **Both models and both cutoffs freeze before any test
score is computed.** Validation remains an in-selection result. Test and OOD
are not used to choose seeds, class weights, hyperparameters, thresholds or a
winning model. The loader's test-data integrity checks are separate from fitting.

The command reconstructs IsolationForest with its existing fixed protocol and
the same seed/source rather than importing a serialized model. Its estimator
bytes, metric rows and the rule rows are checked against the standalone
`train-isolation-forest` command. That command retains its flat artifact format.

## Artifacts and bounds

| Path | Contents |
| --- | --- |
| `models/random_forest/` | Trusted local `model.pkl`, `model.json`, copied all-train `preprocessing.json`, validation-only `threshold.json` |
| `models/isolation_forest/` | Corresponding IsolationForest artifacts with normal-train preprocessing |
| `split.json` | Byte-for-byte saved split |
| `scores.parquet` | Five scores/decisions per retained window, with row index/model ID and no labels/features |
| `metrics.json` | Fifteen model/rule × split metric rows, both model/selection records, common AP/PR and confusion matrices |
| `report.md` | Comparison table and protocol |
| `manifest.json` | Artifact/source/split/code/environment hashes, seed, model versions, both trained-model IDs and `ood_used: false` |

The joint metrics format is `autocan-forest-comparison-metrics`, version 1;
the manifest is `autocan-random-forest-experiment`, version 1. Warm-up scores
remain auditable but are excluded identically from all five metric denominators.
All eligible missing-evidence windows remain. Rules still use their original
raw-feature policies; models use their separately frozen imputation/masks.

Models fit sequentially, with at most 8,000,000 float64 feature values per fit
plus labels and sklearn work arrays, within the loader's 100,000-window limit.
Both forests have bounded tree sizes; scoring uses 256-row batches. Evaluation
retains bounded keys/scores for AP. Quota/validation errors before publication
clean staging, and existing outputs are never overwritten. Manifest-last
publication can leave an incomplete destination on publication I/O failure.
No model-import/live-inference endpoint is added; trusted pickle persistence
has the same restrictions as the IsolationForest pass.

## Observed software fixture and remaining work

On the same 12-run acceleration fixture documented for IsolationForest
(prepared hash `5ce5ddcb8eed4ae03ac928135c407fe89edf48d8cb4cd2f7cc14e7ae96735521`,
model seed 42), validation selected a RandomForest cutoff of about 0.313735.
Frozen test F1/AP were **0.983051 / 0.989394**, compared with
**0.352941 / 0.163729** for IsolationForest and **0.5 / 0.399123** for wheel residual.
These are software-regression results on one synthetic mode with only 30
positive test windows; they do not establish generalization to unseen faults,
normal families or real CAN. Parameters were not adjusted using these results.

The [sixth pass](ML_OOD.md) evaluates the saved joint experiment on frozen OOD
domains without refitting or tuning, including per-fault window metrics and
normal false alarms. The [seventh pass](EVENT_EVALUATION.md) evaluates temporal
fault events using those saved decisions. [Run-bootstrap uncertainty](BOOTSTRAP.md), [frozen runtime measurements](INFERENCE_BENCHMARK.md)
and [scoped frozen-artifact acceptance](../archive/M9_ACCEPTANCE.md) provide subsequent evidence.
Live inference belongs to M10; broader generalization remains unestablished.
