"""Bounded validation of already-published ID/OOD scores; no model loading."""

from collections.abc import Generator, Iterable
from contextlib import closing
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
from sys import float_info

import pyarrow as pa
import pyarrow.parquet as pq

from autocan.factory import _read_json
from autocan.features import DETECTOR_ID, FEATURE_SCHEMA_VERSION
from autocan.ml_experiment import SCORE_SCHEMA
from autocan.ml_ood import ML_OOD_VERSION
from autocan.model_artifacts import MODEL_IDS, FrozenExperimentMetadata
from autocan.ood import OOD_VERSION
from autocan.parquet import (
    MAX_BUNDLE_MANIFEST_BYTES,
    MAX_DECODED_BYTES,
    ROW_GROUP_ROWS,
    _file_hash,
)
from autocan.traces import TraceError
from autocan.training_artifacts import LoadedTrainingData, _path, _table_rows
from autocan.training_data import KEY_SCHEMA, MAX_ROWS
from autocan.validation import canonical_bytes, integer, number, object_value
from autocan.window_benchmark import MAX_OUTPUT_BYTES
from autocan.window_evaluation import (
    WINDOW_EVALUATOR_VERSION,
    Comparison,
    WindowKey,
    baseline_window_scores,
)


def saved_policies(
    data: LoadedTrainingData,
    frozen: FrozenExperimentMetadata,
) -> dict[str, tuple[float, Comparison]]:
    # Only the fixed rule cutoffs/comparisons are taken from the existing policy;
    # no observed window is rescored here.
    policy = {
        name: (e.threshold, e.comparison)
        for name, e in baseline_window_scores(data._feature_template, data.configuration).items()
    }
    policy.update({name: (frozen.thresholds[name], "ge") for name in MODEL_IDS})
    return policy


def inspect_saved_ood(
    root: Path,
    data: LoadedTrainingData,
    frozen: FrozenExperimentMetadata,
    *,
    experiment: Path,
    source: Path,
    suite: Path,
    reference: Path,
) -> dict[str, object]:
    meta = _read_json(_path(root, "manifest.json"), MAX_BUNDLE_MANIFEST_BYTES)
    expected = {
        "format": "autocan-ml-ood-evaluation",
        "format_version": 1,
        "status": "complete",
        "evaluation_version": ML_OOD_VERSION,
        "ood_version": OOD_VERSION,
        "evaluator_version": WINDOW_EVALUATOR_VERSION,
        "baseline_detector_id": DETECTOR_ID,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "configuration": asdict(data.configuration),
        "lineage": data.manifest["lineage"],
        "source_manifest_sha256": _file_hash(source / "manifest.json"),
        "prepared_manifest_sha256": _file_hash(data.directory / "manifest.json"),
        "experiment_manifest_sha256": _file_hash(experiment / "manifest.json"),
        "suite_manifest_sha256": _file_hash(suite / "manifest.json"),
        "reference_manifest_sha256": _file_hash(reference / "manifest.json"),
        "seed": frozen.seed,
        "models_trained": [],
        "thresholds_selected": [],
        "id_test_scored": False,
    }
    if any(meta.get(k) != v for k, v in expected.items()):
        raise TraceError("saved OOD scores lineage/version mismatch")
    paths = {
        "features.parquet",
        "windows.parquet",
        "labels/windows.parquet",
        "scores.parquet",
        "metrics.json",
        "protocol.json",
        "split.json",
        "report.md",
    } | {
        f"models/{name}/{file}"
        for name in MODEL_IDS
        for file in ("model.json", "preprocessing.json", "threshold.json")
    }
    inventory = object_value(meta.get("artifacts"), "saved OOD inventory")
    if inventory.keys() != paths:
        raise TraceError("unsupported saved OOD inventory")
    total = _path(root, "manifest.json").stat().st_size
    for name, value in inventory.items():
        info = object_value(value, "saved OOD artifact")
        total += integer(info.get("bytes"), "saved artifact size")
        if total > MAX_OUTPUT_BYTES:
            raise TraceError("saved OOD input quota exceeded")
        path = _path(root, name)
        if path.stat().st_size != info["bytes"] or _file_hash(path) != info.get("sha256"):
            raise TraceError(f"saved OOD checksum/size mismatch: {name}")
    copies = {name: experiment / name for name in paths if name.startswith("models/")}
    copies.update(
        {"split.json": experiment / "split.json", "protocol.json": suite / "protocol.json"}
    )
    if any(
        (root / name).read_bytes() != original.read_bytes() for name, original in copies.items()
    ):
        raise TraceError("saved OOD frozen metadata mismatch")
    metrics = _read_json(root / "metrics.json", MAX_OUTPUT_BYTES)
    if any(
        metrics.get(k) != v
        for k, v in {
            "format": "autocan-ml-ood-metrics",
            "format_version": 1,
            "evaluation_version": ML_OOD_VERSION,
            "evaluator_version": WINDOW_EVALUATOR_VERSION,
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "configuration": asdict(data.configuration),
            "experiment_semantic_sha256": frozen.manifest["semantic_sha256"],
            "suite_semantic_sha256": _read_json(suite / "manifest.json")["semantic_sha256"],
        }.items()
    ):
        raise TraceError("saved OOD metrics binding mismatch")
    if (
        meta.get("semantic_sha256")
        != sha256(
            canonical_bytes(
                {
                    "metrics": metrics,
                    "scores_semantic_sha256": object_value(inventory["scores.parquet"], "scores")[
                        "semantic_sha256"
                    ],
                    "features_semantic_sha256": object_value(
                        inventory["features.parquet"], "features"
                    )["semantic_sha256"],
                }
            )
        ).hexdigest()
    ):
        raise TraceError("saved OOD semantic checksum mismatch")
    return meta


