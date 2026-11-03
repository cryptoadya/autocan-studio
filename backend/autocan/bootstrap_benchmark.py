"""Publish run-bootstrap uncertainty from validated, frozen ID/OOD score streams."""

from hashlib import sha256
from importlib.resources import files
from pathlib import Path

import numpy as np

from autocan.bootstrap import (
    BOOTSTRAP_VERSION,
    DEFAULT_REPLICATES,
    MAX_REPLICATES,
    BootstrapResult,
    Interval,
    bootstrap_runs,
)
from autocan.parquet import _finish, _new_output
from autocan.saved_evaluation import load_saved_evaluation
from autocan.score_events import EVENT_EVALUATOR_VERSION
from autocan.traces import TraceError, _git_provenance, _packages
from autocan.validation import canonical_bytes, integer
from autocan.window_benchmark import DEFAULT_OUTPUT_BYTES, MAX_OUTPUT_BYTES
from autocan.window_evaluation import WINDOW_EVALUATOR_VERSION


def _cell(ci: Interval, scale: float = 1) -> str:
    def fmt(value: float | None) -> str:
        return "n/a" if value is None else f"{value / scale:.6g}"

    return f"{fmt(ci['estimate'])} [{fmt(ci['lower'])}, {fmt(ci['upper'])}]"


