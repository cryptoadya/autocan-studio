"""Persist causal numeric windows, separate keys/truth and train-fitted transforms."""

from collections.abc import Generator
from contextlib import ExitStack, closing
from dataclasses import asdict
from hashlib import sha256
from importlib.metadata import version
from importlib.resources import files
from pathlib import Path
from typing import cast

import pyarrow as pa
import pyarrow.parquet as pq

from autocan.benchmark import _fault_episodes
from autocan.clock import SimulationClock
from autocan.evaluation import SPLITS, FaultEpisode
from autocan.factory import _read_json, verify_factory
from autocan.features import (
    FEATURE_SCHEMA_VERSION,
    FeatureConfig,
    FeaturePipeline,
    FeatureWindow,
    replay_observations,
)
from autocan.parquet import ROW_GROUP_ROWS, _finish, _new_output, _TableWriter, open_observer
from autocan.preprocessing import FitSample, fit_training
from autocan.traces import TraceError, _git_provenance, _packages
from autocan.training_split import make_training_split, validate_training_split
from autocan.validation import canonical_bytes, integer, number

TRAINING_DATA_VERSION = "causal-windows-1"
MAX_ROWS = 100_000
MAX_EXPORT_BYTES = 64 * 1024 * 1024
KEY_SCHEMA = pa.schema(
    [
        pa.field("row_index", pa.int64(), nullable=False),
        pa.field("run_id", pa.string(), nullable=False),
        pa.field("split", pa.string(), nullable=False),
        pa.field("start_ns", pa.int64(), nullable=False),
        pa.field("end_ns", pa.int64(), nullable=False),
        pa.field("eligible", pa.bool_(), nullable=False),
    ]
)
LABEL_SCHEMA = pa.schema(
    [
        pa.field("row_index", pa.int64(), nullable=False),
        pa.field("label", pa.bool_()),
        pa.field("fault_ids", pa.list_(pa.string()), nullable=False),
        pa.field("fault_types", pa.list_(pa.string()), nullable=False),
        pa.field("normal_run", pa.bool_(), nullable=False),
    ]
)


def window_features(window: FeatureWindow) -> dict[str, float | None]:
    """An explicit allowlist: no timestamps, identities, labels or provenance."""
    values: dict[str, int | float | bool | None] = {
        "message_count": window.message_count,
        "rpm_mean": window.rpm_mean,
        "rpm_median": window.rpm_median,
        "rpm_std": window.rpm_std,
        "rpm_derivative_max": window.rpm_derivative_max,
        "wheel_residual_max": window.wheel_residual_max,
        "rpm_missing": window.rpm_missing,
        "wheels_missing": window.wheels_missing,
    }
    nullable = {"rpm_mean", "rpm_median", "rpm_std", "rpm_derivative_max", "wheel_residual_max"}
    for message in sorted(window.messages, key=lambda m: m.can_id):
        prefix = f"can_{message.can_id}_"
        for name in (
            "count",
            "age_ns",
            "timeout_ns",
            "timed_out",
            "interval_min_ns",
            "interval_median_ns",
            "interval_max_ns",
            "interval_jitter_ns",
        ):
            values[prefix + name] = getattr(message, name)
            if name.startswith("interval_"):
                nullable.add(prefix + name)
    result = {
        key: None
        if value is None
        else float(value)
        if isinstance(value, bool)
        else number(value, "window feature", minimum=-1e100, maximum=1e100)
        for key, value in sorted(values.items())
    }
    result.update({f"missing__{key}": float(values[key] is None) for key in sorted(nullable)})
    return dict(sorted(result.items()))


def window_label(
    start_ns: int, end_ns: int, eligible: bool, faults: list[FaultEpisode]
) -> dict[str, object]:
    selected = [
        fault
        for fault in faults
        if fault.end_ns > fault.start_ns and fault.start_ns <= end_ns and fault.end_ns > start_ns
    ]
    return {
        "label": bool(selected) if eligible else None,
        "fault_ids": sorted({fault.fault_id for fault in selected}),
        "fault_types": sorted({fault.fault_type for fault in selected}),
    }


def _rows(root: Path, name: str) -> Generator[dict[str, object], None, None]:
    # These are private staging artifacts produced in this invocation, never arbitrary imports.
    with pq.ParquetFile(
        str(root / name), pre_buffer=False, page_checksum_verification=True
    ) as table:
        if table.metadata.num_rows > MAX_ROWS:
            raise TraceError("training table row quota exceeded")
        for i in range(table.num_row_groups):
            if table.metadata.row_group(i).num_rows > ROW_GROUP_ROWS:
                raise TraceError("training row-group quota exceeded")
        for batch in table.iter_batches(batch_size=ROW_GROUP_ROWS, use_threads=False):
            for row in batch.to_pylist():
                yield cast(dict[str, object], row)


