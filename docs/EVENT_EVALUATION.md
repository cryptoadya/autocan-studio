# Event metrics from saved scores — M9 seventh pass

`evaluate-events` adds fault-event detection rate and latency for the three fixed
rules, IsolationForest and RandomForest. It reads the existing joint ID experiment
and [frozen OOD result](ML_OOD.md). No estimator is deserialized, no features or
scores are recomputed, and no threshold/model/seed is selected on test or OOD.

## Run with existing artifacts

```sh
uv run --locked autocan evaluate-events data/recovered/m9-pr17-pytest272/prepared --source data/recovered/m9-pr17-pytest272/source --experiment data/recovered/m9-pr17-pytest272/experiment --ood-results data/experiments/m9-frozen-ood-pr17-seed43-final --suite data/recovered/m9-pr17-pytest272/ood-suite --reference data/recovered/m9-pr17-pytest272/reference --output data/experiments/m9-events-reproduced
```

All inputs must already exist and remain mutually compatible. Use a fresh output
directory. The only optional setting is `--max-bytes` (16 MiB default, 64 MiB
maximum); there are no matching/grace, split, seed, inference or threshold flags.

The prepared/source validation and joint experiment metadata checks are reused,
including frozen preprocessing, cutoffs, split, code/environment and inventory
checks. Model pickle bytes are checked for integrity but are never deserialized.
The OOD suite/reference is revalidated, and the saved OOD result must bind exactly
to that suite, reference, ID experiment, preparation and source. Its copied
model/preprocessing/threshold/split/protocol records must match the originals.

Scores require the exact schema, five ordered detector rows for every expected
window, unchanged cutoffs/comparisons and decisions consistent with the saved
scores. Missing, extra, reordered, nonfinite or inconsistent rows fail. OOD
window keys and separate saved labels are checked against the verified source.
Physical/semantic hashes, bounded row groups and input/window quotas are checked.
Legacy score hashes preserve the recorded numeric type of integral rule cutoffs
before Arrow converted them to float64; this does not change a cutoff or decision.
Integrity checks cannot authenticate an artifact or prove experiment history.

## Fixed common event policy

The persisted scores describe **global window anomalies**, without per-CAN alarm
attribution. All five detectors therefore use the same **temporal-only** event
policy, version `saved-score-events-1`. It does not reconstruct the M8 detector's
latched, CAN-target-aware alarms or claim that an alarm identifies a fault's cause.
Ground-truth CAN IDs remain in the audit ledger but do not enter matching.

1. Keep whole-run train/validation/test membership and the two OOD domains. Never
   pool their event denominators. Validation remains an in-selection result.
2. Ignore warm-up decisions. Start fresh alarm state at the first eligible window.
   Contiguous positive eligible windows form one alarm; its onset is the **end**
   of the first positive window. The first negative window closes it at that
   window's end. No debounce is added. An alarm active at the final decision has
   `end_ns: null`; runs are never joined.
3. Evaluate positive-duration faults whose end is after the first eligible
   window's start and whose start is at or before the final complete window end.
   Faults entirely outside that span are unscored, with explicit counts and truth
   retained. Clip ends at the final decision, mark right censoring, and record
   left censoring when a fault started before the eligible span. Preserve the
   **original start** for latency. A fault starting exactly at the final decision
   can receive zero-latency credit and is right-censored if it continues into
   the discarded tail; faults starting later cannot receive credit.
4. Visit alarm onsets chronologically. An onset must be in
   `[original_fault_start, clipped_fault_end + window_ns)`; the upper bound is
   excluded. Thus an already-open alarm cannot detect a later fault. If several
   unmatched faults qualify, choose earliest end-plus-grace, then start, fault
   ID and saved order. Each alarm and fault receives at most one match per
   detector/run. This deterministic greedy policy is not a global optimum for
   arbitrary overlapping faults.
5. Detection rate is matched/scored faults. Latency is onset minus original
   fault start, in virtual nanoseconds. Report mean and nearest-rank p50/p95
   **among detected events only**; missed events are not assigned zero or a
   synthetic timeout latency. No faults means null rate; no detections means
   null latency. Zero is valid when fault activation and window closure share
   a virtual timestamp. These are not wall-clock inference timings.

One-window post-clear grace accounts for window closure, consistently with the
existing episode benchmark. It is fixed by the saved feature configuration,
not chosen from these event results. Normal false alarms use the same contiguous
window grouping and eligible exposure as the prior OOD pass, with no exposure
from quiet segments of faulty runs.

## Outputs and limits

- `metrics.json`: 25 detector × split/domain summaries, separate fault-type rows,
  counts/rates, conditional latency, unscored/censoring counts and normal FA/hour.
  Fault types with only unscored events have a zero scored denominator and null
  rate/latency rather than a fabricated miss rate.
