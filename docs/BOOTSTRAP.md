# Run-bootstrap confidence intervals — M9 eighth pass

`bootstrap-metrics` quantifies sampling uncertainty for the three fixed rules,
IsolationForest and RandomForest from the same saved ID and OOD scores consumed
by [event evaluation](EVENT_EVALUATION.md). It never deserializes estimators,
recomputes scores, fits preprocessing/models or selects thresholds. Test and both
OOD domains retain their frozen membership, scores and cutoffs.

## Reproduce from saved artifacts

```sh
uv run --locked autocan bootstrap-metrics data/recovered/m9-pr17-pytest272/prepared --source data/recovered/m9-pr17-pytest272/source --experiment data/recovered/m9-pr17-pytest272/experiment --ood-results data/experiments/m9-frozen-ood-pr17-seed43-final --suite data/recovered/m9-pr17-pytest272/ood-suite --reference data/recovered/m9-pr17-pytest272/reference --output data/experiments/m9-bootstrap-reproduced --replicates 2000 --seed 42
```

Inputs must already exist and be compatible; output must be a new directory.
Defaults are 2,000 replicates and bootstrap seed 42; the replicate range is
2–10,000. `--max-bytes` defaults to 16 MiB and permits at most 64 MiB. These
options control Monte Carlo sampling and output size, not detector selection.
The bootstrap seed is recorded separately from the experiment seed. Fix the
sampling protocol before inspecting results; do not search seeds for better CIs.

The shared reader validates all six input manifests, code/environment and
source/split lineage, model bytes without unpickling, copied frozen metadata,
score schema/coverage/order/hashes, finite scores, cutoff comparisons and saved
decisions. OOD keys and labels must agree with verified source truth. Inputs are
never rewritten. Checksums establish integrity, not authenticity or experiment
history. Existing corruption, quota and staging guarantees still apply.

## Sampling and estimands

For each of train, validation, test, `ood_fault` and `ood_normal` separately,
sort run IDs and sample **n whole runs with replacement from its n runs**.
Sample uniformly over runs, without class/type stratification. Runs never cross
partitions, and adjacent windows or individual events are never sampled
independently. Every detector, metric and fault type shares the same replicate's
run multiplicities. A repeated run contributes all its eligible windows, events,
latencies and normal exposure repeatedly, preserving its within-run dependence.
Empty/warm-up-only contributions remain empty; no alarm crosses a run boundary.

Each replicate recomputes pooled metrics, rather than averaging per-run rates:

- Window precision, recall, F1, non-interpolated AP, binary TN/FP/FN/TP and
  eligible/positive/negative window counts. Scores are sorted once, ties enter
  together, and multiplicities weight both positive and total counts for AP.
- Event detection rate, detected/missed/scored/unscored/censored counts,
  episode precision/F1, and mean/nearest-rank p50/p95 detection latency.
  Matching and latency follow the unchanged temporal policy in
  [EVENT_EVALUATION.md](EVENT_EVALUATION.md). Each run is matched once; repeated
  copies multiply its existing matches and preserve censoring/original starts.
- Normal false alarm episodes/hour: summed normal alarm counts divided by summed
  eligible exposure of entirely normal runs. Quiet segments of faulty runs do
  not supply normal exposure. Counts and exposure are also included.
- Separate event detection/latency intervals by fault type, using the same
  partition-wide draws. No type-conditioned resampling or cross-domain pooling.

Point estimates use the original run collection once and match existing window
and event evaluators (AP permits floating-point summation differences).

A partition-local PCG64 stream uses
`SeedSequence([seed, int.from_bytes(sha256(split.encode()).digest()[:8], 'big')])`.
Sorting IDs makes results independent of input iteration order. The protocol
records the ordered roster and SHA-256 of canonical run-multiplicity vectors,
allowing the exact paired draws to be reconstructed with the recorded NumPy version.

## Intervals and undefined replicates

Every metric stores `estimate`, `lower`, `upper`, `valid_replicates`,
`undefined_replicates` and `status`. Bounds are the 2.5th and 97.5th percentiles
of defined replicate values, using NumPy's linear quantile interpolation. They
are fixed 95% marginal percentile intervals, not simultaneous bounds.

The existing null-denominator semantics apply: no positive windows means null
recall/AP; no predicted positives means null precision; no faults means null
detection rate; no matched events means null latency; no normal exposure means
null FA/hour. Undefined replicates are counted and excluded **per metric**,
without redraws or zero substitution. Bounds with `partial_undefined` status
are conditional on the metric being defined; inspect their valid counts.

