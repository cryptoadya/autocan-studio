# M9 — frozen-artifact acceptance

This pass verifies the existing synthetic experiment and publishes one
[consolidated report](../acceptance/m9-frozen-2026-11-03/report.md),
[machine-readable evidence](../acceptance/m9-frozen-2026-11-03/acceptance.json) and
[provenance manifest](../acceptance/m9-frozen-2026-11-03/manifest.json).
Acceptance is scoped to reproducible frozen-artifact software. It does not
establish robust anomaly detection across operating regimes or real CAN.
Negative results, including substantial normal OOD false alarms, remain visible.

## One-command verification

Run from the repository root, in the locked environment, against the already
preserved inputs below. Choose a fresh output directory outside every input
root. No preparation, training, OOD generation or new scoring is invoked.

```sh
uv run --locked autocan accept-m9 data/recovered/m9-pr17-pytest272/prepared --source data/recovered/m9-pr17-pytest272/source --experiment data/recovered/m9-pr17-pytest272/experiment --ood-results data/experiments/m9-frozen-ood-pr17-seed43-final --suite data/recovered/m9-pr17-pytest272/ood-suite --reference data/recovered/m9-pr17-pytest272/reference --events data/experiments/m9-events-pr17-seed43 --bootstrap data/experiments/m9-bootstrap-pr17-seed42-final --inference docs/benchmarks/m1-frozen-inference-2026-11-03 --output data/experiments/m9-acceptance-reproduced
```

The same files and compatible recorded package versions are prerequisites.
Local datasets and pickles remain ignored by Git; a checkout and hashes alone
cannot reconstruct missing input files. This command does not silently replace
missing inputs with newly generated data or models. See
[training data](../research/TRAINING_DATA.md), [saved models](../research/RANDOM_FOREST.md),
[OOD](../research/ML_OOD.md), [events](../research/EVENT_EVALUATION.md),
[bootstrap](../research/BOOTSTRAP.md) and [runtime](../research/INFERENCE_BENCHMARK.md) for their contracts.
Hashes establish integrity and compatibility, not authenticity or proof of an
experiment's selection history. Inputs must come from a trusted local process.

`--max-bytes` bounds each derived replay/output bundle: 16 MiB by default,
4096 bytes minimum, 64 MiB maximum. Replay pairs are sequential; temporary
storage is at most two replay bundles at a time (plus final output). Existing
source collection/window bounds still apply. Existing destinations are refused.
Validation, mismatch or quota failures clean staging and publish no report.
As with the existing publishers, a publication I/O failure can leave an
incomplete destination; require its complete manifest before use.

## What is verified

| Acceptance criterion | Evidence/check |
| --- | --- |
| Frozen source, split and preprocessing lineage | Existing source-bound reader validates all six core inputs, held-out domains, checksums, schemas, eligibility, frozen transforms and model metadata without unpickling |
| Common five-detector window metrics | Re-evaluate saved scores with the existing window evaluator; compare ID/OOD rows, PR curves, confusion counts and OOD per-fault rows |
| Event detection, per-type latency, normal false alarms | Replay the existing saved-score event evaluator and compare its scientific JSON/ledger with the archive; verify OOD normal alarm summaries against replayed points |
| Uncertainty | Replay whole-run bootstrap with the archive's exact seed/repetition count; compare point estimates, intervals, pairing/draw hashes and undefined/suppressed cases |
| Repeatability | Independently replay event and bootstrap publication twice; require byte-identical scientific JSON, ledgers and rendered reports between repeats |
| M1 runtime | Validate the historical capture's checksums, complete detector/batch/scope coverage, raw-duration aggregates, repetitions, threads, parity records and content identity bridge; do not rerun timings |
| Input preservation | Compare all files' fingerprints in all nine input roots before/after verification; output cannot overlap inputs |
| Honest scope | Keep selection partitions, small denominators, normal OOD failures, conditional intervals, historical runtime and later milestones explicit |

Archived floating values allow **absolute error 1e-12, relative error zero**.
Integer values/types, booleans, nulls, object fields and row coverage are exact.
Fresh same-environment replay artifacts must be byte-identical. No tolerance,
seed, replicate count, threshold or detector-selection flags are exposed.
The bootstrap seed/count are taken from the archived bootstrap manifest and
must agree with its protocol and recomputed results. Validation remains
in-selection; test/OOD predictions are reused without new model selection.

A successful command produces `accepted_frozen_synthetic_pipeline` only when
verified local Apple M1 evidence is supplied. CI-only or other-machine runtime
evidence yields `verified_without_local_m1_evidence`, even if every software
check passes. No timing threshold or cross-machine timing equality is asserted.

