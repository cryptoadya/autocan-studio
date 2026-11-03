"""Acceptance checks consume frozen artifacts; experimental inputs remain immutable."""

import json
from hashlib import sha256
from pathlib import Path
from shutil import copytree
from typing import Any

import pytest

from autocan import acceptance
from autocan.acceptance_runtime import verify_runtime
from autocan.traces import TraceError
from autocan.validation import canonical_bytes


@pytest.mark.parametrize(
    "actual",
    [{"count": 3}, {"count": True}, {"count": 2.0}, {"ratio": None}, {"ratio": float("nan")}],
)
def test_scientific_comparison_rejects_changed_counts_types_nulls_and_nonfinite(
    actual: dict[str, Any],
) -> None:
    with pytest.raises(TraceError):
        acceptance.compare_scientific(
            {"count": 2, "ratio": 0.5} | actual, {"count": 2, "ratio": 0.5}
        )


def test_scientific_comparison_has_absolute_tolerance_and_exact_counts() -> None:
    acceptance.compare_scientific({"count": 2, "ratio": 0.5 + 1e-13}, {"count": 2, "ratio": 0.5})
    with pytest.raises(TraceError):
        acceptance.compare_scientific(
            {"count": 2, "ratio": 0.5 + 1e-10}, {"count": 2, "ratio": 0.5}
        )
    with pytest.raises(TraceError):
        acceptance.compare_scientific({"count": 10**16 + 1}, {"count": 10**16})


def _rehash(root: Path, name: str) -> None:
    path = root / name
    meta = json.loads((root / "manifest.json").read_text())
    meta["artifacts"][name].update(
        bytes=path.stat().st_size, sha256=sha256(path.read_bytes()).hexdigest()
    )
    if name == "benchmark.json":
        meta["benchmark_sha256"] = meta["artifacts"][name]["sha256"]
    if name == "metrics.json" and meta["format"] == "autocan-run-bootstrap":
        meta["semantic_sha256"] = sha256(canonical_bytes(json.loads(path.read_text()))).hexdigest()
    (root / "manifest.json").write_bytes(canonical_bytes(meta) + b"\n")


@pytest.fixture(scope="module")
def frozen_acceptance(
    ml_frozen: Path,
    ml_ood_scores: Path,
    tmp_path_factory: pytest.TempPathFactory,
) -> dict[str, Path]:
    """Reuse existing software-test inputs; create only derived metric/capture fixtures."""
    from autocan.bootstrap_benchmark import bootstrap_saved_metrics
    from autocan.event_benchmark import evaluate_saved_events
    from autocan.inference_benchmark import summarize
    from autocan.parquet import _file_hash

    root = tmp_path_factory.mktemp("acceptance-metrics")
    paths = {
        "prepared": ml_frozen / "prepared",
        "source": ml_frozen / "source",
        "experiment": ml_frozen / "experiment",
        "ood_results": ml_ood_scores,
        "suite": ml_frozen / "suite",
        "reference": ml_frozen / "reference",
        "events": root / "events",
        "bootstrap": root / "bootstrap",
        "inference": root / "inference",
    }
    options: dict[str, Any] = {
        k: paths[k] for k in ("source", "experiment", "ood_results", "suite", "reference")
    }
    evaluate_saved_events(paths["prepared"], paths["events"], **options)
    bootstrap_saved_metrics(
        paths["prepared"], paths["bootstrap"], **options, replicates=20, seed=42
    )
    # This is deliberately labelled CI test evidence, never an actual M1 measurement.
    origin = Path(__file__).resolve().parents[1] / "docs/benchmarks/m1-frozen-inference-2026-11-03"
    capture = json.loads((origin / "benchmark.json").read_text())
    manifest = json.loads((origin / "manifest.json").read_text())
    prepared = json.loads((paths["prepared"] / "manifest.json").read_text())
    experiment = json.loads((paths["experiment"] / "manifest.json").read_text())
    capture["workload"].update(
        windows=256,
        prepared_semantic_sha256=prepared["semantic_sha256"],
        experiment_semantic_sha256=experiment["semantic_sha256"],
    )
    capture["configuration"].update(windows=256, warmups=1, repetitions=2)
    capture["environment"]["ci"] = True
    capture["environment"]["package_versions"] = experiment["package_versions"]
    for row in capture["rows"]:
        if row["status"] != "measured":
            continue
        count = 256 // row["batch_size"]
        row["batch_durations_ns"] = [1_000_000] * (count * 2)
        row["run_elapsed_ns"] = [count * 1_000_000 + 100] * 2
        row["metrics"] = summarize(row["batch_durations_ns"], row["batch_size"])
        row["parity"]["windows"] = 256
        row["parity"]["max_absolute_error"] = 0
    manifest["inputs"] = {
        name: {
            p.relative_to(paths[name]).as_posix(): _file_hash(p)
            for p in paths[name].rglob("*")
            if p.is_file()
        }
        for name in ("prepared", "source", "experiment")
    }
    paths["inference"].mkdir()
    (paths["inference"] / "benchmark.json").write_bytes(canonical_bytes(capture) + b"\n")
    (paths["inference"] / "report.md").write_text("CI TEST FIXTURE: no performance claim.\n")
    (paths["inference"] / "manifest.json").write_bytes(canonical_bytes(manifest) + b"\n")
    _rehash(paths["inference"], "benchmark.json")
    _rehash(paths["inference"], "report.md")
    return paths


