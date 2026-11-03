"""Load trusted local forest bundles after bounded integrity and lineage checks.

Checksums are integrity checks, not authentication. The CLI caller must trust the
local training process that produced these pickles; no network import is exposed.
"""

import pickle
from dataclasses import dataclass
from hashlib import sha256
from importlib.metadata import version
from importlib.resources import files
from pathlib import Path

from sklearn.ensemble import IsolationForest, RandomForestClassifier

from autocan.factory import _read_json
from autocan.features import DETECTOR_ID, FEATURE_SCHEMA_VERSION
from autocan.isolation_forest import MODEL_ID, MODEL_VERSION, FittedForest
from autocan.ml_experiment import RF_EXPERIMENT_VERSION, FittedModel
from autocan.parquet import MAX_BUNDLE_MANIFEST_BYTES, _file_hash
from autocan.preprocessing import Preprocessor
from autocan.random_forest import MODEL_ID as RF_ID
from autocan.random_forest import MODEL_VERSION as RF_VERSION
from autocan.random_forest import FittedRandomForest
from autocan.traces import TraceError, _packages
from autocan.training_artifacts import LoadedTrainingData, _path
from autocan.validation import canonical_bytes, integer, number, object_value
from autocan.window_benchmark import MAX_OUTPUT_BYTES
from autocan.window_evaluation import WINDOW_EVALUATOR_VERSION

MODEL_IDS = (MODEL_ID, RF_ID)
MODEL_FILES = ("model.pkl", "model.json", "preprocessing.json", "threshold.json")


@dataclass(frozen=True, slots=True)
class FrozenExperiment:
    manifest: dict[str, object]
    models: dict[str, FittedModel]
    thresholds: dict[str, float]


@dataclass(frozen=True, slots=True)
class FrozenExperimentMetadata:
    manifest: dict[str, object]
    metadata: dict[str, dict[str, object]]
    preprocessors: dict[str, Preprocessor]
    thresholds: dict[str, float]
    seed: int


