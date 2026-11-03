# M9 frozen-artifact acceptance

Verdict: **accepted_frozen_synthetic_pipeline**.
Acceptance is restricted to the verified frozen synthetic experiment pipeline.
It establishes no operational generalization or real-vehicle diagnostic quality.

Training rows are fit data; validation is in-selection. Test and OOD scores are reused,
with no fitting, threshold selection, model selection, new scores or datasets.
Both event/bootstrap replays are byte-identical across two independent evaluations.
Archived scientific JSON agrees with exact counts/nulls and absolute float tolerance 1e-12.
Input file fingerprints were unchanged before/after verification.
Seeds: dataset 12; models 42; bootstrap 42.

## Evidence identities

- prepared manifest SHA-256: `d848840244aaf56bede6aa2f072ee9be35806c5b0cbd217edeaf4759fe8ead35`.
- source manifest SHA-256: `8788537696398875bb8a6be62137196db877732a3a108e86df706316fee5f785`.
- experiment manifest SHA-256: `1f56eb832f69fb745fc4750d114bae8c3301d94bd94797e8c00aee1f67d148f5`.
- ood_results manifest SHA-256: `ccb54f6a7b48e13383a1f995fdc631d77cd4310b10141cda6ec214b65440823a`.
- suite manifest SHA-256: `a0f38be9443ba628cb96497093e531197b96d0de67e3401213ac947701fe6010`.
- reference manifest SHA-256: `f0e6f7dbbd31d5ef786c1da20d2839f849d3b995337138065d2937564d985e18`.
- Quality experiment semantic SHA-256: `4f59336939a03b45a485862e410ea846b6bcfed3c3230a193b1db7dc7935b247`.
- Runtime experiment semantic SHA-256: `0edcf143dc0a0d9a03716f0552d339eb2f65118897642dbb6ed37f1aa1210832`.
- Runtime model pickles, preprocessing, thresholds, numeric features/labels/scaled tables
  and saved scores match by content. Experiment, split and run identities remain distinct.
- Different runtime/quality metadata: models/isolation_forest/model.json, models/random_forest/model.json, split.json.

## Coverage

| Partition | Runs | Entirely normal runs | Eligible windows | Positive windows | Normal seconds |
| --- | ---: | ---: | ---: | ---: | ---: |
| train | 4 | 1 | 304 | 37 | 7.6 |
| validation | 4 | 1 | 304 | 34 | 7.6 |
| test | 4 | 1 | 304 | 30 | 7.6 |
| ood_fault | 6 | 0 | 456 | 87 | 0 |
| ood_normal | 6 | 6 | 456 | 0 | 45.6 |

## Window comparison