- `runs.jsonl`: one record per run/detector with frozen cutoff/comparison, eligible
  span, scored/unscored truth, censoring IDs, alarm intervals and match indices/
  latency. A match's indices refer to the same record's fault/alert lists.
- `protocol.json`, `report.md`, `manifest.json`: matching/exposure definitions,
  aggregate and per-type comparison tables, all six input manifest hashes,
  source/split lineage, implementation/environment/Git provenance and output
  checksums. `models_trained` and `thresholds_selected` are empty;
  `scores_computed` is zero.

Each saved ID/OOD window stream retains the 100,000-window bound. Only one run's
decision vectors are buffered for grouping; bounded event results are retained
for pooled summaries. Alarm episodes have a 10,000-per-run/detector cap. Output
quota checks occur before publication. Existing outputs are refused; validation
or quota failures clean staging. As with prior publishers, publication I/O failure
or a crash may leave an incomplete destination; a complete manifest is required.
Inputs and their manifests are never rewritten.

## Observed preserved fixture, 2026-11-02

The inputs are exactly the recovered PR #17 experiment and once-frozen compatible
OOD suite documented in [ML_OOD.md](ML_OOD.md). No data/model generation or
inference was run for this evaluation. The archived output is
`data/experiments/m9-events-pr17-seed43/` (semantic hash
`28e571e0bf2e51af94187db33313f87063a14c2c7a269730e4ee47426cce2e1c`).

| Split | Detector | Detected/scored | Rate | Latency mean ms | p50 ms | p95 ms |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| test | Heartbeat | 1/3 | 0.333333 | 110 | 110 | 110 |
| test | RPM derivative | 1/3 | 0.333333 | 0 | 0 | 0 |
| test | Wheel residual | 1/3 | 0.333333 | 90 | 90 | 90 |
| test | IsolationForest | 2/3 | 0.666667 | 195 | 0 | 390 |
| test | RandomForest | 3/3 | 1 | 33.3333 | 10 | 90 |
| ood_fault | Heartbeat | 2/6 | 0.333333 | 1000 | 760 | 1240 |
| ood_fault | RPM derivative | 3/6 | 0.5 | 776.667 | 760 | 1540 |
| ood_fault | Wheel residual | 0/6 | 0 | n/a | n/a | n/a |
| ood_fault | IsolationForest | 3/6 | 0.5 | 243.333 | 40 | 660 |
| ood_fault | RandomForest | 6/6 | 1 | 71.6667 | 60 | 140 |

Per-type cells below give **detected/scored; p50/p95 latency in ms**, conditional
on detection. Every OOD type has one event except frequency anomaly, which has two.

| OOD fault type | Heartbeat | RPM derivative | Wheel residual | IsolationForest | RandomForest |
| --- | --- | --- | --- | --- | --- |
| message_delay | 0/1; n/a | 1/1; 760/760 | 0/1; n/a | 1/1; 660/660 | 1/1; 60/60 |
| message_drop | 1/1; 1240/1240 | 0/1; n/a | 0/1; n/a | 0/1; n/a | 1/1; 140/140 |
| message_frequency_anomaly | 1/2; 760/760 | 0/2; n/a | 0/2; n/a | 0/2; n/a | 2/2; 60/100 |
| signal_bias | 0/1; n/a | 1/1; 30/30 | 0/1; n/a | 1/1; 30/30 | 1/1; 30/30 |
| signal_stuck | 0/1; n/a | 1/1; 1540/1540 | 0/1; n/a | 1/1; 40/40 | 1/1; 40/40 |

| ID test fault type | Heartbeat | RPM derivative | Wheel residual | IsolationForest | RandomForest |
| --- | --- | --- | --- | --- | --- |
| cross_signal_inconsistency | 0/1; n/a | 0/1; n/a | 1/1; 90/90 | 1/1; 390/390 | 1/1; 90/90 |
| ecu_offline | 1/1; 110/110 | 0/1; n/a | 0/1; n/a | 0/1; n/a | 1/1; 10/10 |
| signal_spike | 0/1; n/a | 1/1; 0/0 | 0/1; n/a | 1/1; 0/0 | 1/1; 0/0 |

All faults in this fixture are within the eligible span; unscored/censoring counts
are zero. RF's 6/6 fault-event recall does not imply operational quality: normal
OOD still has **10 RF and 15 IF false alarm episodes in only 45.6 eligible seconds**,
versus zero for the rules. A brief alarm can detect an event despite low positive
window recall; event recall and window F1 answer different questions. These tiny
synthetic denominators do not establish real-CAN quality. No settings were changed
in response. Uncertainty/runtime studies, live inference and final M9 acceptance
remain separate work.
