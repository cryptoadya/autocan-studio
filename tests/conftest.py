"""Session-local M9 inputs; consumers mutate copies, never these artifacts."""

from collections.abc import Iterator
from hashlib import sha256
from pathlib import Path

import pytest


def _inventory(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }


def _unchanged(root: Path) -> Iterator[Path]:
    before = _inventory(root)
    try:
        yield root
    finally:
        assert _inventory(root) == before, f"shared M9 fixture was modified: {root}"


def _frozen_cli(arguments: list[str], *, score_only: bool = False) -> None:
    from sklearn.ensemble import IsolationForest, RandomForestClassifier

    from autocan import isolation_forest, ml_experiment, preprocessing, training_data
    from autocan.cli import main

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("frozen M9 inputs must not be fitted or reselected")

    with pytest.MonkeyPatch.context() as patch:
        # Import alias-owning consumers above before patching their providers.
        patch.setattr(preprocessing, "fit_training", forbidden)
        patch.setattr(training_data, "fit_training", forbidden)
        if score_only:
            patch.setattr(IsolationForest, "fit", forbidden)
            patch.setattr(RandomForestClassifier, "fit", forbidden)
            patch.setattr(isolation_forest, "choose_validation_threshold", forbidden)
            patch.setattr(ml_experiment, "choose_validation_threshold", forbidden)
        assert main(arguments) == 0


@pytest.fixture(scope="session")
def ml_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("shared-m9")


@pytest.fixture(scope="session")
def ml_source(ml_root: Path) -> Iterator[Path]:
    from autocan.factory import FactoryConfig, generate_factory

    target = ml_root / "source"
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
        target,
    )
    yield from _unchanged(target)


@pytest.fixture(scope="session")
def ml_prepared(ml_root: Path, ml_source: Path) -> Iterator[Path]:
    from autocan.cli import main

    target = ml_root / "prepared"
    assert main(["prepare-ml", str(ml_source), "--window", "0.1", "--output", str(target)]) == 0
    yield from _unchanged(target)


@pytest.fixture(scope="session")
def ml_prepared_default(ml_root: Path, ml_source: Path) -> Iterator[Path]:
    from autocan.cli import main

    target = ml_root / "prepared-default"
    assert main(["prepare-ml", str(ml_source), "--output", str(target)]) == 0
    yield from _unchanged(target)


@pytest.fixture(scope="session")
def ml_rules(ml_root: Path, ml_source: Path, ml_prepared: Path) -> Iterator[Path]:
    target = ml_root / "rules"
    _frozen_cli(
        ["evaluate-rules", str(ml_prepared), "--source", str(ml_source), "--output", str(target)],
        score_only=True,
    )
    yield from _unchanged(target)


@pytest.fixture(scope="session")
def ml_iforest(ml_root: Path, ml_source: Path, ml_prepared: Path) -> Iterator[Path]:
    target = ml_root / "iforest"
    _frozen_cli(
        [
            "train-isolation-forest",
            str(ml_prepared),
            "--source",
            str(ml_source),
            "--output",
            str(target),
            "--seed",
            "42",
        ]
    )
    yield from _unchanged(target)


@pytest.fixture(scope="session")
def ml_joint(ml_root: Path, ml_source: Path, ml_prepared: Path) -> Iterator[Path]:
    target = ml_root / "experiment"
    _frozen_cli(
        [
            "train-random-forest",
            str(ml_prepared),
            "--source",
            str(ml_source),
            "--output",
            str(target),
            "--seed",
            "42",
        ]
    )
    yield from _unchanged(target)


@pytest.fixture(scope="session")
def ml_reference(ml_root: Path, ml_source: Path) -> Iterator[Path]:
    from autocan.benchmark import run_benchmark
    from autocan.features import FeatureConfig

    target = ml_root / "reference"
    run_benchmark(ml_source, target, config=FeatureConfig(window_ns=100_000_000))
    yield from _unchanged(target)


@pytest.fixture(scope="session")
def ml_suite(ml_root: Path, ml_source: Path, ml_reference: Path) -> Iterator[Path]:
    from autocan.ood import generate_ood

    target = ml_root / "suite"
    generate_ood(ml_source, ml_reference, target, seed=19, fault_runs=2, normal_runs=2)
    yield from _unchanged(target)


@pytest.fixture(scope="session")
def ml_frozen(
    ml_root: Path,
    ml_joint: Path,
    ml_suite: Path,
) -> Path:
    # Dependencies populate the exact source/prepared/experiment/reference/suite layout.
    return ml_root


@pytest.fixture(scope="session")
def ml_ood_scores(ml_frozen: Path) -> Iterator[Path]:
    target = ml_frozen / "ood-scores"
    _frozen_cli(
        [
            "evaluate-ml-ood",
            str(ml_frozen / "prepared"),
            str(ml_frozen / "suite"),
            "--source",
            str(ml_frozen / "source"),
            "--reference",
            str(ml_frozen / "reference"),
            "--experiment",
            str(ml_frozen / "experiment"),
            "--output",
            str(target),
        ],
        score_only=True,
    )
    yield from _unchanged(target)
