# Frozen inference performance

Environment: macOS-27.2-arm64-arm-64bit; Python 3.12.0; CI: False.

| Detector | Batch | Scope | p50 batch ms | p95 batch ms | Windows/s | Peak RSS MiB |
| --- | ---: | --- | ---: | ---: | ---: | ---: |
| heartbeat_timeout | 1 | preprocessing_scoring | 0.022958 | 0.026291 | 41445.9 | 151.672 |
| heartbeat_timeout | 1 | estimator_only | n/a | n/a | n/a | n/a |
| heartbeat_timeout | 256 | preprocessing_scoring | 6.02054 | 7.12447 | 41226.6 | 152.234 |
| heartbeat_timeout | 256 | estimator_only | n/a | n/a | n/a | n/a |
| rpm_derivative | 1 | preprocessing_scoring | 0.022375 | 0.0225441 | 44427.3 | 164.172 |
| rpm_derivative | 1 | estimator_only | n/a | n/a | n/a | n/a |
| rpm_derivative | 256 | preprocessing_scoring | 5.71194 | 5.74904 | 44815.4 | 164.031 |
| rpm_derivative | 256 | estimator_only | n/a | n/a | n/a | n/a |
| wheel_residual | 1 | preprocessing_scoring | 0.02325 | 0.025208 | 42101.5 | 154.156 |
| wheel_residual | 1 | estimator_only | n/a | n/a | n/a | n/a |
| wheel_residual | 256 | preprocessing_scoring | 5.70223 | 5.75346 | 44801.7 | 152.922 |
| wheel_residual | 256 | estimator_only | n/a | n/a | n/a | n/a |
| isolation_forest | 1 | preprocessing_scoring | 3.11217 | 3.91838 | 302.883 | 165.828 |
| isolation_forest | 1 | estimator_only | 3.06027 | 3.31756 | 318.572 | 166.188 |
| isolation_forest | 256 | preprocessing_scoring | 10.8975 | 10.9889 | 23466.2 | 165.688 |
| isolation_forest | 256 | estimator_only | 3.41848 | 3.56835 | 74472.7 | 165.484 |
| random_forest | 1 | preprocessing_scoring | 2.93913 | 3.637 | 322.956 | 164.531 |
| random_forest | 1 | estimator_only | 2.86642 | 3.07097 | 343.361 | 164.719 |
| random_forest | 256 | preprocessing_scoring | 11.8872 | 14.1569 | 20881.5 | 159.719 |
| random_forest | 256 | estimator_only | 3.108 | 3.1723 | 82140.6 | 164.609 |

Timing is machine/session specific; workload and procedure are reproducible, not cross-machine timings.
No CAN ingestion, decoding, feature extraction, scheduling, threshold decisions or alert delivery is timed; this is not CAN-to-alert latency.
Latency percentiles use linear interpolation over repeated batch-call wall times; batch 256 latency is not per-window latency.
Throughput is total scored windows / summed batch-call seconds; run_elapsed_ns also includes timer/loop bookkeeping.
Estimator-only excludes frozen transform/matrix construction and Python score validation; it retains score sign/class extraction.
Rules use raw feature mapping plus the existing joint three-rule scorer; each reported rule call includes that helper's other rule calculations. Rules have no separate estimator.
Peak RSS is fresh-worker process high-water memory through post-run parity; it includes imports, input validation, selected model loading, warm-up and measurement; not incremental estimator allocation.
Each detector/batch/scope uses a separate sequential process; RSS is normalized to bytes (Darwin bytes, Linux KiB).
Inputs are trusted local pickles. Hashes verify integrity, not authenticity. No fit, threshold tuning, test/OOD model selection or new datasets.
Warm-ups are complete untimed workload sweeps. Parity uses the exact batch/scope before warm-up and after measurement, outside all timing.
Fixed prefix workload and repeated cache-hot runs do not represent every operating regime; OS scheduling, thermal state, power mode and background load remain uncontrolled.