Dataset versions are the manifest hashes above: prepared for ID, suite for OOD.
Cells show point estimate [95% run-bootstrap interval]. `ge` means >=; `gt` means >.
| Dataset | Split | Detector | Threshold | Decision | Precision | Recall | F1 | AP | TN/FP/FN/TP |
| --- | --- | --- | ---: | --- | --- | --- | --- | --- | --- |
| suite | ood_fault | heartbeat_timeout | 1 | ge | 1 [1, 1] | 0.0229885 [0, 0.045977] | 0.0449438 [0, 0.0879121] | 0.523066 [0.321372, 0.742325] | 369/0/85/2 |
| suite | ood_fault | rpm_derivative | 20000 | gt | 1 [1, 1] | 0.045977 [0.0108696, 0.0987654] | 0.0879121 [0.0215054, 0.179775] | 0.357637 [0.211178, 0.610271] | 369/0/83/4 |
| suite | ood_fault | wheel_residual | 5 | gt | n/a [n/a, n/a] | 0 [0, 0] | 0 [0, 0] | 0.190789 [0.175439, 0.203947] | 369/0/87/0 |
| suite | ood_fault | isolation_forest | 0.6412 | ge | 0.264151 [0.0861704, 0.435484] | 0.16092 [0.0449438, 0.325] | 0.2 [0.0588235, 0.360058] | 0.192548 [0.13951, 0.296413] | 330/39/73/14 |
| suite | ood_fault | random_forest | 0.313735 | ge | 0.961039 [0.911392, 1] | 0.850575 [0.592593, 0.989247] | 0.902439 [0.732394, 0.988506] | 0.962716 [0.870913, 0.997925] | 366/3/13/74 |
| suite | ood_normal | heartbeat_timeout | 1 | ge | n/a [n/a, n/a] | n/a [n/a, n/a] | n/a [n/a, n/a] | n/a [n/a, n/a] | 456/0/0/0 |
| suite | ood_normal | rpm_derivative | 20000 | gt | n/a [n/a, n/a] | n/a [n/a, n/a] | n/a [n/a, n/a] | n/a [n/a, n/a] | 456/0/0/0 |
| suite | ood_normal | wheel_residual | 5 | gt | n/a [n/a, n/a] | n/a [n/a, n/a] | n/a [n/a, n/a] | n/a [n/a, n/a] | 456/0/0/0 |
| suite | ood_normal | isolation_forest | 0.6412 | ge | 0 [0, 0] | n/a [n/a, n/a] | 0 [0, 0] | n/a [n/a, n/a] | 406/50/0/0 |
| suite | ood_normal | random_forest | 0.313735 | ge | 0 [0, 0] | n/a [n/a, n/a] | 0 [0, 0] | n/a [n/a, n/a] | 156/300/0/0 |
| prepared | test | heartbeat_timeout | 1 | ge | 1 [1, 1] | 0.233333 [0, 0.777778] | 0.378378 [0, 0.875] | 0.339035 [0.0361842, 0.895468] | 274/0/23/7 |
| prepared | test | rpm_derivative | 20000 | gt | 1 [1, 1] | 0.0666667 [0, 0.181818] | 0.125 [0, 0.307692] | 0.137185 [0.0506396, 0.250536] | 274/0/28/2 |
| prepared | test | wheel_residual | 5 | gt | 1 [1, 1] | 0.333333 [0, 1] | 0.5 [0, 1] | 0.399123 [0.0361842, 1] | 274/0/20/10 |
| prepared | test | isolation_forest | 0.6412 | ge | 0.315789 [0, 0.5] | 0.4 [0, 1] | 0.352941 [0, 0.62963] | 0.163729 [0.0415456, 0.419687] | 248/26/18/12 |
| prepared | test | random_forest | 0.313735 | ge | 1 [1, 1] | 0.966667 [0.888889, 1] | 0.983051 [0.941176, 1] | 0.989394 [0.972222, 1] | 274/0/1/29 |
| prepared | train | heartbeat_timeout | 1 | ge | 1 [1, 1] | 0.243243 [0, 0.9] | 0.391304 [0, 0.947368] | 0.335349 [0.0657895, 0.906579] | 267/0/28/9 |
| prepared | train | rpm_derivative | 20000 | gt | 1 [1, 1] | 0.0540541 [0, 0.117647] | 0.102564 [0, 0.210526] | 0.195765 [0.106383, 0.288021] | 267/0/35/2 |
| prepared | train | wheel_residual | 5 | gt | 1 [1, 1] | 0.27027 [0, 1] | 0.425532 [0, 1] | 0.359086 [0.0559211, 1] | 267/0/27/10 |
| prepared | train | isolation_forest | 0.6412 | ge | 0.5 [0, 0.71831] | 0.513514 [0, 1] | 0.506667 [0, 0.80303] | 0.287859 [0.0728782, 0.614575] | 248/19/18/19 |
| prepared | train | random_forest | 0.313735 | ge | 0.948718 [0.833333, 1] | 1 [1, 1] | 0.973684 [0.909091, 1] | 1 [1, 1] | 265/2/0/37 |
| prepared | validation | heartbeat_timeout | 1 | ge | 1 [1, 1] | 0.235294 [0, 0.8] | 0.380952 [0, 0.888889] | 0.346943 [0.0460526, 0.906579] | 270/0/26/8 |
| prepared | validation | rpm_derivative | 20000 | gt | 1 [1, 1] | 0.0588235 [0, 0.142857] | 0.111111 [0, 0.25] | 0.159062 [0.052356, 0.261278] | 270/0/32/2 |
| prepared | validation | wheel_residual | 5 | gt | 1 [1, 1] | 0.294118 [0, 1] | 0.454545 [0, 1] | 0.373065 [0.0460526, 1] | 270/0/24/10 |
| prepared | validation | isolation_forest | 0.6412 | ge | 0.421053 [0, 0.823529] | 0.470588 [0, 1] | 0.444444 [0, 0.815534] | 0.251849 [0.0460653, 0.660116] | 248/22/18/16 |
| prepared | validation | random_forest | 0.313735 | ge | 0.942857 [0.769231, 1] | 0.970588 [0.9, 1] | 0.956522 [0.857143, 1] | 0.976299 [0.937879, 1] | 268/2/1/33 |