## Runtime identity bridge

The quality evaluation uses experiment semantic hash
`4f59336939a03b45a485862e410ea846b6bcfed3c3230a193b1db7dc7935b247`.
The historical M1 capture uses
`0edcf143dc0a0d9a03716f0552d339eb2f65118897642dbb6ed37f1aa1210832`.
These are separately identified bundles. The verifier requires matching:

- Both estimator pickle, preprocessing and threshold file hashes.
- Prepared-data semantic identity and numeric features, labels and both scaled
  table byte hashes.
- Saved ID score table bytes and package-version records.

Differences in model JSON, split files and physical run identity are recorded;
they are not relabelled as an identical experiment. The M1 observation is
historical evidence for the same frozen estimators on the declared numeric
workload. It does not prove the runtime of the entire quality evaluation or the
new acceptance command. Its original files and implementation hash are preserved.
Recorded parity is checked for finite error within its frozen tolerance; no
score or timing is recomputed to manufacture new performance evidence.

## Interpretation and remaining boundaries

The designated ID collection is the existing 12-run acceleration fixture, not a
new representative corpus. Test contains four runs, only one entirely normal;
normal OOD exposure is 45.6 seconds. Most per-type cells have one contributing
run, so their confidence intervals are suppressed. A perfect or zero-width
interval on this collection does not establish reliability. Intervals exclude
training/model-selection uncertainty and domain shift. Latency conditions on
detected events; misses and censoring must be read alongside it.

Large normal OOD false-alarm counts remain a negative finding about these frozen
models. The acceptance verdict confirms the experiment/verification machinery
and its honest reporting; it is not a model-quality approval. Broader studies
need a separately specified protocol and untouched holdouts, with no tuning
against these already observed test/OOD results.

Live inference, offline/live parity, alert delivery, UI and affected-message
explanations belong to M10. XGBoost and MLflow are optional. This pass makes no
V2 release or full CAN-to-alert latency claim.

## Verification commands

```sh
.venv/bin/python -m pytest tests/test_acceptance.py -q
.venv/bin/python -m pytest
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
UV_CACHE_DIR=/tmp/autocan-uv-cache /tmp/autocan-uv-0.13.6/bin/uv build --no-sources
git diff --check
```

Automated tests use disposable software fixtures. They guard acceptance against
pickle loading, fitting, threshold selection, inference and generator calls;
experimental source artifacts are never refitted or regenerated. Actual local
acceptance uses only the designated preserved inputs above.

## Observed acceptance, 2026-11-03

Using the designated preserved inputs, the command returned
`accepted_frozen_synthetic_pipeline`. It verified 25 detector/partition rows,
70 per-fault event rows, and two independent event and bootstrap replays.
A second complete invocation produced byte-identical `acceptance.json` and
`report.md`; both have acceptance semantic SHA-256
`7ba47da050595f20747bc5cbcce58e6f5ce989ffe1cb179a183c6c9d784768ef`.
All nine input roots' file fingerprints were unchanged. The captured runtime
is the original local M1 observation; no new timing or inference was performed.

For the observed runs, the command above used `--output` values
`docs/acceptance/m9-frozen-2026-11-03` and
`/tmp/autocan-m9-acceptance-repeat-seeds`, respectively. Equality was checked with:

```sh
cmp docs/acceptance/m9-frozen-2026-11-03/acceptance.json /tmp/autocan-m9-acceptance-repeat-seeds/acceptance.json
cmp docs/acceptance/m9-frozen-2026-11-03/report.md /tmp/autocan-m9-acceptance-repeat-seeds/report.md
```

The report retains RF's six detected OOD fault events together with 10 normal
OOD false-alarm episodes, and IF's three detected events with 15 normal alarms.
Those normal counts cover only 45.6 seconds; their hourly normalization is not
an hour-long measurement. Per-type interval suppression and the single normal
ID test run remain explicit. No result was used to adjust models or thresholds.

The acceptance manifest records the pre-commit working tree (`git_dirty: true`)
and exact package implementation hash. Git provenance is separate from the
scientific repeatability comparison; it is not rewritten to claim a later run.


Final verification: the full suite passed **513 tests in 814.45 seconds**.
A subsequent provenance audit added two model-seed-header rejection cases;
after that fix, all **19 acceptance tests passed in 356.69 seconds**. The full
515-case suite was not repeated after those focused additions. Final lint,
formatting, strict type checks, package build and diff checks passed. The final
artifact run and repeat both passed; independent code review found no remaining
issues. No new remote CI or local frontend/browser checks were run for this
backend CLI/documentation pass.