def inspect_frozen_experiment(root: Path, data: LoadedTrainingData) -> FrozenExperimentMetadata:
    """Validate saved metadata/inventories without deserializing or scoring models."""
    meta = _read_json(_path(root, "manifest.json"), MAX_BUNDLE_MANIFEST_BYTES)
    expected = {
        "format": "autocan-random-forest-experiment",
        "format_version": 1,
        "status": "complete",
        "experiment_version": RF_EXPERIMENT_VERSION,
        "evaluator_version": WINDOW_EVALUATOR_VERSION,
        "baseline_detector_id": DETECTOR_ID,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "model_versions": {MODEL_ID: MODEL_VERSION, RF_ID: RF_VERSION},
        "models_trained": list(MODEL_IDS),
        "ood_used": False,
        "prepared_manifest_sha256": _file_hash(data.directory / "manifest.json"),
        "prepared_semantic_sha256": data.manifest["semantic_sha256"],
        "source_manifest_sha256": data.manifest["source_manifest_sha256"],
        "lineage": data.manifest["lineage"],
        "configuration": data.manifest["configuration"],
    }
    if any(meta.get(k) != v for k, v in expected.items()):
        raise TraceError("incompatible frozen experiment lineage/version")
    packages = object_value(meta.get("package_versions"), "model package versions")
    current = {
        **_packages(),
        **{
            name: version(name)
            for name in ("pyarrow", "numpy", "scipy", "scikit-learn", "joblib", "threadpoolctl")
        },
    }
    if packages != current:
        raise TraceError("frozen model package environment mismatch")
    code = sha256()
    for name in (
        "isolation_forest",
        "random_forest",
        "ml_experiment",
        "preprocessing",
        "training_artifacts",
        "window_evaluation",
        "window_benchmark",
    ):
        code.update(name.encode() + b"\0" + files("autocan").joinpath(f"{name}.py").read_bytes())
    if meta.get("implementation_sha256") != code.hexdigest():
        raise TraceError("frozen training implementation mismatch")
    inventory = object_value(meta.get("artifacts"), "model inventory")
    paths = {"split.json", "scores.parquet", "metrics.json", "report.md"} | {
        f"models/{name}/{file}" for name in MODEL_IDS for file in MODEL_FILES
    }
    if inventory.keys() != paths:
        raise TraceError("unsupported frozen experiment inventory")
    total = _path(root, "manifest.json").stat().st_size
    for name, value in inventory.items():
        info = object_value(value, "model artifact")
        total += integer(info.get("bytes"), "model artifact size")
        if total > MAX_OUTPUT_BYTES:
            raise TraceError("frozen model input quota exceeded")
        path = _path(root, name)
        if path.stat().st_size != info["bytes"] or _file_hash(path) != info.get("sha256"):
            raise TraceError(f"frozen model artifact checksum/size mismatch: {name}")
    if (root / "split.json").read_bytes() != (data.directory / "split.json").read_bytes():
        raise TraceError("frozen model split mismatch")
    metrics = _read_json(root / "metrics.json", MAX_OUTPUT_BYTES)
    if any(
        metrics.get(k) != meta[k]
        for k in (
            "experiment_version",
            "model_versions",
            "evaluator_version",
            "feature_schema_version",
            "prepared_semantic_sha256",
            "lineage",
            "configuration",
        )
    ):
        raise TraceError("frozen experiment metrics mismatch")
    score_info = object_value(inventory["scores.parquet"], "saved scores")
    if (
        meta.get("semantic_sha256")
        != sha256(
            canonical_bytes(
                {
                    "metrics": metrics,
                    "scores_semantic_sha256": score_info.get("semantic_sha256"),
                }
            )
        ).hexdigest()
    ):
        raise TraceError("frozen experiment semantic checksum mismatch")
    model_meta = object_value(metrics.get("models"), "saved model metadata")
    selections = object_value(metrics.get("selections"), "saved threshold selections")
    seed = integer(meta.get("seed"), "saved model seed")
    states: dict[str, Preprocessor] = {}
    metadata: dict[str, dict[str, object]] = {}
    thresholds: dict[str, float] = {}
    # Finish validation for BOTH bundles before loading either pickle.
    for name, scope in ((MODEL_ID, "normal_train"), (RF_ID, "all_train")):
        folder = root / "models" / name
        model = metadata[name] = _read_json(folder / "model.json")
        threshold = _read_json(folder / "threshold.json")
        state = states[name] = Preprocessor.from_dict(_read_json(folder / "preprocessing.json"))
        if (
            state != data.preprocessors[scope]
            or model != model_meta.get(name)
            or threshold != selections.get(name)
            or any(
                model.get(k) != v
                for k, v in {
                    "model_id": name,
                    "feature_columns": list(data.columns),
                    "prepared_semantic_sha256": data.manifest["semantic_sha256"],
                    "lineage": data.manifest["lineage"],
                    "preprocessing_scope": scope,
                    "fit_rows": state.fit_rows,
                    "fit_rows_sha256": state.fit_rows_sha256,
                    "preprocessing_sha256": _file_hash(folder / "preprocessing.json"),
                }.items()
            )
        ):
            raise TraceError("frozen model/preprocessing scope or metadata mismatch")
        if any(
            threshold.get(k) != v
            for k, v in {
                "format": "autocan-validation-threshold",
                "format_version": 1,
                "split": "validation",
                "comparison": "ge",
                "objective": "pooled window F1",
                "tie_break": "highest threshold",
            }.items()
        ):
            raise TraceError("invalid frozen validation threshold policy")
        thresholds[name] = number(
            threshold.get("threshold"), "saved threshold", minimum=0, maximum=1
        )
    return FrozenExperimentMetadata(meta, metadata, states, thresholds, seed)


def load_frozen_experiment(
    root: Path, data: LoadedTrainingData, *, detector: str | None = None
) -> FrozenExperiment:
    """Validate both bundles, then load both or just one detector for isolated RSS."""
    if detector is not None and detector not in MODEL_IDS:
        raise ValueError("unsupported frozen detector")
    frozen = inspect_frozen_experiment(root, data)
    metadata, states, seed = frozen.metadata, frozen.preprocessors, frozen.seed
    models: dict[str, FittedModel] = {}
    for name in MODEL_IDS if detector is None else (detector,):
        model, state = metadata[name], states[name]
        try:
            estimator = pickle.loads((root / f"models/{name}/model.pkl").read_bytes())
        except (pickle.UnpicklingError, EOFError, AttributeError, ImportError, ValueError) as exc:
            raise TraceError(f"invalid trusted model pickle: {name}") from exc
        kind = IsolationForest if name == MODEL_ID else RandomForestClassifier
        if (
            type(estimator) is not kind
            or estimator.get_params(deep=False) != model["parameters"]
            or getattr(estimator, "n_features_in_", None) != len(data.columns)
            or estimator.random_state != seed
            or len(getattr(estimator, "estimators_", ())) != estimator.n_estimators
        ):
            raise TraceError("frozen estimator type, parameters or feature shape mismatch")
        if name == MODEL_ID:
            models[name] = FittedForest(
                estimator, state, state.fit_rows, state.fit_rows_sha256, seed
            )
        else:
            counts = object_value(model.get("fit_class_counts"), "fit class counts")
            models[name] = FittedRandomForest(
                estimator,
                state,
                state.fit_rows,
                state.fit_rows_sha256,
                seed,
                integer(counts.get("anomaly"), "positive train rows", minimum=1),
                str(model["label_rows_sha256"]),
            )
    return FrozenExperiment(frozen.manifest, models, frozen.thresholds)
