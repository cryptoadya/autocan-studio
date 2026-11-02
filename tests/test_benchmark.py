"""Benchmark exercises verified factory artifacts, frozen splits and real CLI."""

import json
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def dataset(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from autocan.factory import FactoryConfig, generate_factory

    path = tmp_path_factory.mktemp("benchmark-data") / "collection"
    generate_factory(
        FactoryConfig(
            runs=6,
            seed=17,
            modes=("idle", "acceleration"),
            fault_runs=3,
            fault_types=("ecu_offline", "signal_spike", "cross_signal_inconsistency"),
            min_duration_ns=8_000_000_000,
            max_duration_ns=8_000_000_000,
        ),
        path,
    )
    return path


def test_benchmark_cli_is_reproducible_and_reuses_bound_split(
    dataset: Path, tmp_path: Path
) -> None:
    from autocan.cli import main

    first, second = tmp_path / "first", tmp_path / "second"
    assert main(["benchmark", str(dataset), "--output", str(first), "--split-seed", "7"]) == 0
    assert (
        main(
            [
                "benchmark",
                str(dataset),
                "--output",
                str(second),
                "--split",
                str(first / "split.json"),
            ]
        )
        == 0
    )
    for name in ("split.json", "metrics.json", "runs.jsonl", "report.md", "manifest.json"):
        assert (first / name).read_bytes() == (second / name).read_bytes()
    report = json.loads((first / "metrics.json").read_text())
    assert len(report["rows"]) == 9
    assert {row["rule_id"] for row in report["rows"]} == {
        "heartbeat_timeout",
        "rpm_derivative",
        "wheel_residual",
    }
    assert (
        sum(
            row["fault_episodes"] for row in report["rows"] if row["rule_id"] == "heartbeat_timeout"
        )
        == 3
    )
    runs = [json.loads(line) for line in (first / "runs.jsonl").read_text().splitlines()]
    assert len({row["run_id"] for row in runs}) == 6
    assert sum(row["normal_duration_ns"] for row in runs) == 24_000_000_000
    assert all(row["evaluated_ns"] == 8_000_000_000 for row in runs)


def test_failed_quota_existing_output_and_incompatible_split_do_not_publish(
    dataset: Path, tmp_path: Path
) -> None:
    from autocan.benchmark import run_benchmark
    from autocan.features import FeatureConfig
    from autocan.traces import TraceError

    good = tmp_path / "good"
    run_benchmark(dataset, good, config=FeatureConfig())
    before = (good / "manifest.json").read_bytes()
    with pytest.raises(FileExistsError):
        run_benchmark(dataset, good, config=FeatureConfig())
    assert (good / "manifest.json").read_bytes() == before
    failed = tmp_path / "quota"
    with pytest.raises(TraceError, match="quota"):
        run_benchmark(dataset, failed, config=FeatureConfig(), max_bytes=4096)
    assert not failed.exists()
    split = json.loads((good / "split.json").read_text())
    split["assignments"].append(split["assignments"][0])
    bad = tmp_path / "bad-split.json"
    bad.write_text(json.dumps(split))
    with pytest.raises(ValueError, match="split"):
        run_benchmark(dataset, tmp_path / "overlap", config=FeatureConfig(), split_path=bad)
    assert not (tmp_path / "overlap").exists()


@pytest.mark.parametrize("window_ns", [5_000_000_000, 10_000_000_000])
def test_partial_or_unscored_runs_do_not_inflate_normal_exposure(
    dataset: Path, tmp_path: Path, window_ns: int
) -> None:
    from autocan.benchmark import run_benchmark
    from autocan.features import FeatureConfig

    output = tmp_path / "benchmark"
    run_benchmark(dataset, output, config=FeatureConfig(window_ns=window_ns))
    rows = [json.loads(line) for line in (output / "runs.jsonl").read_text().splitlines()]
    cutoff = 5_000_000_000 if window_ns == 5_000_000_000 else 0
    assert all(row["evaluated_ns"] == cutoff for row in rows)
    assert all(row["discarded_tail_ns"] == 8_000_000_000 - cutoff for row in rows)
    assert sum(row["normal_duration_ns"] for row in rows) == 3 * cutoff
    assert sum(row["normal_run"] for row in rows) == 3
    assert all(fault["end_ns"] <= cutoff for row in rows for fault in row["faults"])
    if cutoff == 0:
        assert sum(row["unscored_fault_episodes"] for row in rows) == 3
        report = json.loads((output / "metrics.json").read_text())
        assert all(row["false_alarms_per_hour"] is None for row in report["rows"])


def test_fresh_logically_identical_runs_keep_benchmark_semantic_hash(
    dataset: Path, tmp_path: Path
) -> None:
    from autocan.benchmark import run_benchmark
    from autocan.factory import FactoryConfig, generate_factory
    from autocan.features import FeatureConfig

    config = FactoryConfig.from_dict(
        json.loads((dataset / "manifest.json").read_text())["configuration"]
    )
    generate_factory(config, tmp_path / "regenerated")
    first = run_benchmark(dataset, tmp_path / "first", config=FeatureConfig())
    second = run_benchmark(tmp_path / "regenerated", tmp_path / "second", config=FeatureConfig())
    assert first["semantic_sha256"] == second["semantic_sha256"]
    assert first["source_manifest_sha256"] != second["source_manifest_sha256"]


def test_invalid_source_is_rejected_before_benchmark_publication(
    dataset: Path, tmp_path: Path
) -> None:
    from autocan.benchmark import run_benchmark
    from autocan.features import FeatureConfig
    from autocan.traces import TraceError

    path = dataset / "manifest.json"
    original = path.read_bytes()
    invalid = json.loads(original)
    invalid["status"] = "incomplete"
    try:
        path.write_text(json.dumps(invalid))
        with pytest.raises(TraceError, match="incomplete"):
            run_benchmark(dataset, tmp_path / "invalid", config=FeatureConfig())
    finally:
        path.write_bytes(original)
    assert not (tmp_path / "invalid").exists()
