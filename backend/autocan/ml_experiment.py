"""Frozen forest experiments against fixed rules on the same saved ID splits."""

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
from autocan.random_forest import MODEL_ID as RF_ID
from autocan.random_forest import MODEL_VERSION as RF_VERSION
from autocan.random_forest import FittedRandomForest, fit_random_forest
from autocan.traces import TraceError, _git_provenance, _packages
from autocan.training_artifacts import LoadedTrainingData, PreparedWindow, load_training_data
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
RF_EXPERIMENT_VERSION = "random-forest-comparison-1"
FittedModel = FittedForest | FittedRandomForest
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


def _batches(windows: Iterable[PreparedWindow]) -> Generator[list[PreparedWindow], None, None]:
    batch: list[PreparedWindow] = []
    for row in windows:
        batch.append(row)
        if len(batch) == ROW_GROUP_ROWS:
            yield batch
            batch = []
    if batch:
        yield batch


def _scored_windows(
    windows: Iterable[PreparedWindow],
    model: FittedModel,
    *,
    validation_only: bool = False,
) -> Generator[tuple[PreparedWindow, float], None, None]:
    selected = (
        row
        for row in windows
        if not validation_only or (row.key.split == "validation" and row.key.eligible)
    )
    for batch in _batches(selected):
        yield from zip(batch, model.score(batch), strict=True)


def _model_metadata(name: str, model: FittedModel, data: LoadedTrainingData) -> dict[str, object]:
    supervised = isinstance(model, FittedRandomForest)
    scope = "all_train" if supervised else "normal_train"
    meta: dict[str, object] = {
        "format": "autocan-random-forest" if supervised else "autocan-isolation-forest",
        "format_version": 1,
        "model_id": name,
        "model_version": RF_VERSION if supervised else MODEL_VERSION,
        "parameters": model.estimator.get_params(deep=False),
        "feature_columns": list(data.columns),
        "fit_rows": model.fit_rows,
        "fit_rows_sha256": model.fit_rows_sha256,
        "fit_scope": "eligible labeled train windows"
        if supervised
        else "eligible windows of entirely normal train runs",
        "preprocessing_scope": scope,
        "score": "predict_proba for class 1; higher means anomaly"
        if supervised
        else "-score_samples; higher means anomaly",
        "prepared_semantic_sha256": data.manifest["semantic_sha256"],
        "lineage": data.manifest["lineage"],
        "preprocessing_sha256": _file_hash(data.directory / f"preprocessing/{scope}.json"),
    }
    if isinstance(model, FittedRandomForest):
        positives, negatives = (
            model.fit_positive_windows,
            model.fit_rows - model.fit_positive_windows,
        )
        meta.update(
            {
                "label_rows_sha256": model.label_rows_sha256,
                "fit_class_counts": {"normal": negatives, "anomaly": positives},
                "class_weights": {
                    "normal": model.fit_rows / (2 * negatives),
                    "anomaly": model.fit_rows / (2 * positives),
                },
            }
        )
    return meta


def run_isolation_forest(
    prepared: Path,
    target: Path,
    *,
    source: Path,
    seed: int = 42,
    max_bytes: int = DEFAULT_OUTPUT_BYTES,
) -> dict[str, object]:
    return _run_experiment(
        prepared, target, source=source, seed=seed, max_bytes=max_bytes, include_random_forest=False
    )


def run_random_forest(
    prepared: Path,
    target: Path,
    *,
    source: Path,
    seed: int = 42,
    max_bytes: int = DEFAULT_OUTPUT_BYTES,
) -> dict[str, object]:
    return _run_experiment(
        prepared, target, source=source, seed=seed, max_bytes=max_bytes, include_random_forest=True
    )


