"""Prepared tables preserve causal inputs, separate truth and balanced groups."""

import json
from pathlib import Path
from typing import cast

import pyarrow.parquet as pq
import pytest

from autocan.factory import FactoryManifest, RunSummary


def test_stratified_split_guarantees_mode_type_coverage_and_duplicate_groups() -> None:
    from autocan.training_split import make_training_split, validate_training_split

    rows: list[RunSummary] = []
    for kind in (None, "signal_spike"):
        for _i in range(6):
            index = len(rows)
            rows.append(
                {
                    "index": index,
                    "run_id": f"r{index}",
                    "path": f"runs/{index:04d}",
                    "mode": "acceleration",
                    "seed": index,
                    "duration_ns": 8_000_000_000,
                    "fault_type": kind,
                    "scenario_sha256": f"s{index}",
                    "trace_semantic_sha256": f"t{index}",
                    "observation_semantic_sha256": f"o{index}",
                }
            )
    rows[1]["observation_semantic_sha256"] = rows[0]["observation_semantic_sha256"]
    meta = cast(
        FactoryManifest,
        {
            "runs": rows,
            "semantic_sha256": "dataset",
            "configuration": {"modes": ["acceleration"], "fault_types": ["signal_spike"]},
        },
    )
    split = make_training_split(meta, seed=42)
    assert split == make_training_split(meta, seed=42)
    assert split["assignments"][0]["split"] == split["assignments"][1]["split"]
    by_index = {r["index"]: r for r in rows}
    for name in ("train", "validation", "test"):
        assert {
            by_index[r["index"]]["fault_type"] for r in split["assignments"] if r["split"] == name
        } == {None, "signal_spike"}
    validate_training_split(meta, split)
    changed = {**split, "assignments": split["assignments"][:-1]}
    with pytest.raises(ValueError, match="split"):
        validate_training_split(meta, changed)
    meta["configuration"]["fault_types"] = ["signal_spike", "ecu_offline"]
    with pytest.raises(ValueError, match="coverage"):
        make_training_split(meta, seed=42)


