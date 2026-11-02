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


@pytest.fixture(scope="module")
def prepared(source: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    from autocan.features import FeatureConfig
    from autocan.training_data import prepare_training_data

    root = tmp_path_factory.mktemp("saved-windows") / "prepared"
    prepare_training_data(source, root, config=FeatureConfig(window_ns=100_000_000))
    return root


def test_loader_validates_existing_export_without_refitting(
    source: Path, prepared: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autocan.training_artifacts import load_training_data

    def no_fit(*args: object, **kwargs: object) -> None:
        pytest.fail("loading/evaluation must not fit preprocessing or detectors")

    monkeypatch.setattr("autocan.preprocessing.fit_training", no_fit)
    monkeypatch.setattr("autocan.training_data.fit_training", no_fit)
    loaded = load_training_data(prepared, source=source)
    rows = list(loaded.iter_windows())
    assert len(rows) == 960
    assert sum(not row.key.eligible for row in rows) == 48
    assert loaded.columns == tuple(pq.read_schema(prepared / "features.parquet").names)
    assert rows[0].key.row_index == 0 and rows[-1].key.row_index == 959
    assert all(len(row.features) == len(loaded.columns) for row in rows)


@pytest.mark.parametrize("damage", ["status", "version", "path", "checksum", "lineage", "semantic"])
def test_loader_rejects_invalid_manifest_or_inventory(
    source: Path, prepared: Path, tmp_path: Path, damage: str
) -> None:
    from shutil import copytree

    from autocan.training_artifacts import load_training_data

    root = copytree(prepared, tmp_path / "broken")
    path = root / "manifest.json"
    manifest = json.loads(path.read_text())
    if damage == "status":
        manifest["status"] = "incomplete"
    elif damage == "version":
        manifest["training_data_version"] = "unknown"
    elif damage == "path":
        manifest["artifacts"]["../outside"] = manifest["artifacts"].pop("features.parquet")
    elif damage == "checksum":
        (root / "features.parquet").write_bytes(b"not parquet")
    elif damage == "lineage":
        manifest["source_manifest_sha256"] = "0" * 64
    else:
        manifest["semantic_sha256"] = "0" * 64
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        load_training_data(root, source=source)


@pytest.mark.parametrize("directory", [False, True])
def test_loader_rejects_symlink_artifacts(
    source: Path, prepared: Path, tmp_path: Path, directory: bool
) -> None:
    from shutil import copytree, rmtree

    from autocan.training_artifacts import load_training_data

    root = copytree(prepared, tmp_path / "linked")
    name = "labels" if directory else "features.parquet"
    link = root / name
    if directory:
        rmtree(link)
    else:
        link.unlink()
    link.symlink_to(prepared / name, target_is_directory=directory)
    with pytest.raises(ValueError, match="symlink"):
        load_training_data(root, source=source)


@pytest.mark.parametrize("damage", ["schema", "alignment", "split", "label", "mask", "scaled"])
def test_loader_rejects_rehashed_inconsistent_tables(
    source: Path, prepared: Path, tmp_path: Path, damage: str
) -> None:
    from hashlib import sha256
    from shutil import copytree

    import pyarrow as pa

    from autocan.training_artifacts import load_training_data
    from autocan.validation import canonical_bytes

    root = copytree(prepared, tmp_path / "rehashed")
    name = {
        "schema": "features.parquet",
        "alignment": "windows.parquet",
        "split": "windows.parquet",
        "label": "labels/windows.parquet",
        "mask": "features.parquet",
        "scaled": "scaled/all_train.parquet",
    }[damage]
    table = pq.read_table(root / name)
    rows = table.to_pylist()
    if damage == "schema":
        table = table.select(list(reversed(table.column_names)))
    else:
        if damage == "alignment":
            rows[0]["row_index"] = 1
        elif damage == "split":
            rows[0]["split"] = "other"
        elif damage == "label":
            rows[0]["normal_run"] = not rows[0]["normal_run"]
        elif damage == "mask":
            rows[0]["missing__rpm_mean"] = 1.0 - rows[0]["missing__rpm_mean"]
        else:
            rows[0][table.column_names[0]] += 1
        table = pa.Table.from_pylist(rows, schema=table.schema)
    pq.write_table(table, root / name, row_group_size=256)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["artifacts"][name].update(
        bytes=(root / name).stat().st_size,
        sha256=sha256((root / name).read_bytes()).hexdigest(),
        semantic_sha256=sha256(
            b"".join(canonical_bytes(row) + b"\n" for row in table.to_pylist())
        ).hexdigest(),
    )
    manifest_path.write_text(json.dumps(manifest))
    expected = {
        "schema": "schema: features.parquet",
        "alignment": "key alignment",
        "split": "key alignment",
        "label": "label alignment",
        "mask": "missing mask",
        "scaled": "scaled training table",
    }[damage]
    with pytest.raises(ValueError, match=expected):
        load_training_data(root, source=source)


@pytest.mark.parametrize("damage", ["coverage", "scope", "fingerprint", "split"])
def test_loader_validates_rehashed_json_contracts(
    source: Path, prepared: Path, tmp_path: Path, damage: str
) -> None:
    from hashlib import sha256
    from shutil import copytree

    from autocan.training_artifacts import load_training_data

    root = copytree(prepared, tmp_path / "json-contract")
    name = (
        "coverage.json"
        if damage == "coverage"
        else "split.json"
        if damage == "split"
        else "preprocessing/all_train.json"
    )
    path = root / name
    value = json.loads(path.read_text())
    if damage == "coverage":
        value["partitions"]["train"]["eligible_windows"] += 1
    elif damage == "scope":
        value["normal_only"] = True
    elif damage == "fingerprint":
        value["fit_rows_sha256"] = "0" * 64
    else:
        value["assignments"][0]["split"] = "other"
    path.write_text(json.dumps(value))
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["artifacts"][name].update(
        bytes=path.stat().st_size, sha256=sha256(path.read_bytes()).hexdigest()
    )
    manifest_path.write_text(json.dumps(manifest))
    expected = (
        "coverage"
        if damage == "coverage"
        else "scope|fingerprint"
        if damage in ("scope", "fingerprint")
        else "split"
    )
    with pytest.raises(ValueError, match=expected):
        load_training_data(root, source=source)


def test_rules_cli_uses_saved_split_and_is_reproducible(
    source: Path, prepared: Path, tmp_path: Path
) -> None:
    from autocan.cli import main

    first, second = tmp_path / "first", tmp_path / "second"
    for target in (first, second):
        assert (
            main(
                ["evaluate-rules", str(prepared), "--source", str(source), "--output", str(target)]
            )
            == 0
        )
    for name in ("manifest.json", "metrics.json", "report.md", "split.json"):
        assert (first / name).read_bytes() == (second / name).read_bytes()
    assert (first / "split.json").read_bytes() == (prepared / "split.json").read_bytes()
    metrics = json.loads((first / "metrics.json").read_text())
    coverage = json.loads((prepared / "coverage.json").read_text())["partitions"]
    assert len(metrics["rows"]) == 9
    for row in metrics["rows"]:
        assert row["evaluated_windows"] == coverage[row["split"]]["eligible_windows"]
        assert row["positive_windows"] == coverage[row["split"]]["positive_windows"]
    assert json.loads((first / "manifest.json").read_text())["models_trained"] == []
    assert (
        main(["evaluate-rules", str(prepared), "--source", str(source), "--output", str(first)])
        == 1
    )
    failed = tmp_path / "quota"
    assert (
        main(
            [
                "evaluate-rules",
                str(prepared),
                "--source",
                str(source),
                "--output",
                str(failed),
                "--max-bytes",
                "4096",
            ]
        )
        == 1
    )
    assert not failed.exists()


def test_isolation_forest_cli_fits_normal_train_selects_validation_and_compares_rules(
    source: Path, prepared: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autocan.cli import main

    def no_preprocessing_fit(*args: object, **kwargs: object) -> None:
        pytest.fail("model experiments must reuse frozen preprocessing")

    monkeypatch.setattr("autocan.preprocessing.fit_training", no_preprocessing_fit)
    monkeypatch.setattr("autocan.training_data.fit_training", no_preprocessing_fit)
    first, second = tmp_path / "iforest-first", tmp_path / "iforest-second"
    for target in (first, second):
        assert (
            main(
                [
                    "train-isolation-forest",
                    str(prepared),
                    "--source",
                    str(source),
                    "--output",
                    str(target),
                    "--seed",
                    "42",
                ]
            )
            == 0
        )
    for path in first.iterdir():
        assert path.read_bytes() == (second / path.name).read_bytes()
    assert (first / "split.json").read_bytes() == (prepared / "split.json").read_bytes()
    assert (first / "preprocessing.json").read_bytes() == (
        prepared / "preprocessing/normal_train.json"
    ).read_bytes()
    model = json.loads((first / "model.json").read_text())
    frozen = json.loads((prepared / "preprocessing/normal_train.json").read_text())
    assert model["fit_rows"] == frozen["fit_rows"]
    assert model["fit_rows_sha256"] == frozen["fit_rows_sha256"]
    selection = json.loads((first / "threshold.json").read_text())
    assert selection["split"] == "validation" and selection["comparison"] == "ge"
    scores = pq.read_table(first / "scores.parquet").to_pylist()
    windows = pq.read_table(prepared / "windows.parquet").to_pylist()
    labels = pq.read_table(prepared / "labels/windows.parquet").to_pylist()
    assert len(scores) == 4 * len(windows)
    assert not set(pq.read_schema(first / "scores.parquet").names) & {
        "label",
        "fault_ids",
        "normal_run",
    }
    val = [w for w in windows if w["split"] == "validation" and w["eligible"]]
    assert selection["windows"] == len(val)
    model_rows = [r for r in scores if r["model_id"] == "isolation_forest"]
    candidates = sorted({model_rows[w["row_index"]]["score"] for w in val})

    def f1(threshold: float) -> float:
        tp = sum(
            labels[w["row_index"]]["label"] is True
            and model_rows[w["row_index"]]["score"] >= threshold
            for w in val
        )
        positives = sum(labels[w["row_index"]]["label"] is True for w in val)
        predicted = sum(model_rows[w["row_index"]]["score"] >= threshold for w in val)
        return float(2 * tp / (positives + predicted))

    assert selection["threshold"] == max(candidates, key=lambda t: (f1(t), t))
    metrics = json.loads((first / "metrics.json").read_text())
    assert len(metrics["rows"]) == 12
    from autocan.window_benchmark import evaluate_saved_rules

    reference = tmp_path / "rule-reference"
    evaluate_saved_rules(prepared, reference, source=source)
    assert [r for r in metrics["rows"] if r["model_id"] != "isolation_forest"] == json.loads(
        (reference / "metrics.json").read_text()
    )["rows"]
    for part in ("train", "validation", "test"):
        selected = [r for r in metrics["rows"] if r["split"] == part]
        assert len(selected) == 4
        assert len({r["evaluated_windows"] for r in selected}) == 1
        assert len({r["positive_windows"] for r in selected}) == 1
    manifest = json.loads((first / "manifest.json").read_text())
    assert manifest["models_trained"] == ["isolation_forest"]
    assert manifest["ood_used"] is False
    assert (
        main(
            [
                "train-isolation-forest",
                str(prepared),
                "--source",
                str(source),
                "--output",
                str(first),
            ]
        )
        == 1
    )
    failed = tmp_path / "iforest-quota"
    assert (
        main(
            [
                "train-isolation-forest",
                str(prepared),
                "--source",
                str(source),
                "--output",
                str(failed),
                "--max-bytes",
                "4096",
            ]
        )
        == 1
    )
    assert not failed.exists()


def test_random_forest_cli_compares_five_detectors_with_validation_only_thresholds(
    source: Path, prepared: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hashlib import sha256

    from autocan.cli import main
    from autocan.validation import canonical_bytes

    def no_preprocessing_fit(*args: object, **kwargs: object) -> None:
        pytest.fail("both classifiers must reuse their saved preprocessing scopes")

    monkeypatch.setattr("autocan.preprocessing.fit_training", no_preprocessing_fit)
    monkeypatch.setattr("autocan.training_data.fit_training", no_preprocessing_fit)
    first, second = tmp_path / "rf-first", tmp_path / "rf-second"
    for target in (first, second):
        assert (
            main(
                [
                    "train-random-forest",
                    str(prepared),
                    "--source",
                    str(source),
                    "--output",
                    str(target),
                    "--seed",
                    "42",
                ]
            )
            == 0
        )
    for path in first.rglob("*"):
        if path.is_file():
            assert path.read_bytes() == (second / path.relative_to(first)).read_bytes()
    windows = pq.read_table(prepared / "windows.parquet").to_pylist()
    labels = pq.read_table(prepared / "labels/windows.parquet").to_pylist()
    targets = [w for w in windows if w["split"] == "train" and w["eligible"]]
    model = json.loads((first / "models/random_forest/model.json").read_text())
    frozen = json.loads((prepared / "preprocessing/all_train.json").read_text())
    assert model["fit_rows"] == frozen["fit_rows"] == len(targets)
    assert model["fit_rows_sha256"] == frozen["fit_rows_sha256"]
    assert (
        model["label_rows_sha256"]
        == sha256(
            b"".join(
                canonical_bytes(
                    {"row_index": w["row_index"], "label": labels[w["row_index"]]["label"]}
                )
                + b"\n"
                for w in targets
            )
        ).hexdigest()
    )
    for model_id, scope in (("random_forest", "all_train"), ("isolation_forest", "normal_train")):
        assert (first / f"models/{model_id}/preprocessing.json").read_bytes() == (
            prepared / f"preprocessing/{scope}.json"
        ).read_bytes()
    assert (first / "split.json").read_bytes() == (prepared / "split.json").read_bytes()
    scores = pq.read_table(first / "scores.parquet").to_pylist()
    assert len(scores) == 5 * len(windows)
    val = [w for w in windows if w["split"] == "validation" and w["eligible"]]
    for model_id in ("random_forest", "isolation_forest"):
        selection = json.loads((first / f"models/{model_id}/threshold.json").read_text())
        assert selection["split"] == "validation" and selection["windows"] == len(val)
        model_scores = [r for r in scores if r["model_id"] == model_id]
        cutoffs = {model_scores[w["row_index"]]["score"] for w in val}

        def f1(
            cutoff: float,
            probabilities: tuple[float, ...] = tuple(float(r["score"]) for r in model_scores),
        ) -> float:
            tp = sum(
                labels[w["row_index"]]["label"] is True and probabilities[w["row_index"]] >= cutoff
                for w in val
            )
            positives = sum(labels[w["row_index"]]["label"] is True for w in val)
            predicted = sum(probabilities[w["row_index"]] >= cutoff for w in val)
            return float(2 * tp / (positives + predicted))

        assert selection["threshold"] == max(cutoffs, key=lambda t: (f1(t), t))
    metrics = json.loads((first / "metrics.json").read_text())
    assert len(metrics["rows"]) == 15
    assert metrics["format"] == "autocan-forest-comparison-metrics"
    reference = tmp_path / "iforest-reference"
    assert (
        main(
            [
                "train-isolation-forest",
                str(prepared),
                "--source",
                str(source),
                "--output",
                str(reference),
            ]
        )
        == 0
    )
    assert [r for r in metrics["rows"] if r["model_id"] != "random_forest"] == json.loads(
        (reference / "metrics.json").read_text()
    )["rows"]
    assert (first / "models/isolation_forest/model.pkl").read_bytes() == (
        reference / "model.pkl"
    ).read_bytes()
    for part in ("train", "validation", "test"):
        rows = [r for r in metrics["rows"] if r["split"] == part]
        assert len(rows) == 5
        assert len({r["evaluated_windows"] for r in rows}) == 1
        assert len({r["positive_windows"] for r in rows}) == 1
    manifest = json.loads((first / "manifest.json").read_text())
    assert manifest["models_trained"] == ["isolation_forest", "random_forest"]
    assert manifest["ood_used"] is False
    assert (
        main(
            ["train-random-forest", str(prepared), "--source", str(source), "--output", str(first)]
        )
        == 1
    )
    failed = tmp_path / "rf-quota"
    assert (
        main(
            [
                "train-random-forest",
                str(prepared),
                "--source",
                str(source),
                "--output",
                str(failed),
                "--max-bytes",
                "4096",
            ]
        )
        == 1
    )
    assert not failed.exists()