def iter_saved_decisions(
    root: Path,
    manifest: dict[str, object],
    keys: Iterable[WindowKey],
    policies: dict[str, tuple[float, Comparison]],
) -> Generator[dict[str, bool], None, None]:
    """Require five ordered, finite, frozen-policy scores for every saved key."""
    inventory = object_value(manifest["artifacts"], "score inventory")
    info = object_value(inventory["scores.parquet"], "scores")
    if integer(info["records"], "score row count") > MAX_ROWS * len(policies):
        raise TraceError("saved score row quota exceeded")
    count = 0
    digest = sha256()

    def score_rows() -> Generator[dict[str, object], None, None]:
        # Old writers hash records before Arrow converts integral rule cutoffs
        # to float64. Reconstruct that numeric type from the frozen policy below.
        try:
            with pq.ParquetFile(
                str(_path(root, "scores.parquet")),
                pre_buffer=False,
                page_checksum_verification=True,
                arrow_extensions_enabled=False,
                thrift_string_size_limit=MAX_BUNDLE_MANIFEST_BYTES,
                thrift_container_size_limit=MAX_BUNDLE_MANIFEST_BYTES,
            ) as table:
                if not table.schema_arrow.equals(SCORE_SCHEMA, check_metadata=True):
                    raise TraceError("unsupported saved score table schema")
                if table.metadata.num_rows != info["records"]:
                    raise TraceError("saved score record count mismatch")
                decoded = 0
                for index in range(table.num_row_groups):
                    group = table.metadata.row_group(index)
                    decoded += group.total_byte_size
                    if group.num_rows > ROW_GROUP_ROWS or decoded > MAX_DECODED_BYTES:
                        raise TraceError("saved score decoded size/row-group quota exceeded")
                for batch in table.iter_batches(batch_size=ROW_GROUP_ROWS, use_threads=False):
                    for raw in batch.to_pylist():
                        yield object_value(raw, "saved score row")
        except pa.ArrowException as exc:
            raise TraceError(f"invalid saved score Parquet: {exc}") from exc

    with closing(score_rows()) as stream:
        for key in keys:
            if count >= MAX_ROWS:
                raise TraceError("saved window quota exceeded")
            decisions = {}
            for name, (threshold, comparison) in policies.items():
                row = next(stream, None)
                if row is None or row["row_index"] != key.row_index or row["model_id"] != name:
                    raise TraceError("saved score key/model alignment mismatch")
                value = number(row["score"], "saved score", minimum=0, maximum=float_info.max)
                positive = value >= threshold if comparison == "ge" else value > threshold
                if (
                    row["threshold"] != threshold
                    or row["comparison"] != comparison
                    or type(row["anomalous"]) is not bool
                    or row["anomalous"] != positive
                ):
                    raise TraceError("saved score threshold/comparison/decision mismatch")
                decisions[name] = positive
                digest.update(canonical_bytes({**row, "threshold": threshold}) + b"\n")
            count += 1
            yield decisions
        if next(stream, None) is not None:
            raise TraceError("extra saved score rows")
    if info["records"] != count * len(policies):
        raise TraceError("saved score coverage mismatch")
    if digest.hexdigest() != info["semantic_sha256"]:
        raise TraceError("saved score semantic checksum mismatch")


def iter_saved_ood_keys(
    root: Path,
    manifest: dict[str, object],
    expected: Iterable[WindowKey],
) -> Generator[WindowKey, None, None]:
    inventory = object_value(manifest["artifacts"], "OOD inventory")
    info = object_value(inventory["windows.parquet"], "saved windows")
    if integer(info["records"], "saved window rows") > MAX_ROWS:
        raise TraceError("saved OOD window quota exceeded")
    with closing(_table_rows(root, "windows.parquet", KEY_SCHEMA, info)) as stream:
        for key in expected:
            if next(stream, None) != asdict(key):
                raise TraceError("saved OOD window key alignment mismatch")
            yield key
        if next(stream, None) is not None:
            raise TraceError("extra saved OOD windows")
