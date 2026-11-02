# M9 preparation: window tables and train-only preprocessing

This preparatory pass creates a persisted ID window dataset for later model
experiments. It reuses the M8 causal feature pipeline and M7 verified run
collections. It trains **no detector/model**, selects no thresholds and does not
read or regenerate the existing OOD sets. No new ML dependency is required.

## Build a representative ID collection

Plan mode/fault coverage before model evaluation. For example:

```sh
uv run --locked autocan generate --output data/datasets/m9-id-seed42 --runs 60 --fault-runs 36 --modes city acceleration --fault-types ecu_offline signal_spike cross_signal_inconsistency --seed 42 --max-bytes 1073741824
uv run --locked autocan prepare-ml data/datasets/m9-id-seed42 --output data/ml/m9-id-seed42 --split-seed 42
uv run --locked autocan prepare-ml data/datasets/m9-id-seed42 --output data/ml/m9-id-seed42-repeat --split data/ml/m9-id-seed42/split.json
```

All destinations must be new. The source is an ID factory collection, not an
observer directory, prior benchmark, OOD suite or prepared window dataset.
Generation is the existing M7 command; its scenario/physics contracts are unchanged.
The example reserves idle, stop-and-go, cruise and braking families, and the other
fault types, for later OOD work. More runs do not establish deployment prevalence
or operational false alarm performance.

`prepare-ml` accepts `--window` (seconds, default 0.5), `--heartbeat-periods` (5),
one of `--split-seed` (42) or `--split`, and `--max-bytes` (64 MiB maximum,
minimum 4096 bytes). It prints one JSON completion record with row count and
logical hash. There are no estimator, training, threshold-selection or scoring
options. Median/scaling statistics are the only fitted parameters in this pass.

## Whole-group stratification before feature extraction

The separate `autocan-training-split` format uses `mode-fault-groups-1`. Existing
M8 split v1, benchmarks and frozen OOD references retain their prior behavior.
This new format is consumed by `prepare-ml`; it is not an M8 `benchmark --split`
import. Future M9 comparisons must use this same saved partition for models and
rule baselines, rather than compare scores from different splits.

Exact scenario or whole-observer-input duplicates first form connected groups,
using the existing grouping contract. Groups are never split. Each configured
`mode × (normal or fault type)` stratum must have at least three independent
groups. Groups spanning different strata are rejected as ambiguous rather than
silently divided or relabelled. Missing configured strata fail preparation.

Within each stratum, a seed derived from the recorded seed/algorithm/stratum
shuffles sorted groups. `max(1, floor(groups/5))` groups each go to validation
and test; the rest go to train. Every part therefore contains every configured
mode/type and normal mode. Rounding and unequal duplicate-group sizes affect
run ratios; three-group fixtures yield 1/1/1, while larger strata approach
60/20/20. Split reuse validates the complete deterministic assignment and source
identity; overlap, omissions or hand-edited memberships are rejected.

Coverage is checked again after extraction: each split and each mode/type/split
cell needs eligible normal-run windows or eligible positive windows as appropriate.
`coverage.json` records group/run and window coverage, including warm-up exclusions.
Coverage uses source/label metadata only. It does not rank splits by a detector's
test/OOD quality or fit any feature transformation.

## Separate, aligned tables

| File | Contents |
| --- | --- |
| `features.parquet` | Nullable float64 numeric features and explicit `missing__…` flags only |
| `windows.parquet` | `row_index`, run ID, split, start/end ns and eligibility |
| `labels/windows.parquet` | `row_index`, nullable binary label, fault IDs/types and entire-run normal marker |
| `scaled/all_train.parquet` | Dense features transformed by the eligible-all-train state |
| `scaled/normal_train.parquet` | Dense features transformed by the normal-train-runs state |
| `preprocessing/*.json` | Safe JSON fitted medians, means/scales, observed counts, feature order and fit-sample fingerprint |
| `split.json`, `coverage.json`, `manifest.json` | Partitions, coverage, source/column/split lineage, versions and hashes |

The feature matrices contain **no** row IDs, run IDs, timestamps, split/domain,
mode, seed, fault fields, labels or source path. Row order is explicit: feature
row `i` joins windows/labels `row_index=i`. The exporter validates alignment and
row counts before fitting. Consumers must check the manifest/artifacts and
feature-column order; keys and labels must never be appended to model input.

