"""Bounded, source-bound loading of the persisted M9 window contract; no fitting."""

from collections.abc import Generator
from contextlib import ExitStack, closing
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from typing import cast

import pyarrow as pa
import pyarrow.parquet as pq

from autocan.benchmark import _fault_episodes
from autocan.evaluation import SPLITS
from autocan.factory import FactoryManifest, _read_json, verify_factory
from autocan.features import FEATURE_SCHEMA_VERSION, FeatureConfig, FeatureWindow, MessageFeatures
from autocan.network_profile import MESSAGE_PLANS
from autocan.parquet import MAX_BUNDLE_MANIFEST_BYTES, MAX_DECODED_BYTES, ROW_GROUP_ROWS, _file_hash
from autocan.preprocessing import Preprocessor
from autocan.traces import TraceError, _safe_file
from autocan.training_data import (
    KEY_SCHEMA,
    LABEL_SCHEMA,
    MAX_EXPORT_BYTES,
    MAX_ROWS,
    TRAINING_DATA_VERSION,
    window_features,
    window_label,
)
from autocan.training_split import TrainingSplit, validate_training_split
from autocan.validation import canonical_bytes, fields, integer, number, object_value
from autocan.window_evaluation import WindowKey

SCOPES = ("all_train", "normal_train")
TABLES = (
    "features.parquet",
    "windows.parquet",
    "labels/windows.parquet",
    "scaled/all_train.parquet",
    "scaled/normal_train.parquet",
)
ARTIFACTS = set(TABLES) | {
    "split.json",
    "coverage.json",
    "preprocessing/all_train.json",
    "preprocessing/normal_train.json",
}


def _checksum(value: object) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise TraceError("invalid training artifact SHA-256")


def _path(root: Path, name: str) -> Path:
    if root.is_symlink() or any(
        root.joinpath(*Path(name).parts[:i]).is_symlink()
        for i in range(1, len(Path(name).parts) + 1)
    ):
        raise TraceError(f"symlink training artifact rejected: {name}")
    return _safe_file(root, name)


def _inventory(root: Path, max_bytes: int) -> dict[str, object]:
    meta = _read_json(_path(root, "manifest.json"), MAX_BUNDLE_MANIFEST_BYTES)
    fields(
        meta,
        {
            "format",
            "format_version",
            "status",
            "training_data_version",
            "feature_schema_version",
            "feature_columns",
            "configuration",
            "lineage",
            "rows",
            "models_trained",
            "semantic_sha256",
            "label_policy",
            "eligibility",
            "source_manifest_sha256",
            "implementation_sha256",
            "package_versions",
            "git_commit",
            "git_dirty",
            "artifacts",
        },
    )
    if (
        meta["format"] != "autocan-training-data"
        or meta["status"] != "complete"
        or integer(meta["format_version"], "training format") != 1
        or meta["training_data_version"] != TRAINING_DATA_VERSION
        or integer(meta["feature_schema_version"], "feature schema") != FEATURE_SCHEMA_VERSION
        or meta["models_trained"] != []
        or meta["label_policy"]
        != "positive intervention overlaps (start,end]; "
        "first window includes zero; no post-clear grace"
        or meta["eligibility"]
        != "window end >= maximum subscribed message timeout; partial tail omitted"
    ):
        raise TraceError("unsupported or incomplete training artifact contract")
    if integer(meta["rows"], "window rows", minimum=1) > MAX_ROWS:
        raise TraceError("training window row quota exceeded")
    for name in ("semantic_sha256", "source_manifest_sha256", "implementation_sha256"):
        _checksum(meta[name])
    packages = object_value(meta["package_versions"], "training package versions")
    if any(not isinstance(value, str) or not value for value in packages.values()):
        raise TraceError("invalid training package versions")
    if meta["git_dirty"] is not None and type(meta["git_dirty"]) is not bool:
        raise TraceError("invalid training Git provenance")
    commit = meta["git_commit"]
    if commit is not None and (
        not isinstance(commit, str)
        or len(commit) != 40
        or any(char not in "0123456789abcdef" for char in commit)
    ):
        raise TraceError("invalid training Git provenance")
    inventory = object_value(meta["artifacts"], "training artifacts")
    if inventory.keys() != ARTIFACTS:
        raise TraceError("unsupported training artifact paths")
    total = _path(root, "manifest.json").stat().st_size
    for name, value in inventory.items():
        info = object_value(value, "training artifact")
        fields(info, {"bytes", "sha256", "records", "semantic_sha256"})
        _checksum(info["sha256"])
        if name in TABLES:
            _checksum(info["semantic_sha256"])
        total += integer(info["bytes"], "artifact bytes")
        if total > max_bytes:
            raise TraceError("training import byte quota exceeded")
        path = _path(root, name)
        if path.stat().st_size != info["bytes"] or _file_hash(path) != info["sha256"]:
            raise TraceError(f"training artifact checksum/size mismatch: {name}")
        if integer(info["records"], "artifact records") != (meta["rows"] if name in TABLES else 0):
            raise TraceError("training artifact record count mismatch")
        if name not in TABLES and info["semantic_sha256"] is not None:
            raise TraceError("unexpected JSON artifact semantic checksum")
    return meta