def _samples(root: Path, columns: tuple[str, ...]) -> Generator[FitSample, None, None]:
    with (
        closing(_rows(root, "features.parquet")) as features,
        closing(_rows(root, "windows.parquet")) as windows,
        closing(_rows(root, "labels/windows.parquet")) as labels,
    ):
        for index, (raw, key, label) in enumerate(zip(features, windows, labels, strict=True)):
            if key["row_index"] != index or label["row_index"] != index:
                raise TraceError("training table alignment mismatch")
            yield FitSample(
                index,
                cast(str, key["split"]),
                cast(bool, key["eligible"]),
                cast(bool, label["normal_run"]),
                tuple(cast(float | None, raw[name]) for name in columns),
            )


def prepare_training_data(
    source: Path,
    target: Path,
    *,
    config: FeatureConfig | None = None,
    split_seed: int = 42,
    split_path: Path | None = None,
    max_bytes: int = MAX_EXPORT_BYTES,
) -> dict[str, object]:
    config = config or FeatureConfig()
    if integer(max_bytes, "training output quota", minimum=4096) > MAX_EXPORT_BYTES:
        raise ValueError("training output quota must be <= 64 MiB")
    with _new_output(target) as staging:
        meta = verify_factory(source)
        split = (
            validate_training_split(meta, _read_json(split_path))
            if split_path is not None
            else make_training_split(meta, seed=split_seed)
        )
        (staging / "split.json").write_bytes(canonical_bytes(split) + b"\n")
        membership = {row["index"]: row["split"] for row in split["assignments"]}
        writers: dict[str, _TableWriter] = {}
        columns: tuple[str, ...] = ()
        feature_schema: pa.Schema | None = None
        count = 0
        windows_semantic = sha256()
        stratum_windows: dict[tuple[str, str, str], dict[str, int]] = {}
        coverage = {
            part: {
                "runs": 0,
                "normal_runs": 0,
                "windows": 0,
                "eligible_windows": 0,
                "positive_windows": 0,
                "normal_run_windows": 0,
            }
            for part in SPLITS
        }
        with ExitStack() as stack:
            for name, schema in (
                ("windows.parquet", KEY_SCHEMA),
                ("labels/windows.parquet", LABEL_SCHEMA),
            ):
                writer = writers[name] = _TableWriter(staging, name, schema, max_bytes)
                stack.callback(writer.close)
            for run in meta["runs"]:
                partition = membership[run["index"]]
                coverage[partition]["runs"] += 1
                cell = stratum_windows.setdefault(
                    (partition, run["mode"], run["fault_type"] or "normal"),
                    {"eligible_windows": 0, "positive_windows": 0, "normal_run_windows": 0},
                )
                bounds: list[dict[str, object]] = []

                def on_window(
                    window: FeatureWindow,
                    run_id: str = run["run_id"],
                    partition_name: str = partition,
                    run_bounds: list[dict[str, object]] = bounds,
                ) -> None:
                    nonlocal count, columns, feature_schema
                    if count >= MAX_ROWS:
                        raise TraceError("training window row quota exceeded")
                    vector = window_features(window)
                    if not columns:
                        columns = tuple(vector)
                        feature_schema = pa.schema(
                            [pa.field(name, pa.float64()) for name in columns]
                        )
                        writer = writers["features.parquet"] = _TableWriter(
                            staging, "features.parquet", feature_schema, max_bytes
                        )
                        stack.callback(writer.close)
                    if tuple(vector) != columns:
                        raise TraceError("feature columns changed between runs")
                    eligible = window.end_ns >= max(
                        message.timeout_ns for message in window.messages
                    )
                    key = {
                        "row_index": count,
                        "run_id": run_id,
                        "split": partition_name,
                        "start_ns": window.start_ns,
                        "end_ns": window.end_ns,
                        "eligible": eligible,
                    }
                    writers["features.parquet"].write(cast(dict[str, object], vector))
                    writers["windows.parquet"].write(key)
                    windows_semantic.update(
                        canonical_bytes({k: v for k, v in key.items() if k != "run_id"}) + b"\n"
                    )
                    run_bounds.append(key)
                    count += 1

                with open_observer(source / run["path"] / "observer") as observed:
                    clock = SimulationClock()
                    pipeline = FeaturePipeline(
                        clock,
                        profile_id=observed.profile_id,
                        receiver_id=observed.receiver_id,
                        end_ns=observed.end_ns,
                        config=config,
                        on_window=on_window,
                    )
                    replay_observations(
                        observed.observations, clock, pipeline, end_ns=observed.end_ns
                    )
                # Feature extraction finishes before reading truth for this run.
                faults = _fault_episodes(source / run["path"] / "trace", run["duration_ns"])
                normal = not faults
                coverage[partition]["normal_runs"] += int(normal)
                for key in bounds:
                    label = window_label(
                        integer(key["start_ns"], "start"),
                        integer(key["end_ns"], "end"),
                        cast(bool, key["eligible"]),
                        faults,
                    )
                    writers["labels/windows.parquet"].write(
                        {"row_index": key["row_index"], "normal_run": normal, **label}
                    )
                    coverage[partition]["windows"] += 1
                    if key["eligible"]:
                        coverage[partition]["eligible_windows"] += 1
                        coverage[partition]["positive_windows"] += int(label["label"] is True)
                        coverage[partition]["normal_run_windows"] += int(normal)
                        cell["eligible_windows"] += 1
                        cell["positive_windows"] += int(label["label"] is True)
                        cell["normal_run_windows"] += int(normal)
        if not columns or feature_schema is None:
            raise ValueError("no feature windows available for training preparation")
        if any(
            not values["positive_windows"] or not values["normal_run_windows"]
            for values in coverage.values()
        ):
            raise ValueError(
                "coverage requires eligible positive and normal-run windows in every split"
            )
        lineage = {
            # This physical split fingerprint binds exact source identities; the
            # logical dataset hash below deliberately excludes regenerated UUIDs.
            "dataset_semantic_sha256": meta["semantic_sha256"],
            "split_sha256": sha256(canonical_bytes(split)).hexdigest(),
            "feature_columns_sha256": sha256(canonical_bytes(columns)).hexdigest(),
        }
        if any(
            not values["eligible_windows"]
            or not (
                values["normal_run_windows"] if kind == "normal" else values["positive_windows"]
            )
            for (_part, _mode, kind), values in stratum_windows.items()
        ):
            raise ValueError(
                "coverage requires eligible target windows in every mode/fault/split stratum"
            )
        fitted_states: dict[str, dict[str, object]] = {}
        for scope, normal_only in (("all_train", False), ("normal_train", True)):
            with closing(_samples(staging, columns)) as samples:
                fitted = fit_training(samples, columns, normal_only=normal_only)
            fitted_states[scope] = fitted.as_dict()
            (staging / "preprocessing").mkdir(exist_ok=True)
            (staging / f"preprocessing/{scope}.json").write_bytes(
                canonical_bytes(fitted.as_dict()) + b"\n"
            )
            name = f"scaled/{scope}.parquet"
            with closing(_samples(staging, columns)) as samples:
                writer = writers[name] = _TableWriter(staging, name, feature_schema, max_bytes)
                try:
                    for sample in samples:
                        values = fitted.transform(sample.values, columns=columns)
                        writer.write(dict(zip(columns, values, strict=True)))
                finally:
                    writer.close()
        (staging / "coverage.json").write_bytes(
            canonical_bytes(
                {
                    "strata": split["strata"],
                    "partitions": coverage,
                    "stratum_windows": [
                        {
                            "split": part,
                            "mode": mode,
                            "fault_type": None if kind == "normal" else kind,
                            **values,
                        }
                        for (part, mode, kind), values in sorted(stratum_windows.items())
                    ],
                }
            )
            + b"\n"
        )
        semantic = sha256(
            canonical_bytes(
                {
                    "version": TRAINING_DATA_VERSION,
                    "configuration": asdict(config),
                    "source_semantic_sha256": meta["semantic_sha256"],
                    "split": {
                        **split,
                        "assignments": [
                            {k: v for k, v in row.items() if k != "run_id"}
                            for row in split["assignments"]
                        ],
                    },
                    "windows_semantic_sha256": windows_semantic.hexdigest(),
                    "tables": {
                        name: writer.digest.hexdigest()
                        for name, writer in writers.items()
                        if name != "windows.parquet"
                    },
                    "preprocessing": fitted_states,
                }
            )
        ).hexdigest()
        implementation = sha256()
        for name in (
            "features",
            "evaluation",
            "benchmark",
            "training_split",
            "preprocessing",
            "training_data",
        ):
            implementation.update(
                name.encode() + b"\0" + files("autocan").joinpath(f"{name}.py").read_bytes()
            )
        manifest = _finish(
            staging,
            {
                "format": "autocan-training-data",
                "format_version": 1,
                "status": "complete",
                "training_data_version": TRAINING_DATA_VERSION,
                "feature_schema_version": FEATURE_SCHEMA_VERSION,
                "feature_columns": list(columns),
                "configuration": asdict(config),
                "lineage": lineage,
                "rows": count,
                "models_trained": [],
                "semantic_sha256": semantic,
                "label_policy": "positive intervention overlaps (start,end]; "
                "first window includes zero; no post-clear grace",
                "eligibility": "window end >= maximum subscribed message timeout; "
                "partial tail omitted",
                "source_manifest_sha256": sha256(
                    (source / "manifest.json").read_bytes()
                ).hexdigest(),
                "implementation_sha256": implementation.hexdigest(),
                "package_versions": {**_packages(), "pyarrow": version("pyarrow")},
                **_git_provenance(),
            },
            writers,
            max_bytes,
        )
    return manifest
