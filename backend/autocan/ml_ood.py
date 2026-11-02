"""Score both frozen OOD domains with trusted saved forests and fixed rules."""

from contextlib import ExitStack
from dataclasses import asdict
from hashlib import sha256
from importlib.metadata import version
from importlib.resources import files
from pathlib import Path
from typing import cast

import pyarrow as pa

from autocan.benchmark import _fault_episodes
from autocan.clock import SimulationClock
from autocan.evaluation import RULE_IDS
from autocan.factory import _read_json
from autocan.features import (
    DETECTOR_ID,
    FEATURE_SCHEMA_VERSION,
    FeatureConfig,
    FeaturePipeline,
    FeatureWindow,
    replay_observations,
)
from autocan.ml_experiment import SCORE_SCHEMA, _batches
from autocan.model_artifacts import MODEL_FILES, MODEL_IDS, load_frozen_experiment
from autocan.ood import DOMAINS, OOD_VERSION, verify_ood
from autocan.parquet import _file_hash, _finish, _new_output, _TableWriter, open_observer
from autocan.traces import TraceError, _git_provenance, _packages
from autocan.training_artifacts import PreparedWindow, load_training_data
from autocan.training_data import KEY_SCHEMA, LABEL_SCHEMA, MAX_ROWS, window_features, window_label
from autocan.validation import canonical_bytes, integer
from autocan.window_benchmark import DEFAULT_OUTPUT_BYTES, MAX_OUTPUT_BYTES, _report
from autocan.window_evaluation import (
    WINDOW_EVALUATOR_VERSION,
    Comparison,
    WindowKey,
    WindowScore,
    WindowTruth,
    baseline_window_scores,
    evaluate_windows,
)

ML_OOD_VERSION = "frozen-forest-ood-1"


def _run_windows(
    root: Path,
    config: FeatureConfig,
    columns: tuple[str, ...],
    domain: str,
    run_id: str,
    offset: int,
) -> list[PreparedWindow]:
    rows: list[PreparedWindow] = []

    def consume(window: FeatureWindow) -> None:
        if offset + len(rows) >= MAX_ROWS:
            raise TraceError("OOD feature window quota exceeded")
        vector = window_features(window)
        if tuple(vector) != columns:
            raise TraceError("OOD feature columns/order mismatch")
        eligible = window.end_ns >= max(m.timeout_ns for m in window.messages)
        rows.append(
            PreparedWindow(
                WindowKey(
                    offset + len(rows), run_id, domain, window.start_ns, window.end_ns, eligible
                ),
                tuple(vector.values()),
                None,
                False,
            )
        )

    with open_observer(root / "observer") as observed:
        clock = SimulationClock()
        pipeline = FeaturePipeline(
            clock,
            profile_id=observed.profile_id,
            receiver_id=observed.receiver_id,
            end_ns=observed.end_ns,
            config=config,
            on_window=consume,
        )
        replay_observations(observed.observations, clock, pipeline, end_ns=observed.end_ns)
    return rows