def _template(factory: FactoryManifest, config: FeatureConfig) -> dict[str, float | None]:
    receiver = factory["configuration"]["receiver_id"]
    messages = tuple(
        MessageFeatures(
            p.can_id,
            0,
            0,
            max(100_000_000, p.period_ns * config.heartbeat_periods),
            False,
            None,
            None,
            None,
            None,
        )
        for p in MESSAGE_PLANS
        if receiver in p.receivers
    )
    return window_features(
        FeatureWindow(0, config.window_ns, 0, messages, None, None, None, None, None, True, True)
    )


def _table_rows(
    root: Path, name: str, schema: pa.Schema, info: dict[str, object]
) -> Generator[dict[str, object], None, None]:
    # Parquet canonicalizes Arrow's list child name from item to element.
    if name == "labels/windows.parquet":
        schema = pa.schema(
            [
                field.with_type(pa.list_(pa.field("element", field.type.value_type)))
                if pa.types.is_list(field.type)
                else field
                for field in schema
            ]
        )
    try:
        with pq.ParquetFile(
            str(_path(root, name)),
            pre_buffer=False,
            page_checksum_verification=True,
            thrift_string_size_limit=MAX_BUNDLE_MANIFEST_BYTES,
            thrift_container_size_limit=MAX_BUNDLE_MANIFEST_BYTES,
            arrow_extensions_enabled=False,
        ) as table:
            if not table.schema_arrow.equals(schema, check_metadata=True):
                raise TraceError(f"unsupported training table schema: {name}")
            if table.metadata.num_rows != info["records"]:
                raise TraceError(f"training table record count mismatch: {name}")
            decoded = 0
            for i in range(table.num_row_groups):
                group = table.metadata.row_group(i)
                decoded += group.total_byte_size
                if group.num_rows > ROW_GROUP_ROWS or decoded > MAX_DECODED_BYTES:
                    raise TraceError("training decoded size or row-group quota exceeded")
            digest = sha256()
            for batch in table.iter_batches(batch_size=ROW_GROUP_ROWS, use_threads=False):
                for value in batch.to_pylist():
                    row = object_value(value, "training table row")
                    digest.update(canonical_bytes(row) + b"\n")
                    yield row
            if digest.hexdigest() != info["semantic_sha256"]:
                raise TraceError(f"training table semantic checksum mismatch: {name}")
    except pa.ArrowException as exc:
        raise TraceError(f"invalid training Parquet table: {name}: {exc}") from exc


@dataclass(frozen=True, slots=True)
class PreparedWindow:
    key: WindowKey
    features: tuple[float | None, ...]
    label: bool | None
    normal_run: bool


