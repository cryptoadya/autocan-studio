# Offline research records

These records retain the original experimental protocols, command lines, and
artifact layouts. For current methodology, results, and what a fresh checkout
contains, start with [ML evaluation](../ML_EVALUATION.md). Paths under `data/`
in historical commands refer to generated or locally preserved inputs.

| Topic | Protocol and evidence |
| --- | --- |
| Features and fixed rules | [Causal windows and rule definitions](BASELINES.md), [rule benchmark](BENCHMARK.md), [separate OOD suite](OOD.md) |
| Prepared artifacts and shared evaluation | [Training data and preprocessing](TRAINING_DATA.md), [window metrics](WINDOW_EVALUATION.md), [source-bound loading and rule scoring](SAVED_WINDOW_RULES.md) |
| Model experiments | [Isolation Forest](ISOLATION_FOREST.md), [joint Random Forest comparison](RANDOM_FOREST.md), [frozen forest OOD scoring](ML_OOD.md) |
| Saved-score analysis | [Event matching and latency](EVENT_EVALUATION.md), [run-bootstrap intervals](BOOTSTRAP.md) |
| Runtime | [Inference benchmark protocol](INFERENCE_BENCHMARK.md), [Apple M1 report](../benchmarks/m1-frozen-inference-2026-11-03/report.md) |

The [verification archive](../archive/README.md) indexes the historical acceptance
reports and frozen evidence.
