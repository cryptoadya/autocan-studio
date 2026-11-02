"""One frozen IsolationForest experiment against fixed rules on saved ID splits."""

import pickle
from collections.abc import Generator, Iterable
from contextlib import closing
from dataclasses import asdict
from hashlib import sha256
from importlib.metadata import version
from importlib.resources import files
from pathlib import Path

import pyarrow as pa

from autocan.evaluation import RULE_IDS, SPLITS
from autocan.features import DETECTOR_ID, FEATURE_SCHEMA_VERSION
from autocan.isolation_forest import (
    MODEL_ID,
    MODEL_VERSION,
    FittedForest,
    choose_validation_threshold,
    fit_isolation_forest,
)
from autocan.parquet import ROW_GROUP_ROWS, _file_hash, _finish, _new_output, _TableWriter
from autocan.traces import TraceError, _git_provenance, _packages
from autocan.training_artifacts import PreparedWindow, load_training_data
from autocan.validation import canonical_bytes, integer
from autocan.window_benchmark import DEFAULT_OUTPUT_BYTES, MAX_OUTPUT_BYTES, _report
from autocan.window_evaluation import (
    WINDOW_EVALUATOR_VERSION,
    WindowScore,
    WindowTruth,
    baseline_window_scores,
    evaluate_windows,
)

EXPERIMENT_VERSION = "isolation-forest-comparison-1"
SCORE_SCHEMA = pa.schema(
    [
        pa.field("row_index", pa.int64(), nullable=False),
        pa.field("model_id", pa.string(), nullable=False),
        pa.field("score", pa.float64(), nullable=False),
        pa.field("threshold", pa.float64(), nullable=False),
        pa.field("comparison", pa.string(), nullable=False),
        pa.field("anomalous", pa.bool_(), nullable=False),
    ]
)


def _scored_windows(
    windows: Iterable[PreparedWindow],
    model: FittedForest,
    *,
    validation_only: bool = False,
) -> Generator[tuple[PreparedWindow, float], None, None]:
    batch: list[PreparedWindow] = []
    for row in windows:
        if validation_only and (row.key.split != "validation" or not row.key.eligible):
            continue
        batch.append(row)
        if len(batch) == ROW_GROUP_ROWS:
            yield from zip(batch, model.score(batch), strict=True)
            batch.clear()
    if batch:
        yield from zip(batch, model.score(batch), strict=True)


