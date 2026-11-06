"""Live scoring with one explicitly trusted local M9 experiment model."""

from dataclasses import dataclass
from pathlib import Path

from autocan.features import FEATURE_SCHEMA_VERSION, FeatureConfig, FeatureWindow
from autocan.ml_experiment import FittedModel
from autocan.model_artifacts import load_frozen_experiment
from autocan.random_forest import MODEL_ID
from autocan.traces import LiveModelProvenance, TraceError
from autocan.training_artifacts import PreparedWindow, load_training_data
from autocan.training_data import window_features
from autocan.validation import object_value
from autocan.window_evaluation import WindowKey, WindowScore


@dataclass(frozen=True, slots=True)
class LiveModel:
    configuration: FeatureConfig
    model: FittedModel
    model_sha256: str
    threshold: float

    @property
    def provenance(self) -> LiveModelProvenance:
        return {
            "model_id": MODEL_ID,
            "model_sha256": self.model_sha256,
            "threshold": self.threshold,
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
        }

    def score(self, window: FeatureWindow, *, run_id: str, row_index: int) -> WindowScore:
        vector = window_features(window)
        if (
            window.feature_schema_version != FEATURE_SCHEMA_VERSION
            or tuple(vector) != self.model.preprocessing.columns
        ):
            raise TraceError("live feature schema or column order differs from the frozen model")
        key = WindowKey(
            row_index,
            run_id,
            "live",
            window.start_ns,
            window.end_ns,
            window.end_ns >= max(message.timeout_ns for message in window.messages),
        )
        row = PreparedWindow(key, tuple(vector.values()), None, False)
        return WindowScore(key, self.model.score([row])[0])


def load_live_model(experiment: Path, prepared: Path, source: Path) -> LiveModel:
    """Verify lineage and load only the Random Forest estimator from local artifacts."""
    data = load_training_data(prepared, source=source)
    if data._factory["configuration"]["receiver_id"] != "observer":
        raise TraceError("live scoring requires a model trained for the network observer")
    frozen = load_frozen_experiment(experiment, data, detector=MODEL_ID)
    inventory = object_value(frozen.manifest["artifacts"], "model inventory")
    artifact = object_value(inventory[f"models/{MODEL_ID}/model.pkl"], "model artifact")
    return LiveModel(
        data.configuration,
        frozen.models[MODEL_ID],
        str(artifact["sha256"]),
        frozen.thresholds[MODEL_ID],
    )