@dataclass(frozen=True, slots=True)
class LoadedTrainingData:
    directory: Path
    source: Path
    manifest: dict[str, object]
    configuration: FeatureConfig
    columns: tuple[str, ...]
    split: TrainingSplit
    preprocessors: dict[str, Preprocessor]
    _factory: FactoryManifest
    _feature_template: dict[str, float | None]

    def iter_windows(self) -> Generator[PreparedWindow, None, None]:
        """Stream verified rows; exhaust to complete table/coverage/hash checks."""
        schema = pa.schema([pa.field(name, pa.float64()) for name in self.columns])
        inventory = object_value(self.manifest["artifacts"], "artifacts")
        membership = {row["index"]: row["split"] for row in self.split["assignments"]}
        partitions = {
            part: dict.fromkeys(
                (
                    "runs",
                    "normal_runs",
                    "windows",
                    "eligible_windows",
                    "positive_windows",
                    "normal_run_windows",
                ),
                0,
            )
            for part in SPLITS
        }
        strata: dict[tuple[str, str, str], dict[str, int]] = {}
        fit_hashes = {scope: sha256() for scope in SCOPES}
        fit_counts = dict.fromkeys(SCOPES, 0)
        observed = {scope: [0] * len(self.columns) for scope in SCOPES}
        windows_hash = sha256()
        count = 0
        timeouts = {
            name: value
            for name, value in self._feature_template.items()
            if name.endswith("_timeout_ns")
        }
        warmup = max(cast(float, value) for value in timeouts.values())
        with ExitStack() as stack:
            streams = {
                name: stack.enter_context(
                    closing(
                        _table_rows(
                            self.directory,
                            name,
                            KEY_SCHEMA
                            if name == "windows.parquet"
                            else LABEL_SCHEMA
                            if name == "labels/windows.parquet"
                            else schema,
                            object_value(inventory[name], "artifact"),
                        )
                    )
                )
                for name in TABLES
            }
            for run in self._factory["runs"]:
                part = membership[run["index"]]
                faults = _fault_episodes(self.source / run["path"] / "trace", run["duration_ns"])
                normal = not faults
                counts = partitions[part]
                counts["runs"] += 1
                counts["normal_runs"] += int(normal)
                cell = strata.setdefault(
                    (part, run["mode"], run["fault_type"] or "normal"),
                    dict.fromkeys(
                        ("eligible_windows", "positive_windows", "normal_run_windows"), 0
                    ),
                )
                for end in range(
                    self.configuration.window_ns,
                    run["duration_ns"] + 1,
                    self.configuration.window_ns,
                ):
                    actual: dict[str, dict[str, object]] = {}
                    for name, stream in streams.items():
                        row = next(stream, None)
                        if row is None:
                            raise TraceError("training table alignment: missing window row")
                        actual[name] = row
                    key = WindowKey(
                        count,
                        run["run_id"],
                        part,
                        end - self.configuration.window_ns,
                        end,
                        end >= warmup,
                    )
                    expected_key = asdict(key)
                    if actual["windows.parquet"] != expected_key:
                        raise TraceError("training key alignment/split/eligibility mismatch")
                    label = window_label(key.start_ns, end, key.eligible, faults)
                    if actual["labels/windows.parquet"] != {
                        "row_index": count,
                        "normal_run": normal,
                        **label,
                    }:
                        raise TraceError("training label alignment/source mismatch")
                    raw = actual["features.parquet"]
                    values = []
                    for name in self.columns:
                        value = raw[name]
                        if value is None and self._feature_template[name] is not None:
                            raise TraceError(f"unexpected null training feature: {name}")
                        values.append(
                            None
                            if value is None
                            else number(value, "window feature", minimum=0, maximum=1e100)
                        )
                        if name.startswith("missing__") and value != float(raw[name[9:]] is None):
                            raise TraceError("training missing mask mismatch")
                        if name in timeouts and value != timeouts[name]:
                            raise TraceError("training heartbeat timeout/configuration mismatch")
                    features = tuple(values)
                    for scope, state in self.preprocessors.items():
                        transformed = state.transform(features, columns=self.columns)
                        if list(actual[f"scaled/{scope}.parquet"].values()) != list(transformed):
                            raise TraceError(f"scaled training table/state mismatch: {scope}")
                        if part == "train" and key.eligible and (normal or not state.normal_only):
                            fit_counts[scope] += 1
                            fit_hashes[scope].update(canonical_bytes(count) + b"\n")
                            for i, value in enumerate(features):
                                observed[scope][i] += int(value is not None)
                    windows_hash.update(
                        canonical_bytes({k: v for k, v in expected_key.items() if k != "run_id"})
                        + b"\n"
                    )
                    counts["windows"] += 1
                    if key.eligible:
                        for target in (counts, cell):
                            target["eligible_windows"] += 1
                            target["positive_windows"] += int(label["label"] is True)
                            target["normal_run_windows"] += int(normal)
                    count += 1
                    yield PreparedWindow(key, features, cast(bool | None, label["label"]), normal)
            for stream in streams.values():
                if next(stream, None) is not None:
                    raise TraceError("training table alignment: extra window rows")
        for scope, state in self.preprocessors.items():
            if (
                state.fit_rows != fit_counts[scope]
                or state.fit_rows_sha256 != fit_hashes[scope].hexdigest()
                or state.observed != tuple(observed[scope])
            ):
                raise TraceError("preprocessor train-only fit scope/fingerprint mismatch")
        coverage = {
            "strata": self.split["strata"],
            "partitions": partitions,
            "stratum_windows": [
                {
                    "split": part,
                    "mode": mode,
                    "fault_type": None if kind == "normal" else kind,
                    **values,
                }
                for (part, mode, kind), values in sorted(strata.items())
            ],
        }
        if any(
            not counts["positive_windows"] or not counts["normal_run_windows"]
            for counts in partitions.values()
        ) or any(
            not values["eligible_windows"]
            or not values["normal_run_windows" if kind == "normal" else "positive_windows"]
            for (_part, _mode, kind), values in strata.items()
        ):
            raise TraceError("insufficient training window coverage")
        if (
            _read_json(_path(self.directory, "coverage.json"), MAX_BUNDLE_MANIFEST_BYTES)
            != coverage
        ):
            raise TraceError("training coverage mismatch")
        semantic = sha256(
            canonical_bytes(
                {
                    "version": TRAINING_DATA_VERSION,
                    "configuration": asdict(self.configuration),
                    "source_semantic_sha256": self._factory["semantic_sha256"],
                    "split": {
                        **self.split,
                        "assignments": [
                            {k: v for k, v in row.items() if k != "run_id"}
                            for row in self.split["assignments"]
                        ],
                    },
                    "windows_semantic_sha256": windows_hash.hexdigest(),
                    "tables": {
                        name: object_value(inventory[name], "artifact")["semantic_sha256"]
                        for name in TABLES
                        if name != "windows.parquet"
                    },
                    "preprocessing": {
                        scope: state.as_dict() for scope, state in self.preprocessors.items()
                    },
                }
            )
        ).hexdigest()
        if count != self.manifest["rows"] or semantic != self.manifest["semantic_sha256"]:
            raise TraceError("training dataset row count or semantic checksum mismatch")