## Fault events and false alarms

Latency is conditional on detection, in **virtual ms**, not wall-clock inference time.
Normal exposure includes only entirely normal runs. FA/hour is exposure-normalized.
| Split | Detector | Detection rate | Latency p50 ms | Latency p95 ms | Normal alarms | Normal FA/hour |
| --- | --- | --- | --- | --- | ---: | --- |
| ood_fault | heartbeat_timeout | 0.333333 [0, 0.666667] | 760 [760, 1240] | 1240 [760, 1240] | 0 | n/a [n/a, n/a] |
| ood_fault | rpm_derivative | 0.5 [0.166667, 0.833333] | 760 [30, 1540] | 1540 [30, 1540] | 0 | n/a [n/a, n/a] |
| ood_fault | wheel_residual | 0 [0, 0] | n/a [n/a, n/a] | n/a [n/a, n/a] | 0 | n/a [n/a, n/a] |
| ood_fault | isolation_forest | 0.5 [0.166667, 0.833333] | 40 [30, 660] | 660 [30, 660] | 0 | n/a [n/a, n/a] |
| ood_fault | random_forest | 1 [1, 1] | 60 [30, 100] | 140 [60, 140] | 0 | n/a [n/a, n/a] |
| ood_normal | heartbeat_timeout | n/a [n/a, n/a] | n/a [n/a, n/a] | n/a [n/a, n/a] | 0 | 0 [0, 0] |
| ood_normal | rpm_derivative | n/a [n/a, n/a] | n/a [n/a, n/a] | n/a [n/a, n/a] | 0 | 0 [0, 0] |
| ood_normal | wheel_residual | n/a [n/a, n/a] | n/a [n/a, n/a] | n/a [n/a, n/a] | 0 | 0 [0, 0] |
| ood_normal | isolation_forest | n/a [n/a, n/a] | n/a [n/a, n/a] | n/a [n/a, n/a] | 15 | 1184.21 [315.789, 2131.58] |
| ood_normal | random_forest | n/a [n/a, n/a] | n/a [n/a, n/a] | n/a [n/a, n/a] | 10 | 789.474 [473.684, 1105.26] |
| test | heartbeat_timeout | 0.333333 [0, 1] | 110 [110, 110] | 110 [110, 110] | 0 | 0 [0, 0] |
| test | rpm_derivative | 0.333333 [0, 1] | 0 [0, 0] | 0 [0, 0] | 0 | 0 [0, 0] |
| test | wheel_residual | 0.333333 [0, 1] | 90 [90, 90] | 90 [90, 90] | 0 | 0 [0, 0] |
| test | isolation_forest | 0.666667 [0, 1] | 0 [0, 390] | 390 [0, 390] | 2 | 947.368 [947.368, 947.368] |
| test | random_forest | 1 [1, 1] | 10 [0, 90] | 90 [0, 90] | 0 | 0 [0, 0] |
| train | heartbeat_timeout | 0.333333 [0, 1] | 80 [80, 80] | 80 [80, 80] | 0 | 0 [0, 0] |
| train | rpm_derivative | 0.333333 [0, 1] | 0 [0, 0] | 0 [0, 0] | 0 | 0 [0, 0] |
| train | wheel_residual | 0.333333 [0, 1] | 30 [30, 30] | 30 [30, 30] | 0 | 0 [0, 0] |
| train | isolation_forest | 0.666667 [0, 1] | 0 [0, 30] | 30 [0, 30] | 2 | 947.368 [947.368, 947.368] |
| train | random_forest | 1 [1, 1] | 30 [0, 80] | 80 [0, 80] | 0 | 0 [0, 0] |
| validation | heartbeat_timeout | 0.333333 [0, 1] | 110 [110, 110] | 110 [110, 110] | 0 | 0 [0, 0] |
| validation | rpm_derivative | 0.333333 [0, 1] | 20 [20, 20] | 20 [20, 20] | 0 | 0 [0, 0] |
| validation | wheel_residual | 0.333333 [0, 1] | 70 [70, 70] | 70 [70, 70] | 0 | 0 [0, 0] |
| validation | isolation_forest | 0.666667 [0, 1] | 20 [20, 570] | 570 [20, 570] | 3 | 1421.05 [1421.05, 1421.05] |
| validation | random_forest | 1 [1, 1] | 20 [10, 70] | 70 [10, 70] | 1 | 473.684 [473.684, 473.684] |