def _run_experiment(
    prepared: Path,
    target: Path,
    *,
    source: Path,
    seed: int,
    max_bytes: int,
    include_random_forest: bool,
) -> dict[str, object]:
    if integer(max_bytes, "ML experiment quota", minimum=4096) > MAX_OUTPUT_BYTES:
        raise TraceError("ML experiment quota exceeds 64 MiB")
    experiment_version = RF_EXPERIMENT_VERSION if include_random_forest else EXPERIMENT_VERSION
    with _new_output(target) as staging:
        data = load_training_data(prepared, source=source)
        models: dict[str, FittedModel] = {}
        with closing(data.iter_windows()) as windows:
            models[MODEL_ID] = fit_isolation_forest(
                windows, data.preprocessors["normal_train"], seed=seed
            )
        if include_random_forest:
            with closing(data.iter_windows()) as windows:
                models[RF_ID] = fit_random_forest(
                    windows, data.preprocessors["all_train"], seed=seed
                )
        thresholds: dict[str, dict[str, object]] = {}
        cutoffs: dict[str, float] = {}
        for name, model in models.items():
            with closing(data.iter_windows()) as windows:
                selection = choose_validation_threshold(
                    _scored_windows(windows, model, validation_only=True)
                )
            cutoffs[name] = selection.threshold
            thresholds[name] = {
                "format": "autocan-validation-threshold",
                "format_version": 1,
                "split": "validation",
                "comparison": "ge",
                "objective": "pooled window F1",
                "tie_break": "highest threshold",
                **asdict(selection),
            }
        # Every model and threshold is frozen before any test score is calculated.
        metadata = {name: _model_metadata(name, model, data) for name, model in models.items()}

        def save(name: str, raw: bytes) -> None:
            used = sum(p.stat().st_size for p in staging.rglob("*") if p.is_file())
            if used + len(raw) > max_bytes:
                raise TraceError("ML experiment output quota exceeded")
            path = staging / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)

        for name, model in models.items():
            prefix = f"models/{name}/" if include_random_forest else ""
            scope = metadata[name]["preprocessing_scope"]
            save(prefix + "model.json", canonical_bytes(metadata[name]) + b"\n")
            save(prefix + "threshold.json", canonical_bytes(thresholds[name]) + b"\n")
            save(prefix + "model.pkl", pickle.dumps(model.estimator, protocol=5))
            save(
                prefix + "preprocessing.json",
                (prepared / f"preprocessing/{scope}.json").read_bytes(),
            )
        save("split.json", (prepared / "split.json").read_bytes())
        detector_ids = (*RULE_IDS, *models)
        targets = []
        predictions: dict[str, list[WindowScore]] = {name: [] for name in detector_ids}
        policy = None
        writer = _TableWriter(staging, "scores.parquet", SCORE_SCHEMA, max_bytes)
        try:
            with closing(data.iter_windows()) as windows:
                for batch in _batches(windows):
                    model_scores = {name: model.score(batch) for name, model in models.items()}
                    for index, window in enumerate(batch):
                        raw = dict(zip(data.columns, window.features, strict=True))
                        evidence = baseline_window_scores(raw, data.configuration)
                        if policy is None:
                            policy = evidence
                        targets.append(WindowTruth(window.key, window.label))
                        for name in detector_ids:
                            value = (
                                model_scores[name][index]
                                if name in models
                                else evidence[name].score
                            )
                            cutoff = cutoffs[name] if name in models else evidence[name].threshold
                            comparison = "ge" if name in models else evidence[name].comparison
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
                threshold=cutoffs[name] if name in models else policy[name].threshold,
                comparison="ge" if name in models else policy[name].comparison,
            )
            for part in SPLITS
            for name in detector_ids
        ]
        version_metadata: dict[str, object] = (
            {"model_versions": {name: meta["model_version"] for name, meta in metadata.items()}}
            if include_random_forest
            else {"model_version": MODEL_VERSION}
        )
        metrics = {
            "format": "autocan-forest-comparison-metrics"
            if include_random_forest
            else "autocan-ml-window-metrics",
            "format_version": 1,
            "experiment_version": experiment_version,
            **version_metadata,
            "evaluator_version": WINDOW_EVALUATOR_VERSION,
            "baseline_detector_id": DETECTOR_ID,
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "prepared_semantic_sha256": data.manifest["semantic_sha256"],
            "lineage": data.manifest["lineage"],
            "configuration": asdict(data.configuration),
            **(
                {"models": metadata, "selections": thresholds}
                if include_random_forest
                else {"model": metadata[MODEL_ID], "selection": thresholds[MODEL_ID]}
            ),
            "rows": rows,
        }
        save("metrics.json", canonical_bytes(metrics) + b"\n")
        note = (
            "RandomForest fit: all eligible labeled train windows; frozen all-train preprocessing; "
            "balanced train class weights. IsolationForest fit: eligible windows of entirely "
            "normal train runs; frozen normal-train preprocessing. Both thresholds: "
            "maximum validation F1; "
            "highest threshold breaks ties. Test scored after both selections; OOD not loaded."
            if include_random_forest
            else "IsolationForest fit: eligible windows of entirely normal train runs; "
            "frozen normal-train preprocessing. Threshold: maximum validation F1; "
            "highest threshold breaks ties. Test scored after selection; OOD not loaded."
        )
        save(
            "report.md",
            _report(
                rows,
                str(data.manifest["semantic_sha256"]),
                model_note=note,
                title="RandomForest, IsolationForest and fixed-rule comparison"
                if include_random_forest
                else None,
            ).encode(),
        )
        implementation = sha256()
        for name in (
            "isolation_forest",
            "random_forest",
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
                "format": "autocan-random-forest-experiment"
                if include_random_forest
                else "autocan-isolation-forest-experiment",
                "format_version": 1,
                "status": "complete",
                "experiment_version": experiment_version,
                **version_metadata,
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
                "models_trained": list(models),
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