@pytest.fixture(scope="module")
def source(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from autocan.factory import FactoryConfig, generate_factory

    root = tmp_path_factory.mktemp("ml-prep-source") / "collection"
    generate_factory(
        FactoryConfig(
            runs=12,
            seed=12,
            modes=("acceleration",),
            fault_runs=9,
            fault_types=("ecu_offline", "signal_spike", "cross_signal_inconsistency"),
            min_duration_ns=8_000_000_000,
            max_duration_ns=8_000_000_000,
        ),
        root,
    )
    return root


def test_prepared_tables_and_train_only_states_are_reproducible(
    source: Path, tmp_path: Path
) -> None:
    from autocan.cli import main
    from autocan.preprocessing import Preprocessor

    first, second = tmp_path / "first", tmp_path / "second"
    for path in (first, second):
        assert main(["prepare-ml", str(source), "--output", str(path)]) == 0
    for name in (
        "features.parquet",
        "windows.parquet",
        "labels/windows.parquet",
        "scaled/all_train.parquet",
        "scaled/normal_train.parquet",
        "split.json",
        "coverage.json",
        "preprocessing/all_train.json",
        "preprocessing/normal_train.json",
    ):
        assert (first / name).read_bytes() == (second / name).read_bytes()
    matrix = pq.read_table(first / "features.parquet")
    keys = pq.read_table(first / "windows.parquet").to_pylist()
    labels = pq.read_table(first / "labels/windows.parquet").to_pylist()
    assert matrix.num_rows == len(keys) == len(labels) == 192
    assert not set(matrix.column_names) & {
        "run_id",
        "split",
        "row_index",
        "label",
        "fault_id",
        "mode",
        "seed",
        "start_ns",
        "end_ns",
    }
    assert any(name.startswith("missing__") for name in matrix.column_names)
    for key, label in zip(keys, labels, strict=True):
        assert key["row_index"] == label["row_index"]
        assert key["eligible"] is True
    all_fit = json.loads((first / "preprocessing/all_train.json").read_text())
    normal_fit = json.loads((first / "preprocessing/normal_train.json").read_text())
    assert all_fit["fit_rows"] == 64 and normal_fit["fit_rows"] == 16
    assert Preprocessor.from_dict(all_fit).fit_rows == 64
    assert Preprocessor.from_dict(normal_fit).normal_only is True
    assert json.loads((first / "manifest.json").read_text())["models_trained"] == []
    assert all(row["eligible"] for row in keys)
    coverage = json.loads((first / "coverage.json").read_text())
    assert len(coverage["stratum_windows"]) == 12
    assert all(row["eligible_windows"] > 0 for row in coverage["stratum_windows"])
    assert (
        json.loads((first / "manifest.json").read_text())["semantic_sha256"]
        == json.loads((second / "manifest.json").read_text())["semantic_sha256"]
    )


def test_warmup_windows_are_retained_with_missing_labels_and_excluded_from_fit(
    source: Path, tmp_path: Path
) -> None:
    from autocan.features import FeatureConfig
    from autocan.training_data import prepare_training_data

    output = tmp_path / "short-windows"
    prepare_training_data(source, output, config=FeatureConfig(window_ns=100_000_000))
    windows = pq.read_table(output / "windows.parquet").to_pylist()
    labels = pq.read_table(output / "labels/windows.parquet").to_pylist()
    assert len(windows) == 960
    assert sum(not row["eligible"] for row in windows) == 48
    assert sum(row["label"] is None for row in labels) == 48
    fitted = json.loads((output / "preprocessing/all_train.json").read_text())
    assert fitted["fit_rows"] == 304


def test_window_labels_use_right_closed_window_and_half_open_fault_boundaries() -> None:
    from autocan.evaluation import FaultEpisode
    from autocan.training_data import window_label

    fault = FaultEpisode("f", "signal_spike", 100, 200, (512,))
    assert window_label(0, 100, True, [fault])["label"] is True
    assert window_label(100, 200, True, [fault])["label"] is True
    assert window_label(200, 300, True, [fault])["label"] is False
    assert window_label(0, 100, False, [fault])["label"] is None


def test_failed_quota_and_existing_output_preserve_data(source: Path, tmp_path: Path) -> None:
    from autocan.training_data import prepare_training_data

    failed = tmp_path / "small"
    with pytest.raises(ValueError, match="quota"):
        prepare_training_data(source, failed, max_bytes=4096)
    assert not failed.exists()
    existing = tmp_path / "existing"
    existing.mkdir()
    (existing / "keep").write_text("unchanged")
    with pytest.raises(FileExistsError):
        prepare_training_data(source, existing)
    assert (existing / "keep").read_text() == "unchanged"


def test_common_window_evaluator_scores_rules_on_prepared_partitions(
    source: Path, tmp_path: Path
) -> None:
    from autocan.features import FeatureConfig
    from autocan.training_data import prepare_training_data
    from autocan.window_evaluation import (
        WindowKey,
        WindowScore,
        WindowTruth,
        baseline_window_scores,
        evaluate_windows,
    )

    output = tmp_path / "evaluation-input"
    config = FeatureConfig(window_ns=100_000_000)
    prepare_training_data(source, output, config=config)
    features = pq.read_table(output / "features.parquet").to_pylist()
    windows = pq.read_table(output / "windows.parquet").to_pylist()
    labels = pq.read_table(output / "labels/windows.parquet").to_pylist()
    targets = []
    predictions: dict[str, list[WindowScore]] = {}
    for feature, window, label in zip(features, windows, labels, strict=True):
        assert window["row_index"] == label["row_index"]
        key = WindowKey(**window)
        targets.append(WindowTruth(key, label["label"]))
        for rule, evidence in baseline_window_scores(feature, config).items():
            predictions.setdefault(rule, []).append(WindowScore(key, evidence.score))
    coverage = json.loads((output / "coverage.json").read_text())["partitions"]
    for part, counts in coverage.items():
        for rule, evidence in baseline_window_scores(features[0], config).items():
            result = evaluate_windows(
                targets,
                predictions[rule],
                model_id=rule,
                split=part,
                threshold=evidence.threshold,
                comparison=evidence.comparison,
            )
            assert result["input_windows"] == 960
            assert result["selected_windows"] == counts["windows"]
            assert result["evaluated_windows"] == counts["eligible_windows"]
            assert result["positive_windows"] == counts["positive_windows"]
            assert result["excluded_warmup_windows"] > 0
            assert result["average_precision"] is not None
            assert len(result["pr_curve"]["precision"]) == len(result["pr_curve"]["thresholds"]) + 1
    # Evaluating rules neither fits a detector nor rewrites the prepared data.
    assert json.loads((output / "manifest.json").read_text())["models_trained"] == []
