"""Frozen inference metrics, integrity, parity and subprocess provenance."""

import json
import math
from hashlib import sha256
from pathlib import Path
from shutil import copytree
from typing import Any

import pytest

from autocan import inference_benchmark as bench
from autocan.traces import TraceError


def test_latency_percentiles_throughput_and_rss_units() -> None:
    result = bench.summarize([1_000_000, 2_000_000, 3_000_000, 4_000_000], 256)
    assert result["batch_latency_ms_p50"] == 2.5
    assert result["batch_latency_ms_p95"] == pytest.approx(3.85)
    assert result["windows_per_second"] == 102400
    assert bench.rss_bytes(1024, "Darwin") == 1024
    assert bench.rss_bytes(1024, "Linux") == 1048576
    with pytest.raises(ValueError):
        bench.rss_bytes(1024, "Windows")
    for samples in ([], [0], [-1]):
        with pytest.raises(ValueError):
            bench.summarize(samples, 1)


@pytest.mark.parametrize(
    "overrides",
    [
        {"warmups": 0},
        {"warmups": True},
        {"repetitions": 1},
        {"repetitions": 101},
        {"windows": 255},
        {"windows": 257},
        {"windows": 4352},
        {"threads": 2},
        {"threads": False},
    ],
)
def test_invalid_configuration(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        bench.BenchmarkConfig(**overrides)


@pytest.mark.parametrize("actual", [[1.0], [1.0, math.nan], [1.0, 3.0]])
def test_score_parity_rejects_missing_nonfinite_or_changed_scores(actual: list[float]) -> None:
    with pytest.raises(TraceError, match="parity"):
        bench.check_parity(actual, [1.0, 2.0])


def test_score_parity_tolerance_is_fixed() -> None:
    assert bench.check_parity([1.0, 2.0 + 1e-13], [1.0, 2.0]) < 1e-12
    with pytest.raises(TraceError):
        bench.check_parity([1.0 + 1e-8], [1.0])


def _args(root: Path, target: Path) -> list[str]:
    return [
        "benchmark-inference",
        str(root / "prepared"),
        "--source",
        str(root / "source"),
        "--experiment",
        str(root / "experiment"),
        "--output",
        str(target),
        "--windows",
        "256",
        "--warmups",
        "1",
        "--repetitions",
        "2",
    ]


def test_cli_isolated_workers_preserve_inputs_and_record_provenance(
    ml_root: Path,
    ml_joint: Path,
    tmp_path: Path,
) -> None:
    from autocan.cli import main

    inputs = bench.input_fingerprints(ml_root / "prepared", ml_root / "source", ml_joint)
    target = tmp_path / "benchmark"
    assert main(_args(ml_root, target)) == 0
    assert bench.input_fingerprints(ml_root / "prepared", ml_root / "source", ml_joint) == inputs
    manifest = json.loads((target / "manifest.json").read_text())
    result = json.loads((target / "benchmark.json").read_text())
    assert manifest["inputs"] == inputs
    assert manifest["models_trained"] == manifest["thresholds_selected"] == []
    assert (
        manifest["benchmark_sha256"] == sha256((target / "benchmark.json").read_bytes()).hexdigest()
    )
    assert len(manifest["implementation_sha256"]) == 64
    assert result["configuration"]["batch_sizes"] == [1, 256]
    assert result["configuration"]["threads"] == 1
    assert result["environment"]["python"]
    assert result["environment"]["package_versions"]["threadpoolctl"]
    assert result["environment"]["hardware"]["logical_cpus"]
    assert len(result["workload"]["ordered_keys_sha256"]) == 64
    rows = result["rows"]
    assert len(rows) == 20
    measured = [r for r in rows if r["status"] == "measured"]
    assert len(measured) == 14
    assert len({r["worker_token"] for r in measured}) == 14
    assert len({(r["detector"], r["batch_size"], r["mode"]) for r in measured}) == 14
    for row in measured:
        assert row["parity"]["windows"] == 256
        assert row["parity"]["max_absolute_error"] <= 1e-12
        assert row["metrics"]["batch_latency_ms_p95"] > 0
        assert row["metrics"]["windows_per_second"] > 0
        assert len(row["run_elapsed_ns"]) == 2
        assert row["peak_process_rss_bytes"] > 0
        assert row["thread_environment"]["OMP_NUM_THREADS"] == "1"
        assert all(p["num_threads"] == 1 for p in row["threadpools"])
    assert main(_args(ml_root, target)) == 1


@pytest.mark.parametrize("damage", ["model", "features", "scores", "source"])
def test_cli_rejects_corruption_without_publishing(
    ml_root: Path,
    ml_joint: Path,
    tmp_path: Path,
    damage: str,
) -> None:
    from autocan.cli import main

    root = tmp_path / "inputs"
    for name in ("prepared", "source", "experiment"):
        copytree(ml_root / name, root / name)
    relative = {
        "model": "experiment/models/random_forest/model.pkl",
        "features": "prepared/features.parquet",
        "scores": "experiment/scores.parquet",
        "source": "source/manifest.json",
    }[damage]
    path = root / relative
    path.write_bytes(path.read_bytes() + b"corrupt")
    target = tmp_path / "rejected"
    assert main(_args(root, target)) == 1
    assert not target.exists()


def test_worker_uses_frozen_scoring_without_fit_or_selection(
    ml_root: Path,
    ml_joint: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sklearn.ensemble import IsolationForest, RandomForestClassifier

    from autocan import isolation_forest, ml_experiment, preprocessing

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("benchmark must never fit or select thresholds")

    for cls in (IsolationForest, RandomForestClassifier):
        monkeypatch.setattr(cls, "fit", forbidden)
    monkeypatch.setattr(preprocessing, "fit_training", forbidden)
    monkeypatch.setattr(isolation_forest, "choose_validation_threshold", forbidden)
    monkeypatch.setattr(ml_experiment, "choose_validation_threshold", forbidden)
    for detector in ("isolation_forest", "random_forest"):
        row = bench.measure_worker(
            ml_root / "prepared",
            ml_root / "source",
            ml_joint,
            detector,
            256,
            "preprocessing_scoring",
            bench.BenchmarkConfig(warmups=1, repetitions=2),
        )
        assert row["parity"]["max_absolute_error"] <= 1e-12


def test_parity_failure_occurs_before_any_timed_measurement(
    ml_root: Path,
    ml_joint: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autocan.isolation_forest import FittedForest

    def wrong_scores(self: FittedForest, rows: list[object]) -> list[float]:
        return [0.0] * len(rows)

    def forbidden_clock() -> int:
        pytest.fail("parity failure must precede the timed measurement section")

    monkeypatch.setattr(FittedForest, "score", wrong_scores)
    monkeypatch.setattr(bench, "perf_counter_ns", forbidden_clock)
    with pytest.raises(TraceError, match="parity"):
        bench.measure_worker(
            ml_root / "prepared",
            ml_root / "source",
            ml_joint,
            "isolation_forest",
            256,
            "preprocessing_scoring",
            bench.BenchmarkConfig(),
        )


def test_output_overlap_and_insufficient_workload_are_rejected(
    ml_root: Path,
    ml_joint: Path,
    tmp_path: Path,
) -> None:
    from autocan.cli import main

    assert main(_args(ml_root, ml_root / "source" / "inside")) == 1
    assert not (ml_root / "source" / "inside").exists()
    target = tmp_path / "too-many-windows"
    assert main(_args(ml_root, target) + ["--windows", "4096"]) == 1
    assert not target.exists()


def test_single_model_loading_never_deserializes_sibling(
    ml_root: Path,
    ml_joint: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pickle

    from sklearn.ensemble import RandomForestClassifier

    from autocan.model_artifacts import load_frozen_experiment
    from autocan.training_artifacts import load_training_data

    original = pickle.loads

    def only_iforest(raw: bytes) -> object:
        value = original(raw)
        if isinstance(value, RandomForestClassifier):
            pytest.fail("sibling estimator must not contaminate isolated worker RSS")
        return value

    monkeypatch.setattr(pickle, "loads", only_iforest)
    data = load_training_data(ml_root / "prepared", source=ml_root / "source")
    loaded = load_frozen_experiment(ml_joint, data, detector="isolation_forest")
    assert list(loaded.models) == ["isolation_forest"]
    with pytest.raises(ValueError):
        load_frozen_experiment(ml_joint, data, detector="unknown")