## Per-fault event evidence

Intervals require at least two contributing runs. JSON retains every valid/undefined count.
| Split | Type | Detector | Contributing runs | Detection rate | p50 ms | p95 ms |
| --- | --- | --- | ---: | --- | --- | --- |
| ood_fault | message_delay | heartbeat_timeout | 1 | 0 [n/a, n/a] | n/a [n/a, n/a] | n/a [n/a, n/a] |
| ood_fault | message_drop | heartbeat_timeout | 1 | 1 [n/a, n/a] | 1240 [n/a, n/a] | 1240 [n/a, n/a] |
| ood_fault | message_frequency_anomaly | heartbeat_timeout | 2 | 0.5 [0, 1] | 760 [760, 760] | 760 [760, 760] |
| ood_fault | signal_bias | heartbeat_timeout | 1 | 0 [n/a, n/a] | n/a [n/a, n/a] | n/a [n/a, n/a] |
| ood_fault | signal_stuck | heartbeat_timeout | 1 | 0 [n/a, n/a] | n/a [n/a, n/a] | n/a [n/a, n/a] |
| ood_fault | message_delay | rpm_derivative | 1 | 1 [n/a, n/a] | 760 [n/a, n/a] | 760 [n/a, n/a] |
| ood_fault | message_drop | rpm_derivative | 1 | 0 [n/a, n/a] | n/a [n/a, n/a] | n/a [n/a, n/a] |
| ood_fault | message_frequency_anomaly | rpm_derivative | 2 | 0 [0, 0] | n/a [n/a, n/a] | n/a [n/a, n/a] |
| ood_fault | signal_bias | rpm_derivative | 1 | 1 [n/a, n/a] | 30 [n/a, n/a] | 30 [n/a, n/a] |
| ood_fault | signal_stuck | rpm_derivative | 1 | 1 [n/a, n/a] | 1540 [n/a, n/a] | 1540 [n/a, n/a] |
| ood_fault | message_delay | wheel_residual | 1 | 0 [n/a, n/a] | n/a [n/a, n/a] | n/a [n/a, n/a] |
| ood_fault | message_drop | wheel_residual | 1 | 0 [n/a, n/a] | n/a [n/a, n/a] | n/a [n/a, n/a] |
| ood_fault | message_frequency_anomaly | wheel_residual | 2 | 0 [0, 0] | n/a [n/a, n/a] | n/a [n/a, n/a] |
| ood_fault | signal_bias | wheel_residual | 1 | 0 [n/a, n/a] | n/a [n/a, n/a] | n/a [n/a, n/a] |
| ood_fault | signal_stuck | wheel_residual | 1 | 0 [n/a, n/a] | n/a [n/a, n/a] | n/a [n/a, n/a] |
| ood_fault | message_delay | isolation_forest | 1 | 1 [n/a, n/a] | 660 [n/a, n/a] | 660 [n/a, n/a] |
| ood_fault | message_drop | isolation_forest | 1 | 0 [n/a, n/a] | n/a [n/a, n/a] | n/a [n/a, n/a] |
| ood_fault | message_frequency_anomaly | isolation_forest | 2 | 0 [0, 0] | n/a [n/a, n/a] | n/a [n/a, n/a] |
| ood_fault | signal_bias | isolation_forest | 1 | 1 [n/a, n/a] | 30 [n/a, n/a] | 30 [n/a, n/a] |
| ood_fault | signal_stuck | isolation_forest | 1 | 1 [n/a, n/a] | 40 [n/a, n/a] | 40 [n/a, n/a] |
| ood_fault | message_delay | random_forest | 1 | 1 [n/a, n/a] | 60 [n/a, n/a] | 60 [n/a, n/a] |
| ood_fault | message_drop | random_forest | 1 | 1 [n/a, n/a] | 140 [n/a, n/a] | 140 [n/a, n/a] |
| ood_fault | message_frequency_anomaly | random_forest | 2 | 1 [1, 1] | 60 [60, 100] | 100 [60, 100] |
| ood_fault | signal_bias | random_forest | 1 | 1 [n/a, n/a] | 30 [n/a, n/a] | 30 [n/a, n/a] |
| ood_fault | signal_stuck | random_forest | 1 | 1 [n/a, n/a] | 40 [n/a, n/a] | 40 [n/a, n/a] |
| test | cross_signal_inconsistency | heartbeat_timeout | 1 | 0 [n/a, n/a] | n/a [n/a, n/a] | n/a [n/a, n/a] |
| test | ecu_offline | heartbeat_timeout | 1 | 1 [n/a, n/a] | 110 [n/a, n/a] | 110 [n/a, n/a] |
| test | signal_spike | heartbeat_timeout | 1 | 0 [n/a, n/a] | n/a [n/a, n/a] | n/a [n/a, n/a] |
| test | cross_signal_inconsistency | rpm_derivative | 1 | 0 [n/a, n/a] | n/a [n/a, n/a] | n/a [n/a, n/a] |
| test | ecu_offline | rpm_derivative | 1 | 0 [n/a, n/a] | n/a [n/a, n/a] | n/a [n/a, n/a] |
| test | signal_spike | rpm_derivative | 1 | 1 [n/a, n/a] | 0 [n/a, n/a] | 0 [n/a, n/a] |
| test | cross_signal_inconsistency | wheel_residual | 1 | 1 [n/a, n/a] | 90 [n/a, n/a] | 90 [n/a, n/a] |
| test | ecu_offline | wheel_residual | 1 | 0 [n/a, n/a] | n/a [n/a, n/a] | n/a [n/a, n/a] |
| test | signal_spike | wheel_residual | 1 | 0 [n/a, n/a] | n/a [n/a, n/a] | n/a [n/a, n/a] |
| test | cross_signal_inconsistency | isolation_forest | 1 | 1 [n/a, n/a] | 390 [n/a, n/a] | 390 [n/a, n/a] |
| test | ecu_offline | isolation_forest | 1 | 0 [n/a, n/a] | n/a [n/a, n/a] | n/a [n/a, n/a] |
| test | signal_spike | isolation_forest | 1 | 1 [n/a, n/a] | 0 [n/a, n/a] | 0 [n/a, n/a] |
| test | cross_signal_inconsistency | random_forest | 1 | 1 [n/a, n/a] | 90 [n/a, n/a] | 90 [n/a, n/a] |
| test | ecu_offline | random_forest | 1 | 1 [n/a, n/a] | 10 [n/a, n/a] | 10 [n/a, n/a] |
| test | signal_spike | random_forest | 1 | 1 [n/a, n/a] | 0 [n/a, n/a] | 0 [n/a, n/a] |
| train | cross_signal_inconsistency | heartbeat_timeout | 1 | 0 [n/a, n/a] | n/a [n/a, n/a] | n/a [n/a, n/a] |
| train | ecu_offline | heartbeat_timeout | 1 | 1 [n/a, n/a] | 80 [n/a, n/a] | 80 [n/a, n/a] |
| train | signal_spike | heartbeat_timeout | 1 | 0 [n/a, n/a] | n/a [n/a, n/a] | n/a [n/a, n/a] |
| train | cross_signal_inconsistency | rpm_derivative | 1 | 0 [n/a, n/a] | n/a [n/a, n/a] | n/a [n/a, n/a] |
| train | ecu_offline | rpm_derivative | 1 | 0 [n/a, n/a] | n/a [n/a, n/a] | n/a [n/a, n/a] |
| train | signal_spike | rpm_derivative | 1 | 1 [n/a, n/a] | 0 [n/a, n/a] | 0 [n/a, n/a] |
| train | cross_signal_inconsistency | wheel_residual | 1 | 1 [n/a, n/a] | 30 [n/a, n/a] | 30 [n/a, n/a] |
| train | ecu_offline | wheel_residual | 1 | 0 [n/a, n/a] | n/a [n/a, n/a] | n/a [n/a, n/a] |
| train | signal_spike | wheel_residual | 1 | 0 [n/a, n/a] | n/a [n/a, n/a] | n/a [n/a, n/a] |
| train | cross_signal_inconsistency | isolation_forest | 1 | 1 [n/a, n/a] | 30 [n/a, n/a] | 30 [n/a, n/a] |
| train | ecu_offline | isolation_forest | 1 | 0 [n/a, n/a] | n/a [n/a, n/a] | n/a [n/a, n/a] |
| train | signal_spike | isolation_forest | 1 | 1 [n/a, n/a] | 0 [n/a, n/a] | 0 [n/a, n/a] |
| train | cross_signal_inconsistency | random_forest | 1 | 1 [n/a, n/a] | 30 [n/a, n/a] | 30 [n/a, n/a] |
| train | ecu_offline | random_forest | 1 | 1 [n/a, n/a] | 80 [n/a, n/a] | 80 [n/a, n/a] |
| train | signal_spike | random_forest | 1 | 1 [n/a, n/a] | 0 [n/a, n/a] | 0 [n/a, n/a] |
| validation | cross_signal_inconsistency | heartbeat_timeout | 1 | 0 [n/a, n/a] | n/a [n/a, n/a] | n/a [n/a, n/a] |
| validation | ecu_offline | heartbeat_timeout | 1 | 1 [n/a, n/a] | 110 [n/a, n/a] | 110 [n/a, n/a] |
| validation | signal_spike | heartbeat_timeout | 1 | 0 [n/a, n/a] | n/a [n/a, n/a] | n/a [n/a, n/a] |
| validation | cross_signal_inconsistency | rpm_derivative | 1 | 0 [n/a, n/a] | n/a [n/a, n/a] | n/a [n/a, n/a] |
| validation | ecu_offline | rpm_derivative | 1 | 0 [n/a, n/a] | n/a [n/a, n/a] | n/a [n/a, n/a] |
| validation | signal_spike | rpm_derivative | 1 | 1 [n/a, n/a] | 20 [n/a, n/a] | 20 [n/a, n/a] |
| validation | cross_signal_inconsistency | wheel_residual | 1 | 1 [n/a, n/a] | 70 [n/a, n/a] | 70 [n/a, n/a] |
| validation | ecu_offline | wheel_residual | 1 | 0 [n/a, n/a] | n/a [n/a, n/a] | n/a [n/a, n/a] |
| validation | signal_spike | wheel_residual | 1 | 0 [n/a, n/a] | n/a [n/a, n/a] | n/a [n/a, n/a] |
| validation | cross_signal_inconsistency | isolation_forest | 1 | 1 [n/a, n/a] | 570 [n/a, n/a] | 570 [n/a, n/a] |
| validation | ecu_offline | isolation_forest | 1 | 0 [n/a, n/a] | n/a [n/a, n/a] | n/a [n/a, n/a] |
| validation | signal_spike | isolation_forest | 1 | 1 [n/a, n/a] | 20 [n/a, n/a] | 20 [n/a, n/a] |
| validation | cross_signal_inconsistency | random_forest | 1 | 1 [n/a, n/a] | 70 [n/a, n/a] | 70 [n/a, n/a] |
| validation | ecu_offline | random_forest | 1 | 1 [n/a, n/a] | 10 [n/a, n/a] | 10 [n/a, n/a] |
| validation | signal_spike | random_forest | 1 | 1 [n/a, n/a] | 20 [n/a, n/a] | 20 [n/a, n/a] |

