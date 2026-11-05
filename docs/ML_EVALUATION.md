# Offline anomaly detection and evaluation

AutoCAN Studio compares simple CAN rules and two scikit-learn forests on the
same causal windows. The detectors run through the headless CLI. The dashboard
shows CAN measurements and injected fault truth, and has no live detector scores.

## Observations, splits, and detectors

- Features use only data available to the selected receiver by the window close.
  Fault truth, driver commands, physical state, frame creation times, scenario
  identity, and seeds stay outside the feature matrix. Missingness is explicit
  and warm-up windows are excluded from fit and scoring metrics.
- Entire runs stay together. Exact scenario or whole-observation duplicates form
  groups before partitioning. The training split stratifies by driving mode and
  normal/fault category. Median imputation and scaling are fitted on training
  windows only, with separate fit populations for the two forests.
- The three fixed rules score heartbeat timeout, RPM derivative, and wheel-speed
  disagreement. Wheel disagreement uses the four-wheel median because the
  synthetic profile lacks an independent CAN vehicle-speed reference.
- Isolation Forest fits eligible windows from entirely normal training runs.
  Random Forest fits eligible labeled training windows with train-only class
  weights. Each forest selects its threshold by pooled validation F1, breaking
  ties toward the higher cutoff. Both models and thresholds are frozen before
  held-out scoring. Validation metrics are part of selection.
- Out-of-distribution (OOD) scoring keeps unseen fault types and unfamiliar
  normal-driving families in separate domains. It reuses the saved models,
  preprocessing, and thresholds.

## Frozen synthetic results

The in-distribution (ID) test contains four runs, three scored fault episodes,
and 304 eligible windows (30 positive). The fault OOD set has six runs, six
scored episodes, and 456 eligible windows (87 positive). The normal OOD set
contains six entirely normal runs. Results below are point estimates from the
[checked-in verification report](acceptance/m9-frozen-2026-11-03/report.md).

| Detector | ID precision / recall / F1 | ID AP | ID events | OOD fault precision / recall / F1 | OOD fault AP | OOD fault events |
| --- | --- | ---: | ---: | --- | ---: | ---: |
| Heartbeat timeout | 1.000 / 0.233 / 0.378 | 0.339 | 1/3 | 1.000 / 0.023 / 0.045 | 0.523 | 2/6 |
| RPM derivative | 1.000 / 0.067 / 0.125 | 0.137 | 1/3 | 1.000 / 0.046 / 0.088 | 0.358 | 3/6 |
| Wheel-speed disagreement | 1.000 / 0.333 / 0.500 | 0.399 | 1/3 | — / 0.000 / 0.000 | 0.191 | 0/6 |
| Isolation Forest | 0.316 / 0.400 / 0.353 | 0.164 | 2/3 | 0.264 / 0.161 / 0.200 | 0.193 | 3/6 |
| Random Forest | 1.000 / 0.967 / 0.983 | 0.989 | 3/3 | 0.961 / 0.851 / 0.902 | 0.963 | 6/6 |

Precision, recall, F1, and average precision (AP) are window metrics. The dash
indicates undefined precision because the rule made no positive decisions in
that domain. Event matching groups consecutive positive windows into one alarm,
matches alarm onsets to fault intervals with a fixed one-window grace period,
and permits at most one match per fault and alarm. Matching is temporal. An alert
has no fault-type or CAN-message attribution.

| Detector | ID median / p95 detection delay | Fault OOD median / p95 detection delay | False alarms, ID normal (7.6 s) | False alarms, OOD normal (45.6 s) |
| --- | --- | --- | ---: | ---: |
| Heartbeat timeout | 110 / 110 ms | 760 / 1240 ms | 0 | 0 |
| RPM derivative | 0 / 0 ms | 760 / 1540 ms | 0 | 0 |
| Wheel-speed disagreement | 90 / 90 ms | — | 0 | 0 |
| Isolation Forest | 0 / 390 ms | 40 / 660 ms | 2 | 15 |
| Random Forest | 10 / 90 ms | 60 / 140 ms | 0 | 10 |

Delays are in virtual milliseconds among detected events. Zero means the first
positive window closed at the fault onset. The normal OOD counts correspond to
789.5 false-alarm episodes/hour for Random Forest and 1,184.2 for Isolation
Forest. Those rates extrapolate from 45.6 seconds of eligible exposure.

The 95% intervals use 2,000 paired bootstrap draws over whole runs within each
domain. For example, Random Forest window F1 was 0.983 [0.941, 1.000] on ID test
and 0.902 [0.732, 0.989] on fault OOD. The intervals condition on the frozen fit,
threshold, and run pool. With so few runs, some intervals are wide or degenerate.
The frequent alarms on unfamiliar normal traces limit the practical usefulness
of both forests at these operating points. This study uses synthetic traffic
only and provides no estimate of performance on real vehicle CAN.

## Inference cost

A separate Apple M1 capture used 512 eligible windows, one thread, three warm-up
sweeps, and ten measured sweeps. Random Forest preprocessing and scoring measured
2.94/3.64 ms p50/p95 at batch size 1 and 11.89/14.16 ms per batch at size 256
(about 20,882 windows/s and 159.7 MiB peak worker RSS for the latter workload).
The timings exclude bus ingestion, decoding, window construction, and alert
delivery. The runtime fixture has distinct run/split identities from the
quality fixture. The verification report records content matches for the models,
preprocessing, thresholds, numeric tables, and saved scores. See the
[benchmark report and workload](benchmarks/m1-frozen-inference-2026-11-03/report.md).

## Reproducibility and provenance

A fresh checkout supplies the generators, scoring and verification code, tests,
and checked-in evidence. To explore the pipeline with your own synthetic data,
start with `generate` and `verify-dataset`, then use `prepare-ml` and
`evaluate-rules` or the training commands. The preparation step requires enough
independent groups per configured mode/fault stratum for all three partitions.
The [research index](research/README.md) links runnable CLI examples and their
input requirements.

The reported numbers are historical measurements. Exact replay of the original
quality experiment requires its local source collection, prepared windows,
saved models and scores, OOD inputs, and a compatible runtime. These inputs
remain outside Git. The checked-in
[acceptance data](acceptance/m9-frozen-2026-11-03/acceptance.json) and
[manifest](acceptance/m9-frozen-2026-11-03/manifest.json) retain metrics, checksums,
and identities. The historical verification recomputed event and bootstrap
results from saved scores twice with byte-identical output and checked input
immutability. See the [verification record](archive/M9_ACCEPTANCE.md).