With fewer than two runs (or two contributing runs for a fault-type row), bounds
are null with `insufficient_runs`, even when a point estimate exists. Fewer than
two defined replicate values also yields null bounds. Constant or perfect
outcomes may produce equal bounds despite a tiny sample; this does not prove
certainty. Latency conditions on detected events and omits misses, so inspect
its detection rate and undefined count together.

These intervals condition on the frozen fitted models, preprocessing and chosen
validation cutoffs. They exclude training/model-selection uncertainty, simulator
bias and real-vehicle domain shift. Validation remains an in-selection result.
Repeated sampling of a small synthetic run collection adds no independent data.

## Outputs and resource bounds

- `metrics.json`: 25 detector × partition rows with window/event point estimates
  and intervals, plus per-fault-type event rows.
- `protocol.json`: fixed sampling/quantile/undefined policies, thresholds,
  ordered run rosters, draw hashes and six input manifest hashes.
- `report.md`: readable comparison tables with estimates and 95% intervals.
- `manifest.json`: output hashes, input lineage, original score experiment hashes,
  both seeds, replicate count, implementation hash, Git and package provenance.
  `models_trained` and `thresholds_selected` are empty; `scores_computed` is zero.

The shared reader retains at most 100,000 ID and 100,000 OOD windows with five
scores per window, plus bounded run-event results. Sampling retains at most
10,000 run-multiplicity vectors for one partition; windows/events are not
physically duplicated. Work scales with replicate count, score groups and event
count. Output checks happen before publication and validation/quota errors clean
staging. A crash or publication I/O failure may leave an incomplete destination;
a complete manifest is required. Runtime/RSS acceptance, live inference and
final M9 acceptance remain separate work.

## Observed preserved fixture, 2026-11-03

The command above ran with the recovered PR #17 experiment and previously frozen
compatible OOD suite; no generation, fitting or inference was performed. The
ignored output is `data/experiments/m9-bootstrap-pr17-seed42-final/`, semantic hash
`f4f4809eaafa1790a5e86e313942445de6bd6801f63572993ffcfccc93d6ccb1`.

There are four test runs, six OOD-fault runs and six OOD-normal runs. Selected
results below are point estimate [95% interval], using 2,000 replicates, seed 42.

| Partition | Detector | Window F1 | Event detection rate | Normal FA/hour |
| --- | --- | ---: | ---: | ---: |
| test | Heartbeat | 0.378378 [0, 0.875] | 0.333333 [0, 1] | 0 [0, 0] |
| test | RPM derivative | 0.125 [0, 0.307692] | 0.333333 [0, 1] | 0 [0, 0] |
| test | Wheel residual | 0.5 [0, 1] | 0.333333 [0, 1] | 0 [0, 0] |
| test | IsolationForest | 0.352941 [0, 0.629630] | 0.666667 [0, 1] | 947.368 [947.368, 947.368] |
| test | RandomForest | 0.983051 [0.941176, 1] | 1 [1, 1] | 0 [0, 0] |
| ood_fault | IsolationForest | 0.2 [0.058824, 0.360058] | 0.5 [0.166667, 0.833333] | n/a |
| ood_fault | RandomForest | 0.902439 [0.732394, 0.988506] | 1 [1, 1] | n/a |
| ood_normal | IsolationForest | 0 [0, 0] | n/a | 1184.21 [315.789, 2131.58] |
| ood_normal | RandomForest | 0 [0, 0] | n/a | 789.474 [473.684, 1105.26] |

Test AP for IF is 0.163729 [0.041546, 0.419687], RF 0.989394
[0.972222, 1]. AP/event detection have 1,993 defined test replicates; seven
samples contain only normal runs. Normal test FA/hour has 1,355 defined
replicates and only **one distinct normal run**, so its constant conditional
interval cannot establish stable false-alarm behavior. RF's perfect event
detection interval similarly reflects this finite collection, while its normal
OOD alarms remain substantial. Normal OOD AP is undefined in every replicate.

All three test fault types occur in one run apiece, so their type-specific
intervals are suppressed. The OOD frequency-anomaly type has two contributing
runs; the other OOD types have one. The full report retains every detector and
latency statistic, together with per-metric valid/undefined counts in JSON.
No model, cutoff or sampling setting was changed in response to these results.