## Historical runtime observation (separate evidence)

Local Apple M1 evidence: **True**; CI: **False**. OS: macOS-27.2-arm64-arm-64bit.
No performance measurement was rerun. The original implementation/configuration is retained in JSON.
These are batch-call **wall-clock ms** and full worker high-water RSS; no cross-machine timing comparison.
Rule calls include the joint three-rule helper. Rule estimator-only timing is not applicable.
| Detector | Batch | Scope | p50 ms | p95 ms | Windows/s | Peak RSS MiB |
| --- | ---: | --- | ---: | ---: | ---: | ---: |
| heartbeat_timeout | 1 | preprocessing_scoring | 0.022958 | 0.026291 | 41445.9 | 151.672 |
| heartbeat_timeout | 256 | preprocessing_scoring | 6.02054 | 7.12447 | 41226.6 | 152.234 |
| rpm_derivative | 1 | preprocessing_scoring | 0.022375 | 0.0225441 | 44427.3 | 164.172 |
| rpm_derivative | 256 | preprocessing_scoring | 5.71194 | 5.74904 | 44815.4 | 164.031 |
| wheel_residual | 1 | preprocessing_scoring | 0.02325 | 0.025208 | 42101.5 | 154.156 |
| wheel_residual | 256 | preprocessing_scoring | 5.70223 | 5.75346 | 44801.7 | 152.922 |
| isolation_forest | 1 | preprocessing_scoring | 3.11217 | 3.91838 | 302.883 | 165.828 |
| isolation_forest | 1 | estimator_only | 3.06027 | 3.31756 | 318.572 | 166.188 |
| isolation_forest | 256 | preprocessing_scoring | 10.8975 | 10.9889 | 23466.2 | 165.688 |
| isolation_forest | 256 | estimator_only | 3.41848 | 3.56835 | 74472.7 | 165.484 |
| random_forest | 1 | preprocessing_scoring | 2.93913 | 3.637 | 322.956 | 164.531 |
| random_forest | 1 | estimator_only | 2.86642 | 3.07097 | 343.361 | 164.719 |
| random_forest | 256 | preprocessing_scoring | 11.8872 | 14.1569 | 20881.5 | 159.719 |
| random_forest | 256 | estimator_only | 3.108 | 3.1723 | 82140.6 | 164.609 |

## Acceptance boundary and limitations

Models, preprocessing, cutoffs, ID/OOD memberships, bootstrap seed and repetitions were reused without selection.
Hashes verify integrity/compatibility, not authenticity or historical proof that test was observed only once.
The finite synthetic collection provides no broad operating-regime or real-CAN generalization guarantee.
Normal OOD false alarms are reported alongside detection; weak/negative results are valid scientific outcomes.
Few contributing runs cause undefined/suppressed or degenerate confidence intervals; resampling adds no new data.
Intervals condition on fitted models and selected validation cutoffs; training/selection uncertainty is excluded.
Latency conditions on detected events; inspect misses and censoring with every latency summary.
Runtime evidence is historical and machine/session specific; it is not CAN-to-alert latency or a timing regression gate.
Live inference, UI, affected-message attribution and final V2 acceptance belong to later work. No release is implied.