def run_isolation_forest(
    prepared: Path,
    target: Path,
    *,
    source: Path,
    seed: int = 42,
    max_bytes: int = DEFAULT_OUTPUT_BYTES,
) -> dict[str, object]:
    if integer(max_bytes, "ML experiment quota", minimum=4096) > MAX_OUTPUT_BYTES:
        raise TraceError("ML experiment quota exceeds 64 MiB")
    with _new_output(target) as staging:
        data = load_training_data(prepared, source=source)
        with closing(data.iter_windows()) as windows:
            model = fit_isolation_forest(windows, data.preprocessors["normal_train"], seed=seed)
        with closing(data.iter_windows()) as windows:
            selection = choose_validation_threshold(
                _scored_windows(windows, model, validation_only=True)
            )
        # Model and threshold are frozen before any test score is calculated.
        threshold = {
            "format": "autocan-validation-threshold",
            "format_version": 1,
            "split": "validation",
            "comparison": "ge",
            "objective": "pooled window F1",
            "tie_break": "highest threshold",
            **asdict(selection),
        }
        model_meta = {
            "format": "autocan-isolation-forest",
            "format_version": 1,
            "model_id": MODEL_ID,
            "model_version": MODEL_VERSION,
            "parameters": model.estimator.get_params(deep=False),
            "feature_columns": list(data.columns),
            "fit_rows": model.fit_rows,
            "fit_rows_sha256": model.fit_rows_sha256,
            "fit_scope": "eligible windows of entirely normal train runs",
            "preprocessing_scope": "normal_train",
            "score": "-score_samples; higher means anomaly",
            "prepared_semantic_sha256": data.manifest["semantic_sha256"],
            "lineage": data.manifest["lineage"],
            "preprocessing_sha256": _file_hash(prepared / "preprocessing/normal_train.json"),
        }

        def save(name: str, raw: bytes) -> None:
            used = sum(p.stat().st_size for p in staging.rglob("*") if p.is_file())
            if used + len(raw) > max_bytes:
                raise TraceError("ML experiment output quota exceeded")
            (staging / name).write_bytes(raw)

        save("model.json", canonical_bytes(model_meta) + b"\n")
        save("threshold.json", canonical_bytes(threshold) + b"\n")
        save("model.pkl", pickle.dumps(model.estimator, protocol=5))
        save("preprocessing.json", (prepared / "preprocessing/normal_train.json").read_bytes())
        save("split.json", (prepared / "split.json").read_bytes())
        targets = []
        predictions: dict[str, list[WindowScore]] = {name: [] for name in (*RULE_IDS, MODEL_ID)}
        policy = None
        writer = _TableWriter(staging, "scores.parquet", SCORE_SCHEMA, max_bytes)
        try:
            with closing(data.iter_windows()) as windows:
                for window, score in _scored_windows(windows, model):
                    raw = dict(zip(data.columns, window.features, strict=True))
                    evidence = baseline_window_scores(raw, data.configuration)
                    if policy is None:
                        policy = evidence
                    targets.append(WindowTruth(window.key, window.label))
                    for name in (*RULE_IDS, MODEL_ID):
                        value = score if name == MODEL_ID else evidence[name].score
                        cutoff = (
                            selection.threshold if name == MODEL_ID else evidence[name].threshold
                        )
                        comparison = "ge" if name == MODEL_ID else evidence[name].comparison
                        predictions[name].append(WindowScore(window.key, value))
                        writer.write(
                            {
                                "row_index": window.key.row_index,
                                "model_id": name,
                                "score": value,
                                "threshold": cutoff,
                                "comparison": comparison,
                                "anomalous": value >= cutoff
                                if comparison == "ge"
                                else value > cutoff,
                            }
                        )
        finally:
            writer.close()
        assert policy is not None
        rows = [
            evaluate_windows(
                targets,
                predictions[name],
                model_id=name,
                split=part,
                threshold=selection.threshold if name == MODEL_ID else policy[name].threshold,
                comparison="ge" if name == MODEL_ID else policy[name].comparison,
            )
            for part in SPLITS
            for name in (*RULE_IDS, MODEL_ID)
        ]
        metrics = {
            "format": "autocan-ml-window-metrics",
            "format_version": 1,
            "experiment_version": EXPERIMENT_VERSION,
            "model_version": MODEL_VERSION,
            "evaluator_version": WINDOW_EVALUATOR_VERSION,
            "baseline_detector_id": DETECTOR_ID,
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "prepared_semantic_sha256": data.manifest["semantic_sha256"],
            "lineage": data.manifest["lineage"],
            "configuration": asdict(data.configuration),
            "model": model_meta,
            "selection": threshold,
            "rows": rows,
        }
        save("metrics.json", canonical_bytes(metrics) + b"\n")
        save(
            "report.md",
            _report(
                rows,
                str(data.manifest["semantic_sha256"]),
                model_note="IsolationForest fit: eligible windows of entirely normal train runs; "
                "frozen normal-train preprocessing. Threshold: maximum validation F1; "
                "highest threshold breaks ties. Test scored after selection; OOD not loaded.",
            ).encode(),
        )
        implementation = sha256()
        for name in (
            "isolation_forest",
            "ml_experiment",
            "preprocessing",
            "training_artifacts",
            "window_evaluation",
            "window_benchmark",
        ):
            implementation.update(
                name.encode() + b"\0" + files("autocan").joinpath(f"{name}.py").read_bytes()
            )
        manifest = _finish(
            staging,
            {
                "format": "autocan-isolation-forest-experiment",
                "format_version": 1,
                "status": "complete",
                "experiment_version": EXPERIMENT_VERSION,
                "model_version": MODEL_VERSION,
                "evaluator_version": WINDOW_EVALUATOR_VERSION,
                "baseline_detector_id": DETECTOR_ID,
                "feature_schema_version": FEATURE_SCHEMA_VERSION,
                "prepared_manifest_sha256": _file_hash(prepared / "manifest.json"),
                "prepared_semantic_sha256": data.manifest["semantic_sha256"],
                "source_manifest_sha256": data.manifest["source_manifest_sha256"],
                "lineage": data.manifest["lineage"],
                "configuration": asdict(data.configuration),
                "seed": seed,
                "implementation_sha256": implementation.hexdigest(),
                "package_versions": {
                    **_packages(),
                    **{
                        name: version(name)
                        for name in (
                            "pyarrow",
                            "numpy",
                            "scipy",
                            "scikit-learn",
                            "joblib",
                            "threadpoolctl",
                        )
                    },
                },
                **_git_provenance(),
                "models_trained": [MODEL_ID],
                "ood_used": False,
                "test_policy": "score only after model/threshold freeze; never used in selection",
                "model_persistence": "locally generated trusted pickle; no import endpoint",
                "semantic_sha256": sha256(
                    canonical_bytes(
                        {
                            "metrics": metrics,
                            "scores_semantic_sha256": writer.digest.hexdigest(),
                        }
                    )
                ).hexdigest(),
            },
            {"scores.parquet": writer},
            max_bytes,
        )
    return manifest