def _arguments(paths: dict[str, Path], output: Path) -> list[str]:
    args = ["accept-m9", str(paths["prepared"]), "--output", str(output)]
    for name, path in paths.items():
        if name != "prepared":
            args += ["--" + name.replace("_", "-"), str(path)]
    return args


def test_acceptance_replays_twice_preserves_inputs_and_never_loads_or_scores_models(
    frozen_acceptance: dict[str, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pickle

    from sklearn.ensemble import IsolationForest, RandomForestClassifier

    from autocan import factory, isolation_forest, ml_experiment, preprocessing, training_data
    from autocan.cli import main
    from autocan.random_forest import FittedRandomForest

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("acceptance may only verify and evaluate existing saved scores")

    for cls in (IsolationForest, RandomForestClassifier):
        monkeypatch.setattr(cls, "fit", forbidden)
    for cls in (isolation_forest.FittedForest, FittedRandomForest):
        monkeypatch.setattr(cls, "score", forbidden)
    monkeypatch.setattr(pickle, "loads", forbidden)
    monkeypatch.setattr(factory, "generate_factory", forbidden)
    monkeypatch.setattr(preprocessing, "fit_training", forbidden)
    monkeypatch.setattr(training_data, "fit_training", forbidden)
    monkeypatch.setattr(isolation_forest, "choose_validation_threshold", forbidden)
    monkeypatch.setattr(ml_experiment, "choose_validation_threshold", forbidden)
    before = acceptance.fingerprints(frozen_acceptance)
    first, second = tmp_path / "one", tmp_path / "two"
    assert main(_arguments(frozen_acceptance, first)) == 0
    assert main(_arguments(frozen_acceptance, second)) == 0
    assert acceptance.fingerprints(frozen_acceptance) == before
    assert (first / "acceptance.json").read_bytes() == (second / "acceptance.json").read_bytes()
    data = json.loads((first / "acceptance.json").read_text())
    assert data["verdict"] == "verified_without_local_m1_evidence"
    assert len(data["rows"]) == 25
    assert data["repeatability"]["events"]["runs"] == 2
    assert data["repeatability"]["bootstrap"]["runs"] == 2
    assert data["runtime"]["local_m1_observation"] is False
    assert data["inputs_preserved"] is True
    assert "validation is in-selection" in (first / "report.md").read_text().lower()
    manifest = json.loads((first / "manifest.json").read_text())
    assert manifest["models_trained"] == manifest["thresholds_selected"] == []
    assert manifest["scores_computed"] == 0 and manifest["inputs"] == before
    assert main(_arguments(frozen_acceptance, first)) == 1
    assert main(_arguments(frozen_acceptance, frozen_acceptance["source"] / "inside")) == 1


@pytest.mark.parametrize(
    "damage",
    [
        "bytes",
        "lineage",
        "event_seed",
        "experiment_seed",
        "metric",
        "runtime_model",
        "runtime_latency",
        "runtime_parity",
    ],
)
def test_acceptance_rejects_invalid_evidence_without_publication(
    frozen_acceptance: dict[str, Path],
    tmp_path: Path,
    damage: str,
) -> None:
    from autocan.cli import main

    paths = dict(frozen_acceptance)
    role = (
        "events"
        if damage == "event_seed"
        else "inference"
        if damage.startswith("runtime")
        else "bootstrap"
    )
    root = paths[role] = copytree(paths[role], tmp_path / role)
    if damage == "bytes":
        (root / "metrics.json").write_bytes(b"corrupt")
    elif damage == "lineage":
        p = root / "manifest.json"
        value = json.loads(p.read_text())
        value["inputs"]["experiment"] = "0" * 64
        p.write_bytes(canonical_bytes(value) + b"\n")
    elif damage in ("event_seed", "experiment_seed"):
        p = root / "manifest.json"
        value = json.loads(p.read_text())
        value["seed" if damage == "event_seed" else "experiment_seed"] += 1
        p.write_bytes(canonical_bytes(value) + b"\n")
    elif damage == "metric":
        p = root / "metrics.json"
        value = json.loads(p.read_text())
        value["rows"][0]["window"]["tp"]["estimate"] += 1
        p.write_bytes(canonical_bytes(value) + b"\n")
        _rehash(root, "metrics.json")
    elif damage == "runtime_model":
        p = root / "manifest.json"
        value = json.loads(p.read_text())
        value["inputs"]["experiment"]["models/random_forest/model.pkl"] = "0" * 64
        p.write_bytes(canonical_bytes(value) + b"\n")
    else:
        p = root / "benchmark.json"
        value = json.loads(p.read_text())
        if damage == "runtime_latency":
            value["rows"][0]["metrics"]["batch_latency_ms_p95"] += 1
        else:
            value["rows"][0]["parity"]["max_absolute_error"] = 0.01
        p.write_bytes(canonical_bytes(value) + b"\n")
        _rehash(root, "benchmark.json")
    target = tmp_path / "rejected"
    assert main(_arguments(paths, target)) == 1
    assert not target.exists() and not list(tmp_path.glob(".autocan-*"))


def test_replay_repeatability_failure_prevents_publication(
    frozen_acceptance: dict[str, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autocan.cli import main

    original = acceptance.evaluate_saved_events
    calls = 0

    def changed(*args: Any, **kwargs: Any) -> dict[str, object]:
        nonlocal calls
        result = original(*args, **kwargs)
        calls += 1
        if calls == 2:
            (args[1] / "report.md").write_text("different repeat\n")
        return result

    monkeypatch.setattr(acceptance, "evaluate_saved_events", changed)
    output = tmp_path / "nonrepeatable"
    assert main(_arguments(frozen_acceptance, output)) == 1
    assert not output.exists()


def test_runtime_bridge_does_not_require_identical_experiment_metadata(
    frozen_acceptance: dict[str, Path],
    tmp_path: Path,
) -> None:
    paths = frozen_acceptance
    root = copytree(paths["inference"], tmp_path / "runtime")
    manifest = json.loads((root / "manifest.json").read_text())
    manifest["inputs"]["experiment"]["models/random_forest/model.json"] = "1" * 64
    manifest["inputs"]["experiment"]["split.json"] = "2" * 64
    (root / "manifest.json").write_bytes(canonical_bytes(manifest) + b"\n")
    result = verify_runtime(
        root,
        json.loads((paths["prepared"] / "manifest.json").read_text()),
        json.loads((paths["experiment"] / "manifest.json").read_text()),
    )
    assert result["identity_bridge"]["different_metadata"] == [
        "models/random_forest/model.json",
        "split.json",
    ]
    assert result["identity_bridge"]["model_preprocessing_thresholds_match"] is True


def test_normal_alarm_table_must_match_saved_score_event_points() -> None:
    points = [
        {
            "split": "ood_normal",
            "model_id": "rule",
            "window": {"fp": {"estimate": 2}},
            "event": {
                "normal_false_alarm_episodes": {"estimate": 1},
                "normal_duration_ns": {"estimate": 3600000000000},
                "false_alarms_per_hour": {"estimate": 1.0},
            },
        }
    ]
    archive = [
        {
            "split": "ood_normal",
            "model_id": "rule",
            "false_positive_windows": 2,
            "false_alarm_episodes": 1,
            "normal_duration_ns": 3600000000000,
            "false_alarms_per_hour": 1.0,
        }
    ]
    acceptance.verify_normal_alarms(points, archive)
    archive[0]["false_alarm_episodes"] = 0
    with pytest.raises(TraceError):
        acceptance.verify_normal_alarms(points, archive)


def test_input_change_during_verification_prevents_publication(
    frozen_acceptance: dict[str, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from autocan.cli import main

    paths = dict(frozen_acceptance)
    paths["bootstrap"] = copytree(paths["bootstrap"], tmp_path / "copied-bootstrap")
    original = acceptance.fingerprints
    calls = 0

    def changed(roots: dict[str, Path]) -> dict[str, Any]:
        nonlocal calls
        if calls == 1:
            path = paths["bootstrap"] / "report.md"
            path.write_text(path.read_text() + "external change\n")
        calls += 1
        return original(roots)

    monkeypatch.setattr(acceptance, "fingerprints", changed)
    output = tmp_path / "changed-input"
    assert main(_arguments(paths, output)) == 1
    assert "inputs changed" in capsys.readouterr().err
    assert not output.exists()
