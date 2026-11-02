# Saved window validation and rule evaluation — M9 third pass

This pass loads the existing `causal-windows-1` artifacts from
[`prepare-ml`](TRAINING_DATA.md) and evaluates all three fixed rules through
the [common window evaluator](WINDOW_EVALUATION.md). It trains no models, fits
no preprocessing, selects no thresholds and creates no new split. M8 episode
benchmarks and frozen OOD references retain their existing formats/behavior.

## Run

After preparing an ID collection, use its original Factory source and a new
output directory:

```sh
uv run --locked autocan evaluate-rules data/ml/m9-id-seed42 --source data/datasets/m9-id-seed42 --output data/evaluations/m9-rules-seed42
```

The positional input is the **prepared window directory**, not a raw observer
dataset or Factory collection. `--source` is required to bind the saved data to
the original verified collection, validate its canonical saved split and audit
run/window identities, labels and coverage. A regenerated collection with new
run identities is incompatible even if its logical content is similar.

The command reads raw features from saved Parquet; it does not replay CAN
observations or regenerate features. Thresholds and feature configuration come
from the preparation manifest. There are no seed/split/threshold override flags.
The JSON completion record gives status, output path and semantic hash. Errors
return nonzero and do not publish evaluation metrics. Existing outputs are never
overwritten. The inherited staging publisher writes the manifest last; an I/O
failure during final publication can leave an incomplete destination.

## Loader contract

`load_training_data(prepared, source=collection)` in `autocan.training_artifacts`
finishes validation before returning `LoadedTrainingData`. Its `columns`,
`configuration`, `split`, `preprocessors` and `manifest` describe the frozen
input. `iter_windows()` yields `PreparedWindow` records with a `WindowKey`,
numeric feature tuple, nullable label and entire-run normal marker. Only the
feature tuple belongs in future model inputs; keys and truth remain separate.

Validation checks:

- Complete supported manifest/version/policies, exact artifact inventory,
  checksum/byte/record counts, provenance field shapes and safe artifact paths,
  including nested-directory symlink rejection.
- Original Factory integrity, source-manifest hash, source/split/column lineage,
  canonical whole-group assignments and the exact profile/receiver feature
  allowlist/order derived from the shared exporter.
- Exact Parquet schemas, bounded row groups, contiguous window identities,
  split membership, eligibility, separate truth projection, finite raw features,
  nullability, missing masks and configured heartbeat timeouts.
- Reloaded JSON preprocessing states, expected all-train/normal-train scope,
  eligible train-row fingerprints and observed counts; every saved scaled value
  must equal the frozen state's transformation of the raw row. No fit is called.
- Recomputed window/table/dataset semantic hashes and exact coverage totals,
  including eligible normal/positive coverage in each saved stratum/split.

The loader deliberately reads all artifact tables, including the scaled tables
unused by rules, so invalid prepared inputs cannot pass because one evaluation
path happens not to use them. Later iteration rechecks table and semantic
consistency; exhaust or close the iterator to release files. Callers must keep
inputs unchanged while loading/evaluating. Checksums detect corruption and bind
the declared source; they are not signatures. Raw features and fitted numeric
statistics are not independently regenerated/refitted from original CAN data.

Import is capped at 64 MiB and 100,000 windows, with the existing 256-row group
and 64 MiB decoded-table limits. Only bounded batches, fit counters and hashes
are retained by the loader. The API's `max_bytes` can lower the import limit.
The CLI output defaults to 16 MiB; `--max-bytes` changes only the output quota,
up to 64 MiB. Evaluation retains bounded window identities and scores for AP.

## Outputs and interpretation

| File | Contents |
| --- | --- |
| `split.json` | Byte-for-byte copy of the saved preparation split |
| `metrics.json` | Nine split/rule rows with counts, precision/recall/F1, AP, full PR curves and binary confusion matrices |
| `report.md` | Human-readable table and metric/missing-evidence conventions |
| `manifest.json` | Source/preparation/split/column lineage, frozen configuration, code/environment provenance, output hashes and `models_trained: []` |

All eligible windows remain in all rules' denominators. Warm-up is excluded;
missing instantaneous RPM/wheel evidence scores zero. Rule boundaries and AP/PR
policies follow the common evaluator. These are intervention window metrics,
distinct from episode matching, latency and false alarm episodes/hour.

All three saved partitions are reported with the already fixed demonstration
thresholds. This command performs no model/threshold selection and does not
schedule a model's final test. Validation remains the only future selection
set; test and OOD must stay outside tuning. Repeating against unchanged saved
inputs and the same code/environment produces identical outputs. No model
training, OOD transformation, Studio UI or real-vehicle validation is included.
