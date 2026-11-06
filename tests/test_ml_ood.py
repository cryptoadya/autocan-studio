"""Frozen OOD scoring must reuse models, transforms and validation cutoffs."""

import json
import pickle
from hashlib import sha256
from pathlib import Path
from shutil import copytree

import numpy as np
import pyarrow.parquet as pq
import pytest

from autocan.training_artifacts import LoadedTrainingData, load_training_data


@pytest.fixture(scope="module")
def frozen(ml_frozen: Path) -> Path:
    return ml_frozen


def arguments(root: Path, target: Path) -> list[str]:
    return [
        "evaluate-ml-ood",
        str(root / "prepared"),
        str(root / "suite"),
        "--source",
        str(root / "source"),
        "--reference",
        str(root / "reference"),
        "--experiment",
        str(root / "experiment"),
        "--output",
        str(target),
    ]


@pytest.fixture(scope="module")
def saved_data(frozen: Path) -> LoadedTrainingData:
    return load_training_data(frozen / "prepared", source=frozen / "source")


@pytest.fixture(scope="module")
def saved_ood(ml_ood_scores: Path) -> Path:
    return ml_ood_scores


def event_arguments(root: Path, saved_ood: Path, target: Path) -> list[str]:
    return [
        "evaluate-events",
        str(root / "prepared"),
        "--source",
        str(root / "source"),
        "--experiment",
        str(root / "experiment"),
        "--ood-results",
        str(saved_ood),
        "--suite",
        str(root / "suite"),
        "--reference",
        str(root / "reference"),
        "--output",
        str(target),
    ]


