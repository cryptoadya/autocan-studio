"""Shared source-bound reader for saved continuous scores and temporal events."""

from contextlib import ExitStack, closing
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import cast

from autocan.benchmark import _fault_episodes
from autocan.factory import FactoryManifest, RunSummary
from autocan.model_artifacts import FrozenExperimentMetadata, inspect_frozen_experiment
from autocan.ood import DOMAINS, verify_ood
from autocan.parquet import _file_hash
from autocan.score_artifacts import (
    inspect_saved_ood,
    iter_saved_ood_keys,
    iter_saved_scores,
    saved_policies,
)
from autocan.score_events import RunEvents, evaluate_score_events
from autocan.traces import TraceError
from autocan.training_artifacts import LoadedTrainingData, _table_rows, load_training_data
from autocan.training_data import LABEL_SCHEMA, MAX_ROWS, window_label
from autocan.validation import object_value
from autocan.window_evaluation import Comparison, WindowKey


@dataclass(frozen=True, slots=True)
class SavedRun:
    root: Path
    summary: RunSummary
    split: str
    keys: list[WindowKey]


def _runs(
    root: Path,
    factory: FactoryManifest,
    membership: dict[int, str],
    *,
    window_ns: int,
    warmup_ns: float,
    offset: int = 0,
) -> list[SavedRun]:
    runs = []
    for run in factory["runs"]:
        count = run["duration_ns"] // window_ns
        if offset + count > MAX_ROWS:
            raise TraceError("saved event window quota exceeded")
        part = membership[run["index"]]
        keys = [
            WindowKey(
                offset + i,
                run["run_id"],
                part,
                i * window_ns,
                (i + 1) * window_ns,
                (i + 1) * window_ns >= warmup_ns,
            )
            for i in range(count)
        ]
        runs.append(SavedRun(root / run["path"], run, part, keys))
        offset += count
    return runs


@dataclass(frozen=True, slots=True)
class ScoredRun:
    run_id: str
    split: str
    fault_type: str | None
    keys: list[WindowKey]
    labels: list[bool | None]
    scores: dict[str, list[float]]
    events: dict[str, RunEvents]


@dataclass(frozen=True, slots=True)
class SavedEvaluation:
    data: LoadedTrainingData
    frozen: FrozenExperimentMetadata
    ood_meta: dict[str, object]
    policies: dict[str, tuple[float, Comparison]]
    inputs: dict[str, str]
    runs: list[ScoredRun]


def load_saved_evaluation(
    prepared: Path,
    *,
    source: Path,
    experiment: Path,
    ood_results: Path,
    suite: Path,
    reference: Path,
) -> SavedEvaluation:
    data = load_training_data(prepared, source=source)
    frozen = inspect_frozen_experiment(experiment, data)
    ood_protocol, collections = verify_ood(source, reference, suite)
    if ood_protocol["feature_configuration"] != asdict(data.configuration):
        raise TraceError("event OOD feature configuration mismatch")
    ood_meta = inspect_saved_ood(
        ood_results,
        data,
        frozen,
        experiment=experiment,
        source=source,
        suite=suite,
        reference=reference,
    )
    policies = saved_policies(data, frozen)
    warmup = max(
        float(v)
        for k, v in data._feature_template.items()
        if k.endswith("_timeout_ns") and v is not None
    )
    window_ns = data.configuration.window_ns
    id_runs = _runs(
        source,
        collections["id"],
        {r["index"]: r["split"] for r in data.split["assignments"]},
        window_ns=window_ns,
        warmup_ns=warmup,
    )
    ood_runs: list[SavedRun] = []
    for domain in DOMAINS:
        ood_runs.extend(
            _runs(
                suite / domain,
                collections[domain],
                {r["index"]: domain for r in collections[domain]["runs"]},
                window_ns=window_ns,
                warmup_ns=warmup,
                offset=sum(len(r.keys) for r in ood_runs),
            )
        )
    inputs = {
        name: _file_hash(path / "manifest.json")
        for name, path in {
            "prepared": prepared,
            "source": source,
            "experiment": experiment,
            "ood_results": ood_results,
            "suite": suite,
            "reference": reference,
        }.items()
    }
    samples: list[ScoredRun] = []
    with ExitStack() as stack:
        id_scores = stack.enter_context(
            closing(
                iter_saved_scores(
                    experiment, frozen.manifest, (k for r in id_runs for k in r.keys), policies
                )
            )
        )
        ood_keys = stack.enter_context(
            closing(
                iter_saved_ood_keys(ood_results, ood_meta, (k for r in ood_runs for k in r.keys))
            )
        )
        ood_scores = stack.enter_context(
            closing(iter_saved_scores(ood_results, ood_meta, ood_keys, policies))
        )
        inventory = object_value(ood_meta["artifacts"], "OOD inventory")
        labels = stack.enter_context(
            closing(
                _table_rows(
                    ood_results,
                    "labels/windows.parquet",
                    LABEL_SCHEMA,
                    object_value(inventory["labels/windows.parquet"], "OOD labels"),
                )
            )
        )
        for groups, stream, is_ood in (
            (id_runs, id_scores, False),
            (ood_runs, ood_scores, True),
        ):
            for run in groups:
                scores: dict[str, list[float]] = {name: [] for name in policies}
                for _ in run.keys:
                    predicted = next(stream)
                    for name in policies:
                        scores[name].append(predicted[name])
                faults = _fault_episodes(run.root / "trace", run.summary["duration_ns"])
                truth = [window_label(k.start_ns, k.end_ns, k.eligible, faults) for k in run.keys]
                if is_ood:
                    for key, label in zip(run.keys, truth, strict=True):
                        expected = {"row_index": key.row_index, "normal_run": not faults, **label}
                        if next(labels, None) != expected:
                            raise TraceError("saved OOD truth alignment mismatch")
                events = {}
                for name, (threshold, comparison) in policies.items():
                    result = evaluate_score_events(
                        run.keys,
                        [
                            v >= threshold if comparison == "ge" else v > threshold
                            for v in scores[name]
                        ],
                        faults,
                        model_id=name,
                        window_ns=window_ns,
                    )
                    events[name] = result
                samples.append(
                    ScoredRun(
                        run.summary["run_id"],
                        run.split,
                        run.summary["fault_type"],
                        run.keys,
                        [cast(bool | None, t["label"]) for t in truth],
                        scores,
                        events,
                    )
                )
            if next(stream, None) is not None:
                raise TraceError("extra saved decision windows")
        if next(labels, None) is not None:
            raise TraceError("extra saved OOD truth rows")
    return SavedEvaluation(data, frozen, ood_meta, policies, inputs, samples)