def load_training_data(
    directory: Path, *, source: Path, max_bytes: int = MAX_EXPORT_BYTES
) -> LoadedTrainingData:
    """Validate all saved artifacts before returning a reusable streaming reader."""
    if integer(max_bytes, "training import quota", minimum=4096) > MAX_EXPORT_BYTES:
        raise TraceError("training import quota exceeds 64 MiB")
    meta = _inventory(directory, max_bytes)
    if _file_hash(_safe_file(source, "manifest.json")) != meta["source_manifest_sha256"]:
        raise TraceError("training source manifest lineage mismatch")
    factory = verify_factory(source)
    configuration = object_value(meta["configuration"], "feature configuration")
    fields(configuration, set(asdict(FeatureConfig())))
    config = FeatureConfig(**cast(dict[str, int], configuration))
    if asdict(config) != configuration:
        raise TraceError("noncanonical feature configuration")
    split = validate_training_split(factory, _read_json(_path(directory, "split.json")))
    template = _template(factory, config)
    columns = tuple(template)
    if meta["feature_columns"] != list(columns):
        raise TraceError("training feature columns/order/schema mismatch")
    if meta["lineage"] != {
        "dataset_semantic_sha256": factory["semantic_sha256"],
        "split_sha256": sha256(canonical_bytes(split)).hexdigest(),
        "feature_columns_sha256": sha256(canonical_bytes(columns)).hexdigest(),
    }:
        raise TraceError("training dataset/split/column lineage mismatch")
    if sum(run["duration_ns"] // config.window_ns for run in factory["runs"]) != meta["rows"]:
        raise TraceError("training window row count mismatch")
    states = {}
    for scope in SCOPES:
        state = Preprocessor.from_dict(_read_json(_path(directory, f"preprocessing/{scope}.json")))
        if state.columns != columns or state.normal_only != (scope == "normal_train"):
            raise TraceError("preprocessor columns or training scope mismatch")
        states[scope] = state
    result = LoadedTrainingData(
        directory, source, meta, config, columns, split, states, factory, template
    )
    with closing(result.iter_windows()) as rows:
        for _ in rows:
            pass
    return result
