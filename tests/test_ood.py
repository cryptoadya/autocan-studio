"""OOD protocols freeze ID settings and keep the two shifts separate."""

import json
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def reference(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    from autocan.benchmark import run_benchmark
    from autocan.factory import FactoryConfig, generate_factory
    from autocan.features import FeatureConfig

    root = tmp_path_factory.mktemp("ood-reference")
    generate_factory(
        FactoryConfig(
            runs=6,
            seed=71,
            modes=("city", "acceleration"),
            fault_runs=3,
            fault_types=("ecu_offline", "signal_spike", "cross_signal_inconsistency"),
            min_duration_ns=8_000_000_000,
            max_duration_ns=8_000_000_000,
        ),
        root / "id",
    )
    run_benchmark(
        root / "id", root / "benchmark", config=FeatureConfig(rpm_derivative_threshold=12345)
    )
    return root / "id", root / "benchmark"


def test_protocol_reserves_absent_types_and_normal_families_and_keeps_config(
    reference: tuple[Path, Path],
) -> None:
    from autocan.factory import verify_factory
    from autocan.ood import plan_ood, verify_reference

    source, benchmark = reference
    meta, frozen = verify_reference(source, benchmark)
    protocol = plan_ood(meta, frozen, seed=19, fault_runs=5, normal_runs=4)
    assert protocol["feature_configuration"]["rpm_derivative_threshold"] == 12345
    assert set(protocol["known_fault_types"]) == {
        "ecu_offline",
        "signal_spike",
        "cross_signal_inconsistency",
    }
    assert protocol["ood_fault_configuration"]["fault_types"] == [
        "message_drop",
        "message_delay",
        "signal_stuck",
        "signal_bias",
        "message_frequency_anomaly",
    ]
    assert protocol["ood_normal_configuration"]["modes"] == [
        "idle",
        "stop_and_go",
        "cruise",
        "braking",
    ]
    assert protocol["ood_normal_configuration"]["fault_runs"] == 0
    assert protocol["ood_fault_configuration"]["fault_runs"] == 5
    assert protocol == plan_ood(
        verify_factory(source), frozen, seed=19, fault_runs=5, normal_runs=4
    )


@pytest.fixture(scope="module")
def suite(reference: tuple[Path, Path], tmp_path_factory: pytest.TempPathFactory) -> Path:
    from autocan.ood import generate_ood

    target = tmp_path_factory.mktemp("ood-data") / "suite"
    generate_ood(*reference, target, seed=19, fault_runs=2, normal_runs=2)
    return target


def test_cli_separate_metrics_reproducible_and_thresholds_frozen(
    reference: tuple[Path, Path], suite: Path, tmp_path: Path
) -> None:
    from autocan.cli import main

    source, benchmark = reference
    first, second = tmp_path / "first", tmp_path / "second"
    for target in (first, second):
        assert (
            main(
                [
                    "benchmark-ood",
                    str(source),
                    str(suite),
                    "--reference",
                    str(benchmark),
                    "--output",
                    str(target),
                ]
            )
            == 0
        )
    for name in ("protocol.json", "runs.jsonl", "metrics.json", "report.md", "manifest.json"):
        assert (first / name).read_bytes() == (second / name).read_bytes()
    report = json.loads((first / "metrics.json").read_text())
    assert len(report["rows"]) == 6
    assert report["configuration"]["rpm_derivative_threshold"] == 12345
    faulty = [r for r in report["rows"] if r["split"] == "ood_fault"]
    normal = [r for r in report["rows"] if r["split"] == "ood_normal"]
    assert all(r["fault_episodes"] == 2 and r["false_alarms_per_hour"] is None for r in faulty)
    assert all(
        r["fault_episodes"] == 0 and r["normal_duration_ns"] == 16_000_000_000 for r in normal
    )
    assert all(r["detection_rate"] is None and r["false_alarms_per_hour"] == 0 for r in normal)
    with pytest.raises(SystemExit):
        main(
            [
                "benchmark-ood",
                str(source),
                str(suite),
                "--reference",
                str(benchmark),
                "--output",
                str(tmp_path / "invalid"),
                "--rpm-derivative-threshold",
                "1",
            ]
        )


def test_cross_partition_duplicate_inputs_rejected(reference: tuple[Path, Path]) -> None:
    from copy import deepcopy

    from autocan.factory import verify_factory
    from autocan.ood import check_isolation

    meta = verify_factory(reference[0])
    for field in ("run_id", "scenario_sha256", "observation_semantic_sha256"):
        copied = deepcopy(meta)
        for index, row in enumerate(copied["runs"]):
            row["run_id"] = f"new-run-{index}"
            row["scenario_sha256"] = f"new-scenario-{index}"
            row["observation_semantic_sha256"] = f"new-input-{index}"
        copied["runs"][0][field] = meta["runs"][0][field]
        with pytest.raises(ValueError, match=field):
            check_isolation({"id": meta, "ood_fault": copied})


def test_corrupt_reference_is_rejected_before_generation(
    reference: tuple[Path, Path], tmp_path: Path
) -> None:
    from autocan.ood import generate_ood

    source, benchmark = reference
    path = benchmark / "metrics.json"
    original = path.read_bytes()
    try:
        path.write_bytes(original + b" ")
        with pytest.raises(ValueError, match="checksum|size"):
            generate_ood(source, benchmark, tmp_path / "invalid", fault_runs=1, normal_runs=1)
    finally:
        path.write_bytes(original)
    assert not (tmp_path / "invalid").exists()


def test_frozen_protocol_rejects_changed_reference_config(
    reference: tuple[Path, Path], suite: Path, tmp_path: Path
) -> None:
    from autocan.benchmark import run_benchmark
    from autocan.features import FeatureConfig
    from autocan.ood_benchmark import evaluate_ood

    source, _ = reference
    run_benchmark(source, tmp_path / "retuned", config=FeatureConfig(rpm_derivative_threshold=1))
    with pytest.raises(ValueError, match="protocol|reference"):
        evaluate_ood(source, tmp_path / "retuned", suite, tmp_path / "rejected")
    assert not (tmp_path / "rejected").exists()


def test_all_known_modes_cannot_be_relabelled_as_normal_ood(reference: tuple[Path, Path]) -> None:
    from autocan.factory import DRIVING_MODES
    from autocan.ood import plan_ood, verify_reference

    meta, frozen = verify_reference(*reference)
    for row, mode in zip(meta["runs"], DRIVING_MODES, strict=True):
        row["mode"] = mode
    with pytest.raises(ValueError, match="reserved"):
        plan_ood(meta, frozen, seed=1, fault_runs=2, normal_runs=2)


def test_tampered_protocol_and_quota_failures_never_publish_results(
    reference: tuple[Path, Path], suite: Path, tmp_path: Path
) -> None:
    from autocan.ood import generate_ood
    from autocan.ood_benchmark import evaluate_ood

    with pytest.raises(ValueError, match="quota"):
        generate_ood(*reference, tmp_path / "small-suite", max_bytes=8192)
    assert not (tmp_path / "small-suite").exists()
    with pytest.raises(ValueError, match="quota"):
        evaluate_ood(*reference, suite, tmp_path / "small-result", max_bytes=4096)
    assert not (tmp_path / "small-result").exists()
    path = suite / "protocol.json"
    original = path.read_bytes()
    changed = json.loads(original)
    changed["feature_configuration"]["rpm_derivative_threshold"] = 1
    try:
        path.write_text(json.dumps(changed))
        with pytest.raises(ValueError, match="protocol"):
            evaluate_ood(*reference, suite, tmp_path / "tampered")
    finally:
        path.write_bytes(original)
    assert not (tmp_path / "tampered").exists()


def test_cli_handles_missing_reference_inventory_as_input_error(
    reference: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from autocan.cli import main

    source, benchmark = reference
    path = benchmark / "manifest.json"
    original = path.read_bytes()
    invalid = json.loads(original)
    del invalid["artifacts"]
    try:
        path.write_text(json.dumps(invalid))
        assert (
            main(
                [
                    "generate-ood",
                    str(source),
                    "--reference",
                    str(benchmark),
                    "--output",
                    str(tmp_path / "invalid"),
                ]
            )
            == 1
        )
        assert "error:" in capsys.readouterr().err
    finally:
        path.write_bytes(original)
    assert not (tmp_path / "invalid").exists()