def _report(result: BootstrapResult) -> str:
    text = [
        "# Saved-score run bootstrap",
        "",
        "Point estimate [95% percentile CI]; whole runs sampled with replacement per split/domain.",
        "All detectors share each run draw. No refitting, inference or threshold selection.",
        "Validation is in-selection. Intervals condition on the saved models and thresholds.",
        "Undefined replicates are excluded per metric, never redrawn; counts are in metrics.json.",
        "Partial-undefined bounds are conditional on a defined denominator.",
        "CIs need two partition runs, or two contributing runs per fault type.",
        "Tiny samples can give degenerate CIs.",
        "No simultaneous-coverage or real-CAN generalization claim is made.",
        "",
        "| Split | Detector | Runs | Precision | Recall | F1 | AP |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in result["rows"]:
        cells = " | ".join(
            _cell(row["window"][m]) for m in ("precision", "recall", "f1", "average_precision")
        )
        text.append(f"| {row['split']} | {row['model_id']} | {row['runs']} | {cells} |")
    text.extend(
        [
            "",
            "## Temporal events and normal exposure",
            "",
            "Latency is conditional on detection, in virtual ms; nearest-rank p50/p95.",
            "Normal FA/hour pools only entirely normal runs and their eligible exposure.",
            "",
            "| Split | Detector | Detection rate | Mean ms | p50 ms | p95 ms | Normal FA/h |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in result["rows"]:
        e = row["event"]
        cells = " | ".join(
            [
                _cell(e["detection_rate"]),
                *(_cell(e[f"latency_{s}_ns"], 1e6) for s in ("mean", "p50", "p95")),
                _cell(e["false_alarms_per_hour"]),
            ]
        )
        text.append(f"| {row['split']} | {row['model_id']} | {cells} |")
    text.extend(
        [
            "",
            "## Fault types",
            "",
            "Types share partition-wide draws; absent-type draws are undefined.",
            "",
            "| Split | Detector | Fault type | Runs | Detection rate | p50 ms | p95 ms |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for detail in result["fault_type_rows"]:
        e = detail["event"]
        cells = " | ".join(
            [
                _cell(e["detection_rate"]),
                _cell(e["latency_p50_ns"], 1e6),
                _cell(e["latency_p95_ns"], 1e6),
            ]
        )
        text.append(
            f"| {detail['split']} | {detail['model_id']} | {detail['fault_type']} | "
            f"{detail['contributing_runs']} | {cells} |"
        )
    return "\n".join(text) + "\n"


def bootstrap_saved_metrics(
    prepared: Path,
    target: Path,
    *,
    source: Path,
    experiment: Path,
    ood_results: Path,
    suite: Path,
    reference: Path,
    replicates: int = DEFAULT_REPLICATES,
    seed: int = 42,
    max_bytes: int = DEFAULT_OUTPUT_BYTES,
) -> dict[str, object]:
    if integer(replicates, "bootstrap replicates", minimum=2) > MAX_REPLICATES:
        raise TraceError("bootstrap replicates exceed 10000")
    integer(seed, "bootstrap seed")
    if integer(max_bytes, "bootstrap output quota", minimum=4096) > MAX_OUTPUT_BYTES:
        raise TraceError("bootstrap output quota exceeds 64 MiB")
    with _new_output(target) as staging:
        saved = load_saved_evaluation(
            prepared,
            source=source,
            experiment=experiment,
            ood_results=ood_results,
            suite=suite,
            reference=reference,
        )
        result = bootstrap_runs(saved.runs, saved.policies, replicates=replicates, seed=seed)
        protocol = {
            "format": "autocan-run-bootstrap-protocol",
            "format_version": 1,
            "bootstrap_version": BOOTSTRAP_VERSION,
            "window_evaluator_version": WINDOW_EVALUATOR_VERSION,
            "event_evaluator_version": EVENT_EVALUATOR_VERSION,
            "confidence_level": 0.95,
            "replicates": replicates,
            "bootstrap_seed": seed,
            "method": "percentile; NumPy quantile(method=linear) at 0.025, 0.975",
            "sampling": "uniform n-out-of-n whole runs with replacement, separately per partition",
            "pairing": "same run multiplicities for all detectors, metrics and fault types",
            "rng": "PCG64(SeedSequence([seed, int.from_bytes(sha256(split)[:8], 'big')]))",
            "order": "sort partitions and run IDs lexicographically before sampling",
            "aggregation": "pooled windows/events/exposure, not mean of per-run rates",
            "ap": "non-interpolated, tied scores grouped; whole-run multiplicity weights",
            "undefined": (
                "exclude per metric; no redraw or zero substitution; report valid/undefined"
            ),
            "minimum_runs": (
                "partition >=2 runs; per-fault-type >=2 contributing runs; point retained"
            ),
            "scope": (
                "conditional on saved models, preprocessing, cutoffs and finite run collection"
            ),
            "selection": "no fit/inference/selection; validation remains in-selection",
            "inputs": saved.inputs,
            "policies": saved.policies,
            "resampling": result["resampling"],
        }
        metrics = {
            "format": "autocan-run-bootstrap-metrics",
            "format_version": 1,
            "bootstrap_version": BOOTSTRAP_VERSION,
            "protocol": protocol,
            "rows": result["rows"],
            "fault_type_rows": result["fault_type_rows"],
        }
        used = 0
        for name, raw in (
            ("protocol.json", canonical_bytes(protocol) + b"\n"),
            ("metrics.json", canonical_bytes(metrics) + b"\n"),
            ("report.md", _report(result).encode()),
        ):
            used += len(raw)
            if used > max_bytes:
                raise TraceError("bootstrap output quota exceeded")
            (staging / name).write_bytes(raw)
        code = sha256()
        for name in (
            "bootstrap",
            "bootstrap_benchmark",
            "saved_evaluation",
            "score_artifacts",
            "score_events",
            "window_evaluation",
            "evaluation",
            "model_artifacts",
            "training_artifacts",
            "ood",
        ):
            code.update(
                name.encode() + b"\0" + files("autocan").joinpath(f"{name}.py").read_bytes()
            )
        manifest = _finish(
            staging,
            {
                "format": "autocan-run-bootstrap",
                "format_version": 1,
                "status": "complete",
                "bootstrap_version": BOOTSTRAP_VERSION,
                "inputs": saved.inputs,
                "lineage": saved.data.manifest["lineage"],
                "id_scores_semantic_sha256": saved.frozen.manifest["semantic_sha256"],
                "ood_scores_semantic_sha256": saved.ood_meta["semantic_sha256"],
                "models_trained": [],
                "thresholds_selected": [],
                "scores_computed": 0,
                "bootstrap_seed": seed,
                "replicates": replicates,
                "experiment_seed": saved.frozen.seed,
                "package_versions": {**_packages(), "numpy": np.__version__},
                "implementation_sha256": code.hexdigest(),
                **_git_provenance(),
                "semantic_sha256": sha256(canonical_bytes(metrics)).hexdigest(),
            },
            {},
            max_bytes,
        )
    return manifest