def test_event_cli_reuses_all_saved_scores_without_model_loading_or_inference(
    frozen: Path,
    saved_ood: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autocan.cli import main

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("saved-score event evaluation cannot load/score/fit models or replay features")

    for name in (
        "pickle.loads",
        "pickle.load",
        "autocan.model_artifacts.load_frozen_experiment",
        "autocan.isolation_forest.FittedForest.score",
        "autocan.random_forest.FittedRandomForest.score",
        "autocan.preprocessing.fit_training",
        "autocan.training_data.fit_training",
        "autocan.isolation_forest.choose_validation_threshold",
        "autocan.ml_experiment.choose_validation_threshold",
        "autocan.features.replay_observations",
    ):
        monkeypatch.setattr(name, forbidden)
    before = {
        p: sha256(p.read_bytes()).hexdigest()
        for folder in (frozen, saved_ood)
        for p in folder.rglob("*")
        if p.is_file()
    }
    first, second = tmp_path / "events", tmp_path / "again"
    for target in (first, second):
        assert main(event_arguments(frozen, saved_ood, target)) == 0
    for p in first.iterdir():
        assert p.read_bytes() == (second / p.name).read_bytes()
    assert all(sha256(p.read_bytes()).hexdigest() == h for p, h in before.items())
    metrics = json.loads((first / "metrics.json").read_text())
    assert len(metrics["rows"]) == 25
    saved_normal = {
        r["model_id"]: r
        for r in json.loads((saved_ood / "metrics.json").read_text())["normal_false_alarms"]
    }
    for row in metrics["rows"]:
        assert row["fault_episodes"] == (
            2 if row["split"] == "ood_fault" else 0 if row["split"] == "ood_normal" else 3
        )
        if row["split"] == "ood_normal":
            previous = saved_normal[row["model_id"]]
            assert row["normal_false_alarm_episodes"] == previous["false_alarm_episodes"]
            assert row["normal_duration_ns"] == previous["normal_duration_ns"]
            assert row["false_alarms_per_hour"] == previous["false_alarms_per_hour"]
            assert row["detection_rate"] is None
    ledger = [json.loads(line) for line in (first / "runs.jsonl").read_text().splitlines()]
    assert len(ledger) == 80
    assert all(m["latency_ns"] >= 0 for row in ledger for m in row["result"]["matches"])
    manifest = json.loads((first / "manifest.json").read_text())
    assert manifest["models_trained"] == [] and manifest["thresholds_selected"] == []
    assert manifest["scores_computed"] == 0
    assert main(event_arguments(frozen, saved_ood, first)) == 1
    assert (
        main(event_arguments(frozen, saved_ood, tmp_path / "quota") + ["--max-bytes", "4096"]) == 1
    )
    assert not (tmp_path / "quota").exists()
    with pytest.raises(SystemExit):
        main(event_arguments(frozen, saved_ood, tmp_path / "tuned") + ["--grace", "1"])


@pytest.mark.parametrize("damage", ["binding", "window"])
def test_event_cli_rejects_ood_binding_or_rehashed_window_changes_without_publication(
    frozen: Path,
    saved_ood: Path,
    tmp_path: Path,
    damage: str,
) -> None:
    from autocan.cli import main
    from autocan.validation import canonical_bytes

    root = copytree(saved_ood, tmp_path / "ood")
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if damage == "binding":
        manifest["experiment_manifest_sha256"] = "0" * 64
    else:
        import pyarrow as pa

        path = root / "windows.parquet"
        table = pq.read_table(path)
        rows = table.to_pylist()
        rows[0]["run_id"] = "different-run"
        pq.write_table(pa.Table.from_pylist(rows, schema=table.schema), path, row_group_size=256)
        manifest["artifacts"]["windows.parquet"].update(
            bytes=path.stat().st_size,
            sha256=sha256(path.read_bytes()).hexdigest(),
            semantic_sha256=sha256(b"".join(canonical_bytes(r) + b"\n" for r in rows)).hexdigest(),
        )
    manifest_path.write_text(json.dumps(manifest))
    assert main(event_arguments(frozen, root, tmp_path / "invalid")) == 1
    assert not (tmp_path / "invalid").exists()


def test_saved_predictions_thresholds_normal_exposure_and_repeatability(
    frozen: Path,
    saved_ood: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autocan.cli import main
    from autocan.preprocessing import Preprocessor

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("OOD evaluation must never fit or select a threshold")

    for name in (
        "sklearn.ensemble.IsolationForest.fit",
        "sklearn.ensemble.RandomForestClassifier.fit",
        "autocan.preprocessing.fit_training",
        "autocan.training_data.fit_training",
        "autocan.isolation_forest.choose_validation_threshold",
        "autocan.ml_experiment.choose_validation_threshold",
    ):
        monkeypatch.setattr(name, forbidden)
    before = {p: sha256(p.read_bytes()).hexdigest() for p in frozen.rglob("*") if p.is_file()}
    first, second = saved_ood, tmp_path / "second"
    assert main(arguments(frozen, second)) == 0
    assert all(sha256(p.read_bytes()).hexdigest() == h for p, h in before.items())
    for p in first.rglob("*"):
        if p.is_file():
            assert p.read_bytes() == (second / p.relative_to(first)).read_bytes()
    keys = pq.read_table(first / "windows.parquet").to_pylist()
    features = pq.read_table(first / "features.parquet").to_pylist()
    scores = pq.read_table(first / "scores.parquet").to_pylist()
    labels = pq.read_table(first / "labels/windows.parquet").to_pylist()
    metrics = json.loads((first / "metrics.json").read_text())
    assert len(metrics["rows"]) == 10
    assert {r["split"] for r in metrics["rows"]} == {"ood_fault", "ood_normal"}
    for model_id in ("isolation_forest", "random_forest"):
        folder = frozen / "experiment/models" / model_id
        estimator = pickle.loads((folder / "model.pkl").read_bytes())
        state = Preprocessor.from_dict(json.loads((folder / "preprocessing.json").read_text()))
        matrix = np.asarray(
            [
                state.transform(tuple(f[c] for c in state.columns), columns=state.columns)
                for f in features
            ]
        )
        expected = (
            -estimator.score_samples(matrix)
            if model_id == "isolation_forest"
            else estimator.predict_proba(matrix)[:, list(estimator.classes_).index(1)]
        )
        threshold = json.loads((folder / "threshold.json").read_text())["threshold"]
        actual = [s for s in scores if s["model_id"] == model_id]
        assert [s["score"] for s in actual] == pytest.approx(expected)
        assert all(s["threshold"] == threshold for s in actual)
        assert [s["anomalous"] for s in actual] == list(expected >= threshold)
    for row in metrics["rows"]:
        selected = [k for k in keys if k["eligible"] and k["split"] == row["split"]]
        assert row["evaluated_windows"] == len(selected)
        if row["split"] == "ood_normal":
            assert row["positive_windows"] == 0 and row["average_precision"] is None
            assert all(labels[k["row_index"]]["label"] is False for k in selected)
    for row in metrics["normal_false_alarms"]:
        selected = [k for k in keys if k["split"] == "ood_normal" and k["eligible"]]
        decisions = {
            s["row_index"]: s["anomalous"] for s in scores if s["model_id"] == row["model_id"]
        }
        episodes = sum(
            decisions[k["row_index"]]
            and (
                i == 0
                or selected[i - 1]["run_id"] != k["run_id"]
                or not decisions[selected[i - 1]["row_index"]]
            )
            for i, k in enumerate(selected)
        )
        duration = sum(k["end_ns"] - k["start_ns"] for k in selected)
        assert row["normal_duration_ns"] == duration == 15_200_000_000
        assert row["false_alarm_episodes"] == episodes
        assert row["false_alarms_per_hour"] == pytest.approx(episodes * 3.6e12 / duration)
    manifest = json.loads((first / "manifest.json").read_text())
    assert manifest["models_trained"] == [] and manifest["thresholds_selected"] == []
    assert main(arguments(frozen, first)) == 1
    assert main(arguments(frozen, tmp_path / "quota") + ["--max-bytes", "4096"]) == 1
    assert not (tmp_path / "quota").exists()
    with pytest.raises(SystemExit):
        main(arguments(frozen, tmp_path / "tuned") + ["--seed", "9"])


@pytest.mark.parametrize(
    "damage",
    [
        "pickle",
        "threshold",
        "preprocessing",
        "lineage",
        "packages",
        "threshold_rehashed",
        "scope_rehashed",
        "symlink",
    ],
)
def test_invalid_experiment_rejected_before_deserialization(
    frozen: Path,
    saved_data: LoadedTrainingData,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    damage: str,
) -> None:
    from autocan.model_artifacts import load_frozen_experiment

    experiment = copytree(frozen / "experiment", tmp_path / "experiment")
    manifest_path = experiment / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if damage in ("lineage", "packages"):
        if damage == "lineage":
            manifest["prepared_semantic_sha256"] = "0" * 64
        else:
            manifest["package_versions"]["scikit-learn"] = "0.0"
        manifest_path.write_text(json.dumps(manifest))
    elif damage == "symlink":
        path = experiment / "models/random_forest/model.pkl"
        path.unlink()
        path.symlink_to(frozen / "experiment/models/random_forest/model.pkl")
    elif damage.endswith("rehashed"):
        file = "threshold.json" if damage == "threshold_rehashed" else "preprocessing.json"
        name = f"models/random_forest/{file}"
        path = experiment / name
        record = json.loads(path.read_text())
        if damage == "threshold_rehashed":
            record["threshold"] = 0.987654321
        else:
            record["normal_only"] = True
        path.write_text(json.dumps(record))
        manifest["artifacts"][name].update(
            bytes=path.stat().st_size,
            sha256=sha256(path.read_bytes()).hexdigest(),
        )
        manifest_path.write_text(json.dumps(manifest))
    else:
        name = {
            "pickle": "model.pkl",
            "threshold": "threshold.json",
            "preprocessing": "preprocessing.json",
        }[damage]
        path = experiment / "models/random_forest" / name
        path.write_bytes(path.read_bytes() + b" ")

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("unvalidated pickle must not be loaded")

    monkeypatch.setattr(pickle, "loads", forbidden)
    with pytest.raises(ValueError, match="checksum|mismatch|symlink|incompatible"):
        load_frozen_experiment(experiment, saved_data)


def test_unfitted_estimator_is_rejected_as_an_invalid_bundle(
    frozen: Path,
    saved_data: LoadedTrainingData,
    tmp_path: Path,
) -> None:
    from sklearn.ensemble import RandomForestClassifier

    from autocan.model_artifacts import load_frozen_experiment

    experiment = copytree(frozen / "experiment", tmp_path / "experiment")
    name = "models/random_forest/model.pkl"
    metadata = json.loads((experiment / "models/random_forest/model.json").read_text())
    path = experiment / name
    path.write_bytes(pickle.dumps(RandomForestClassifier(**metadata["parameters"]), protocol=5))
    manifest_path = experiment / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["artifacts"][name].update(
        bytes=path.stat().st_size,
        sha256=sha256(path.read_bytes()).hexdigest(),
    )
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="estimator"):
        load_frozen_experiment(experiment, saved_data)


def test_reference_and_feature_window_mismatch_fail_before_model_loading(
    frozen: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autocan.benchmark import run_benchmark
    from autocan.cli import main
    from autocan.features import FeatureConfig
    from autocan.ml_ood import evaluate_ml_ood
    from autocan.ood import generate_ood

    run_benchmark(
        frozen / "source", tmp_path / "reference", config=FeatureConfig(window_ns=200_000_000)
    )
    generate_ood(
        frozen / "source",
        tmp_path / "reference",
        tmp_path / "suite",
        seed=19,
        fault_runs=2,
        normal_runs=2,
    )

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("incompatible OOD protocol must be rejected before loading models")

    monkeypatch.setattr("autocan.ml_ood.load_frozen_experiment", forbidden)
    with pytest.raises(ValueError, match="protocol/reference mismatch"):
        evaluate_ml_ood(
            frozen / "prepared",
            frozen / "suite",
            tmp_path / "wrong-reference",
            source=frozen / "source",
            reference=tmp_path / "reference",
            experiment=frozen / "experiment",
        )
    with pytest.raises(ValueError, match="feature configuration mismatch"):
        evaluate_ml_ood(
            frozen / "prepared",
            tmp_path / "suite",
            tmp_path / "wrong-window",
            source=frozen / "source",
            reference=tmp_path / "reference",
            experiment=frozen / "experiment",
        )
    assert not (tmp_path / "wrong-reference").exists()
    assert not (tmp_path / "wrong-window").exists()
    assert (
        main(arguments(frozen, tmp_path / "quota") + ["--max-bytes", str(64 * 1024 * 1024 + 1)])
        == 1
    )


def test_bootstrap_cli_preserves_point_metrics_and_never_loads_or_fits_models(
    frozen: Path,
    saved_ood: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sklearn.ensemble import IsolationForest, RandomForestClassifier

    from autocan.cli import main

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("bootstrap may only resample saved scores")

    for name in (
        "pickle.loads",
        "pickle.load",
        "autocan.model_artifacts.load_frozen_experiment",
        "autocan.isolation_forest.FittedForest.score",
        "autocan.random_forest.FittedRandomForest.score",
        "autocan.preprocessing.fit_training",
        "autocan.training_data.fit_training",
        "autocan.isolation_forest.choose_validation_threshold",
        "autocan.ml_experiment.choose_validation_threshold",
        "autocan.features.replay_observations",
    ):
        monkeypatch.setattr(name, forbidden)
    monkeypatch.setattr(IsolationForest, "fit", forbidden)
    monkeypatch.setattr(RandomForestClassifier, "fit", forbidden)
    first, second = tmp_path / "bootstrap", tmp_path / "bootstrap-again"

    def args(target: Path) -> list[str]:
        return [
            "bootstrap-metrics",
            *event_arguments(frozen, saved_ood, target)[1:],
            "--replicates",
            "30",
            "--seed",
            "42",
        ]

    assert main(args(first)) == 0
    assert main(args(second)) == 0
    for path in first.iterdir():
        assert path.read_bytes() == (second / path.name).read_bytes()
    metrics = json.loads((first / "metrics.json").read_text())
    assert len(metrics["rows"]) == 25
    id_metrics = json.loads((frozen / "experiment/metrics.json").read_text())["rows"]
    ood_metrics = json.loads((saved_ood / "metrics.json").read_text())["rows"]
    expected = {(r["split"], r["model_id"]): r for r in [*id_metrics, *ood_metrics]}
    events = tmp_path / "events-point"
    assert main(event_arguments(frozen, saved_ood, events)) == 0
    expected_events = {
        (r["split"], r["model_id"]): r
        for r in json.loads((events / "metrics.json").read_text())["rows"]
    }
    for row in metrics["rows"]:
        point = expected[row["split"], row["model_id"]]
        for metric in ("precision", "recall", "f1", "average_precision"):
            assert row["window"][metric]["estimate"] == pytest.approx(point[metric])
        event = expected_events[row["split"], row["model_id"]]
        for metric in ("detection_rate", "false_alarms_per_hour", "normal_duration_ns"):
            assert row["event"][metric]["estimate"] == pytest.approx(event[metric])
        for statistic in ("mean", "p50", "p95"):
            assert row["event"][f"latency_{statistic}_ns"]["estimate"] == pytest.approx(
                event["detection_latency_ns"][statistic]
            )
    manifest = json.loads((first / "manifest.json").read_text())
    assert manifest["models_trained"] == manifest["thresholds_selected"] == []
    assert manifest["scores_computed"] == 0
    assert manifest["bootstrap_seed"] == 42 and manifest["replicates"] == 30
    assert main(args(first)) == 1
    assert main(args(tmp_path / "quota") + ["--max-bytes", "4096"]) == 1
    assert not (tmp_path / "quota").exists()
    assert main(args(tmp_path / "bad-count") + ["--replicates", "10001"]) == 1
    assert not (tmp_path / "bad-count").exists()


@pytest.mark.parametrize("damage", ["binding", "scores"])
def test_bootstrap_rejects_corrupted_inputs_before_publishing(
    frozen: Path,
    saved_ood: Path,
    tmp_path: Path,
    damage: str,
) -> None:
    from autocan.cli import main

    root = copytree(saved_ood, tmp_path / "ood")
    if damage == "binding":
        path = root / "manifest.json"
        manifest = json.loads(path.read_text())
        manifest["experiment_manifest_sha256"] = "0" * 64
        path.write_text(json.dumps(manifest))
    else:
        path = root / "scores.parquet"
        path.write_bytes(path.read_bytes()[:-1])
    args = event_arguments(frozen, root, tmp_path / "invalid")
    assert main(["bootstrap-metrics", *args[1:], "--replicates", "10"]) == 1
    assert not (tmp_path / "invalid").exists()


def test_live_random_forest_matches_offline_and_records_provenance(
    tmp_path: Path, ml_joint: Path, ml_prepared: Path, ml_source: Path
) -> None:
    from autocan.api.runtime import Session
    from autocan.live_scoring import load_live_model
    from autocan.ml_ood import _run_windows
    from autocan.model_artifacts import load_frozen_experiment
    from autocan.network_profile import ECU_IDS
    from autocan.parquet import export_observer, export_trace, import_trace
    from autocan.scenarios import Scenario
    from autocan.traces import rerun, verify_trace

    model = load_live_model(ml_joint, ml_prepared, ml_source)
    scenario = Scenario(profile_id="democar-network", duration_ns=1_050_000_000)
    session = Session(
        tmp_path, seed=42, profile_id=scenario.profile_id, scenario=scenario, live_model=model
    )
    try:
        session.inputs(throttle=60)
        session.start()
        for _ in range(6):
            session.tick()
        assert len(session.live_scores) == 3  # the saved 100 ms configuration
        for ecu_id in ECU_IDS:
            session.offline(True, ecu_id)
        for _ in range(15):
            session.tick()
        assert session.stopped and session.feature_windows[-1].message_count == 0
    finally:
        session.close()

    run = tmp_path / session.id
    prepared = load_training_data(ml_prepared, source=ml_source)
    frozen = load_frozen_experiment(ml_joint, prepared, detector="random_forest")
    offline = tmp_path / "offline"
    export_observer(run, offline / "observer", receiver_id="observer")
    rows = _run_windows(offline, prepared.configuration, prepared.columns, "live", session.id, 0)
    assert len(rows) == 10
    assert [item.key for item in session.live_scores] == [row.key for row in rows]
    assert [item.score for item in session.live_scores] == pytest.approx(
        frozen.models["random_forest"].score(rows), abs=1e-12, rel=0
    )
    provenance = {
        "model_id": "random_forest",
        "model_sha256": sha256(
            (ml_joint / "models/random_forest/model.pkl").read_bytes()
        ).hexdigest(),
        "threshold": frozen.thresholds["random_forest"],
        "feature_schema_version": frozen.manifest["feature_schema_version"],
    }
    assert verify_trace(run)["live_model"] == provenance
    export_trace(run, tmp_path / "parquet")
    restored = tmp_path / "restored"
    assert import_trace(tmp_path / "parquet", restored)["live_model"] == provenance
    assert rerun(restored)["matches"] is True


def test_frozen_loader_rejects_pickle_changed_after_inspection(
    tmp_path: Path,
    ml_joint: Path,
    ml_prepared: Path,
    ml_source: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autocan import model_artifacts
    from autocan.traces import TraceError

    experiment = tmp_path / "experiment"
    copytree(ml_joint, experiment)
    data = load_training_data(ml_prepared, source=ml_source)
    metadata = model_artifacts.inspect_frozen_experiment(experiment, data)
    pickle_path = experiment / "models/random_forest/model.pkl"
    payload = pickle_path.read_bytes()
    pickle_path.write_bytes(bytes([payload[0] ^ 1]) + payload[1:])

    def forbid_unpickle(_: bytes) -> None:
        pytest.fail("changed model bytes reached the deserializer")

    monkeypatch.setattr(model_artifacts, "inspect_frozen_experiment", lambda *_: metadata)
    monkeypatch.setattr(pickle, "loads", forbid_unpickle)
    with pytest.raises(TraceError, match="checksum/size mismatch.*random_forest/model.pkl"):
        model_artifacts.load_frozen_experiment(experiment, data, detector="random_forest")