The closed feature allowlist exports counts/ages/timeouts and interarrival
statistics per subscribed CAN ID, RPM statistics/derivative and wheel residual,
plus missing flags. It derives names/masks from the fixed profile/schema, not
from which values happen to be observed in held-out rows. CAN IDs appear in
column names as known profile signal identities. No new speed/physics feature
or simulator truth is introduced.

Features are written via the same receipt-time pipeline before reading a run's
fault truth. Feature schema changes within a collection fail. Labels remain
physically separate from raw and transformed matrices.

## Label and eligibility policy

The first window is `[0,W]`; later windows are `(start,end]`. Truth interventions
are `[fault.start,fault.end)`. A positive-duration intervention labels a window
positive when `fault.start <= window.end` and `fault.end > window.start`.
A start exactly on the right boundary is included; an end exactly on the left
boundary is excluded. There is no post-clear grace in window labels. Episode
metrics keep their independently documented M8 matching grace.

Labels describe injected interventions, including invisible or ineffective ones;
they do not assert physical anomalies. The target is binary normal/intervention,
with separate IDs/types for audit. Recovery or delayed effects outside the
intervention can appear in a negative window under this explicit policy.

A window becomes eligible when its end reaches the largest subscribed message
timeout for the configured profile/heartbeat setting. Earlier full windows are
retained with `eligible=false` and `label=null`, and are excluded from **both**
preprocessor fits. This shared ML warm-up is stricter than M8's per-rule warm-up
for short windows; it is recorded separately without changing M8 behavior.
Partial trailing windows are omitted. Missing data in eligible windows is kept
as null+mask, not converted into an observed zero.

## Train-only fitting and frozen transforms

Two artifacts support future supervised and normal-only experiments:

- `all_train`: all eligible train windows, including intervention windows.
- `normal_train`: eligible train windows from entirely fault-free runs; normal
  segments of fault runs do not enter this fit scope.

Both scopes filter split/eligibility/normal-run context **before** collecting
values. Validation/test values, missingness and labels cannot influence medians,
means or scales. A regression test changes/removes held-out rows and verifies
identical fit state. Normal-run context is used only for selecting the fit subset.

Each column is median-imputed, then scaled with population mean/std computed
after imputation. All-missing train columns use median 0 with `observed=0`;
constant/imputed-constant columns use scale 1. Missing masks preserve absence
information. The saved transforms are applied, without refitting, to all rows,
including held-out and ineligible rows. Downstream fitting/scoring must honor
the separately saved split and eligibility mask.

JSON reload validates format, ordered columns, vector sizes, finite numbers,
positive scales and counts. Transformation rejects column/order mismatches and
nonfinite values; no pickle/joblib, arbitrary code loading or ML fitting occurs.
The manifest binds both state files and their fit-row fingerprints to the exact
source/split/feature-column lineage.

## Reproducibility, limits and remaining work

The manifest records source and split hashes, feature order, configuration,
label/eligibility policy, current code/Git/package provenance, table checksums
and `models_trained: []`. Its preparation implementation hash covers features,
shared evaluation/benchmark helpers, training split, preprocessing and export.
The logical hash excludes generated UUIDs and physical/provenance differences;
same source/config/runtime yields repeatable data and statistics. Physical run
identities remain bound through source/split lineage and artifact hashes.

The complete output is capped at 64 MiB and 100,000 rows, with 256-row Parquet
groups. A fit stores at most 8,000,000 float64 training cells (about 64 MB), one
scope at a time; held-out rows do not consume this fit quota. Only one run's
window keys are buffered during label projection. Existing M7 input/feature
quotas still apply. Validation/quota failures before publication clean staging;
the inherited manifest-last publisher can leave an incomplete destination on
publication I/O failure/crash. Existing outputs are never overwritten.

This pass prepares ID only. Future M9 work must validate/load this artifact
contract, evaluate rules/models on the same frozen split, add common window
metrics and fit the first estimators on the intended training subset. Model and
threshold selection stays on validation; final test/OOD must remain outside
selection. Applying a frozen preprocessor to OOD will not permit refitting it.