def evaluate_ml_ood(
    prepared: Path,
    suite: Path,
    target: Path,
    *,
    source: Path,
    reference: Path,
    experiment: Path,
    max_bytes: int = DEFAULT_OUTPUT_BYTES,
) -> dict[str, object]:
    if integer(max_bytes, "ML OOD output quota", minimum=4096) > MAX_OUTPUT_BYTES:
        raise TraceError("ML OOD output quota exceeds 64 MiB")
    with _new_output(target) as staging:
        data = load_training_data(prepared, source=source)
        protocol, collections = verify_ood(source, reference, suite)
        if protocol["feature_configuration"] != asdict(data.configuration):
            raise TraceError("OOD protocol and frozen ML feature configuration mismatch")
        frozen = load_frozen_experiment(experiment, data)

        def save(name: str, raw: bytes) -> None:
            used = sum(p.stat().st_size for p in staging.rglob("*") if p.is_file())
            if used + len(raw) > max_bytes:
                raise TraceError("ML OOD output quota exceeded")
            path = staging / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)

        save("protocol.json", (suite / "protocol.json").read_bytes())
        save("split.json", (experiment / "split.json").read_bytes())
        for name in MODEL_IDS:
            for file in MODEL_FILES:
                if file != "model.pkl":
                    relative = f"models/{name}/{file}"
                    save(relative, (experiment / relative).read_bytes())
        detectors = (*RULE_IDS, *MODEL_IDS)
        targets: list[WindowTruth] = []
        predictions: dict[str, list[WindowScore]] = {name: [] for name in detectors}
        thresholds = dict(frozen.thresholds)
        comparisons: dict[str, Comparison] = {name: "ge" for name in MODEL_IDS}
        fault_types: dict[str, str] = {}
        schemas = {
            "features.parquet": pa.schema([pa.field(c, pa.float64()) for c in data.columns]),
            "windows.parquet": KEY_SCHEMA,
            "labels/windows.parquet": LABEL_SCHEMA,
            "scores.parquet": SCORE_SCHEMA,
        }
        with ExitStack() as stack:
            writers = {
                name: _TableWriter(staging, name, schema, max_bytes)
                for name, schema in schemas.items()
            }
            for writer in writers.values():
                stack.callback(writer.close)
            for domain in DOMAINS:
                for run in collections[domain]["runs"]:
                    root = suite / domain / run["path"]
                    windows = _run_windows(
                        root, data.configuration, data.columns, domain, run["run_id"], len(targets)
                    )
                    # Models see only numeric feature tuples; no fault labels are read here.
                    for batch in _batches(windows):
                        values = {name: model.score(batch) for name, model in frozen.models.items()}
                        for i, row in enumerate(batch):
                            raw = dict(zip(data.columns, row.features, strict=True))
                            evidence = baseline_window_scores(raw, data.configuration)
                            writers["features.parquet"].write(cast(dict[str, object], raw))
                            writers["windows.parquet"].write(asdict(row.key))
                            for name in detectors:
                                score = (
                                    values[name][i]
                                    if name in frozen.models
                                    else evidence[name].score
                                )
                                if name in evidence:
                                    thresholds[name] = evidence[name].threshold
                                    comparisons[name] = evidence[name].comparison
                                cutoff, comparison = thresholds[name], comparisons[name]
                                predictions[name].append(WindowScore(row.key, score))
                                writers["scores.parquet"].write(
                                    {
                                        "row_index": row.key.row_index,
                                        "model_id": name,
                                        "score": score,
                                        "threshold": cutoff,
                                        "comparison": comparison,
                                        "anomalous": score >= cutoff
                                        if comparison == "ge"
                                        else score > cutoff,
                                    }
                                )
                    # Truth stays physically separate and is projected only after scoring.
                    faults = _fault_episodes(root / "trace", run["duration_ns"])
                    if domain == "ood_fault":
                        fault_types[run["run_id"]] = str(run["fault_type"])
                    for row in windows:
                        label = window_label(
                            row.key.start_ns, row.key.end_ns, row.key.eligible, faults
                        )
                        writers["labels/windows.parquet"].write(
                            {
                                "row_index": row.key.row_index,
                                "normal_run": not faults,
                                **label,
                            }
                        )
                        targets.append(WindowTruth(row.key, cast(bool | None, label["label"])))
            score_hash = writers["scores.parquet"].digest.hexdigest()
            feature_hash = writers["features.parquet"].digest.hexdigest()
        rows = [
            evaluate_windows(
                targets,
                predictions[name],
                model_id=name,
                split=domain,
                threshold=thresholds[name],
                comparison=comparisons[name],
            )
            for domain in DOMAINS
            for name in detectors
        ]
        by_fault = []
        for kind in sorted(set(fault_types.values())):
            indices = [
                i for i, truth in enumerate(targets) if fault_types.get(truth.key.run_id) == kind
            ]
            for name in detectors:
                row_metrics = evaluate_windows(
                    [targets[i] for i in indices],
                    [predictions[name][i] for i in indices],
                    model_id=name,
                    split="ood_fault",
                    threshold=thresholds[name],
                    comparison=comparisons[name],
                )
                by_fault.append({"fault_type": kind, **row_metrics})
        normal = []
        for name in detectors:
            episodes = positive = duration = 0
            previous_run = None
            active = False
            for truth, prediction in zip(targets, predictions[name], strict=True):
                key = truth.key
                if key.split != "ood_normal" or not key.eligible:
                    continue
                assert prediction.score is not None
                anomalous = (
                    prediction.score >= thresholds[name]
                    if comparisons[name] == "ge"
                    else prediction.score > thresholds[name]
                )
                episodes += int(anomalous and (not active or previous_run != key.run_id))
                positive += int(anomalous)
                duration += key.end_ns - key.start_ns
                active, previous_run = anomalous, key.run_id
            normal.append(
                {
                    "split": "ood_normal",
                    "model_id": name,
                    "false_positive_windows": positive,
                    "false_alarm_episodes": episodes,
                    "normal_duration_ns": duration,
                    "false_alarms_per_hour": episodes * 3.6e12 / duration if duration else None,
                }
            )
        metrics = {
            "format": "autocan-ml-ood-metrics",
            "format_version": 1,
            "evaluation_version": ML_OOD_VERSION,
            "evaluator_version": WINDOW_EVALUATOR_VERSION,
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "configuration": asdict(data.configuration),
            "experiment_semantic_sha256": frozen.manifest["semantic_sha256"],
            "suite_semantic_sha256": _read_json(suite / "manifest.json")["semantic_sha256"],
            "rows": rows,
            "fault_type_rows": by_fault,
            "normal_false_alarms": normal,
            "normal_alarm_policy": (
                "contiguous positive eligible windows per run; no debounce; warm-up excluded"
            ),
        }
        save("metrics.json", canonical_bytes(metrics) + b"\n")
        report = _report(
            rows,
            str(data.manifest["semantic_sha256"]),
            title="Frozen forest OOD comparison",
            model_note="Both saved models, preprocessing and validation thresholds are reused. "
            "No fitting, threshold selection or ID test scoring occurs. "
            "OOD domains remain separate.",
        )
        report += (
            "\n## Unseen normal regimes\n\n"
            "| Detector | False alarm episodes | Eligible seconds | FA/hour |\n"
            "| --- | ---: | ---: | ---: |\n"
        )
        for summary in normal:
            report += (
                f"| {summary['model_id']} | {summary['false_alarm_episodes']} | "
                f"{cast(int, summary['normal_duration_ns']) / 1e9:.6g} | "
                f"{summary['false_alarms_per_hour']} |\n"
            )
        report += (
            "\nEpisodes are contiguous positive eligible windows per run, without debounce. "
            "Exposure excludes warm-up and partial tails. This differs from M8 latched, "
            "target-aware episodes. No fault-detection claim follows from normal OOD.\n"
            "\nPer-fault-type window metrics are in metrics.json; small synthetic samples "
            "do not establish real-vehicle generalization.\n"
        )
        save("report.md", report.encode())
        implementation = sha256()
        for name in (
            "ml_ood",
            "model_artifacts",
            "features",
            "training_data",
            "window_evaluation",
            "isolation_forest",
            "random_forest",
            "preprocessing",
            "ood",
        ):
            implementation.update(
                name.encode() + b"\0" + files("autocan").joinpath(f"{name}.py").read_bytes()
            )
        manifest = _finish(
            staging,
            {
                "format": "autocan-ml-ood-evaluation",
                "format_version": 1,
                "status": "complete",
                "evaluation_version": ML_OOD_VERSION,
                "ood_version": OOD_VERSION,
                "evaluator_version": WINDOW_EVALUATOR_VERSION,
                "baseline_detector_id": DETECTOR_ID,
                "feature_schema_version": FEATURE_SCHEMA_VERSION,
                "configuration": asdict(data.configuration),
                "lineage": data.manifest["lineage"],
                "source_manifest_sha256": _file_hash(source / "manifest.json"),
                "prepared_manifest_sha256": _file_hash(prepared / "manifest.json"),
                "experiment_manifest_sha256": _file_hash(experiment / "manifest.json"),
                "suite_manifest_sha256": _file_hash(suite / "manifest.json"),
                "reference_manifest_sha256": _file_hash(reference / "manifest.json"),
                "models_trained": [],
                "thresholds_selected": [],
                "id_test_scored": False,
                "seed": frozen.manifest["seed"],
                "implementation_sha256": implementation.hexdigest(),
                "package_versions": {
                    **_packages(),
                    **{
                        n: version(n)
                        for n in (
                            "pyarrow",
                            "numpy",
                            "scipy",
                            "scikit-learn",
                            "joblib",
                            "threadpoolctl",
                        )
                    },
                },
                **_git_provenance(),
                "semantic_sha256": sha256(
                    canonical_bytes(
                        {
                            "metrics": metrics,
                            "scores_semantic_sha256": score_hash,
                            "features_semantic_sha256": feature_hash,
                        }
                    )
                ).hexdigest(),
            },
            writers,
            max_bytes,
        )
    return manifest
