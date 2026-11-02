"""Evaluate saved ID/OOD window decisions as temporal fault/alert episodes."""

from collections import Counter
from contextlib import ExitStack, closing
from dataclasses import asdict, dataclass
from hashlib import sha256
from importlib.resources import files
from pathlib import Path

from autocan.benchmark import _fault_episodes
from autocan.evaluation import SPLITS, summarize
from autocan.factory import FactoryManifest, RunSummary
from autocan.model_artifacts import inspect_frozen_experiment
from autocan.ood import DOMAINS, verify_ood
from autocan.parquet import _file_hash, _finish, _new_output
from autocan.score_artifacts import (
    inspect_saved_ood,
    iter_saved_decisions,
    iter_saved_ood_keys,
    saved_policies,
)
from autocan.score_events import (
    EVENT_EVALUATOR_VERSION,
    MATCHING_POLICY,
    RunEvents,
    evaluate_score_events,
)
from autocan.traces import TraceError, _git_provenance, _packages
from autocan.training_artifacts import _table_rows, load_training_data
from autocan.training_data import LABEL_SCHEMA, MAX_ROWS, window_label
from autocan.validation import canonical_bytes, integer, object_value
from autocan.window_benchmark import DEFAULT_OUTPUT_BYTES, MAX_OUTPUT_BYTES
from autocan.window_evaluation import WindowKey


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


def _milliseconds(value: object) -> str:
    return "n/a" if value is None else f"{float(str(value)) / 1e6:.6g}"


def _report(rows: list[dict[str, object]], fault_rows: list[dict[str, object]]) -> str:
    text = [
        "# Saved-score event evaluation",
        "",
        "Saved decisions only; no loading, inference, fitting or threshold selection.",
        f"Evaluator `{EVENT_EVALUATOR_VERSION}`; "
        "temporal matching does not establish localisation.",
        "",
        "| Split | Detector | Faults | Detected | Rate | Latency mean ms | p50 ms | p95 ms |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        latency = object_value(row["detection_latency_ns"], "latency")
        rate = (
            "n/a" if row["detection_rate"] is None else f"{float(str(row['detection_rate'])):.6g}"
        )
        text.append(
            f"| {row['split']} | {row['model_id']} | {row['fault_episodes']} | "
            f"{row['detected_episodes']} | {rate} | {_milliseconds(latency['mean'])} | "
            f"{_milliseconds(latency['p50'])} | {_milliseconds(latency['p95'])} |"
        )
    text.extend(
        [
            "",
            "## Fault types",
            "",
            "| Split | Detector | Fault type | Faults | Detected | Rate | Mean ms | p50 | p95 |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in fault_rows:
        latency = object_value(row["detection_latency_ns"], "latency")
        rate = (
            "n/a" if row["detection_rate"] is None else f"{float(str(row['detection_rate'])):.6g}"
        )
        text.append(
            f"| {row['split']} | {row['model_id']} | {row['fault_type']} | "
            f"{row['fault_episodes']} | {row['detected_episodes']} | {rate} | "
            f"{_milliseconds(latency['mean'])} | {_milliseconds(latency['p50'])} | "
            f"{_milliseconds(latency['p95'])} |"
        )
    text.extend(
        [
            "",
            "Alarm onset is at positive window end; "
            "contiguous eligible positives form one episode.",
            "An already-open alarm cannot detect a later fault; matching is one-to-one.",
            "Matching: [original fault start, clipped end + one window), upper bound excluded.",
            "Warm-up/partial tails are excluded. runs.jsonl records unscored/censored faults.",
            "Latency is conditional on detection; "
            "original start and window quantization are retained.",
            "No events or detected latencies means n/a. "
            "Normal exposure/FA rates are in metrics.json.",
            "Synthetic samples and in-selection validation rows do not establish generalization.",
            "",
        ]
    )
    return "\n".join(text)


def evaluate_saved_events(
    prepared: Path,
    target: Path,
    *,
    source: Path,
    experiment: Path,
    ood_results: Path,
    suite: Path,
    reference: Path,
    max_bytes: int = DEFAULT_OUTPUT_BYTES,
) -> dict[str, object]:
    if integer(max_bytes, "event output quota", minimum=4096) > MAX_OUTPUT_BYTES:
        raise TraceError("event output quota exceeds 64 MiB")
    with _new_output(target) as staging:
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
        protocol = {
            "format": "autocan-saved-event-protocol",
            "format_version": 1,
            "evaluator_version": EVENT_EVALUATOR_VERSION,
            "matching": MATCHING_POLICY,
            "alarm_policy": "contiguous eligible positives; onset at window end; no debounce",
            "warmup": "ignore ineligible decisions; fresh state at first eligible window",
            "truth_span": (
                "fault end > first eligible start and fault start <= last end; positive duration"
            ),
            "latency": "matched alarm onset minus original fault start; nearest-rank p50/p95",
            "grace_ns": window_ns,
            "configuration": asdict(data.configuration),
            "inputs": inputs,
            "policies": policies,
        }
        used = 0

        def reserve(raw: bytes) -> None:
            nonlocal used
            used += len(raw)
            if used > max_bytes:
                raise TraceError("event output quota exceeded")

        def save(name: str, raw: bytes) -> None:
            reserve(raw)
            (staging / name).write_bytes(raw)

        save("protocol.json", canonical_bytes(protocol) + b"\n")
        pooled: dict[tuple[str, str], list[RunEvents]] = {
            (part, model): [] for part in (*SPLITS, *DOMAINS) for model in policies
        }
        digest = sha256()
        with ExitStack() as stack:
            id_scores = stack.enter_context(
                closing(
                    iter_saved_decisions(
                        experiment, frozen.manifest, (k for r in id_runs for k in r.keys), policies
                    )
                )
            )
            ood_keys = stack.enter_context(
                closing(
                    iter_saved_ood_keys(
                        ood_results, ood_meta, (k for r in ood_runs for k in r.keys)
                    )
                )
            )
            ood_scores = stack.enter_context(
                closing(iter_saved_decisions(ood_results, ood_meta, ood_keys, policies))
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
            ledger = stack.enter_context((staging / "runs.jsonl").open("wb"))
            for groups, stream, is_ood in (
                (id_runs, id_scores, False),
                (ood_runs, ood_scores, True),
            ):
                for run in groups:
                    decisions: dict[str, list[bool]] = {name: [] for name in policies}
                    for _ in run.keys:
                        predicted = next(stream)
                        for name in policies:
                            decisions[name].append(predicted[name])
                    faults = _fault_episodes(run.root / "trace", run.summary["duration_ns"])
                    if is_ood:
                        for key in run.keys:
                            expected = {
                                "row_index": key.row_index,
                                "normal_run": not faults,
                                **window_label(key.start_ns, key.end_ns, key.eligible, faults),
                            }
                            if next(labels, None) != expected:
                                raise TraceError("saved OOD truth alignment mismatch")
                    for name, (threshold, comparison) in policies.items():
                        result = evaluate_score_events(
                            run.keys, decisions[name], faults, model_id=name, window_ns=window_ns
                        )
                        pooled[run.split, name].append(result)
                        raw = (
                            canonical_bytes(
                                {
                                    "run_id": run.summary["run_id"],
                                    "split": run.split,
                                    "model_id": name,
                                    "threshold": threshold,
                                    "comparison": comparison,
                                    **asdict(result),
                                }
                            )
                            + b"\n"
                        )
                        reserve(raw)
                        ledger.write(raw)
                        digest.update(raw)
                if next(stream, None) is not None:
                    raise TraceError("extra saved decision windows")
            if next(labels, None) is not None:
                raise TraceError("extra saved OOD truth rows")
        rows: list[dict[str, object]] = []
        fault_rows: list[dict[str, object]] = []
        for (part, name), runs in pooled.items():
            summary = summarize(r.result for r in runs)
            unscored = Counter(f.fault_type for r in runs for f in r.unscored_faults)
            threshold, comparison = policies[name]
            rows.append(
                {
                    "split": part,
                    "model_id": name,
                    "threshold": threshold,
                    "comparison": comparison,
                    **summary,
                    "unscored_fault_episodes": sum(unscored.values()),
                    "unscored_by_fault_type": dict(sorted(unscored.items())),
                    "left_censored_fault_episodes": sum(
                        len(r.left_censored_fault_ids) for r in runs
                    ),
                    "right_censored_fault_episodes": sum(
                        len(r.right_censored_fault_ids) for r in runs
                    ),
                }
            )
            for kind in sorted(set(summary["by_fault_type"]) | set(unscored)):
                detail = summary["by_fault_type"].get(kind)
                fault_rows.append(
                    {
                        "split": part,
                        "model_id": name,
                        "fault_type": kind,
                        "threshold": threshold,
                        "comparison": comparison,
                        **(
                            dict(detail)
                            if detail is not None
                            else {
                                "fault_episodes": 0,
                                "detected_episodes": 0,
                                "detection_rate": None,
                                "detection_latency_ns": {"mean": None, "p50": None, "p95": None},
                            }
                        ),
                        "unscored_fault_episodes": unscored[kind],
                    }
                )
        metrics = {
            "format": "autocan-saved-event-metrics",
            "format_version": 1,
            "evaluator_version": EVENT_EVALUATOR_VERSION,
            "protocol": protocol,
            "rows": rows,
            "fault_type_rows": fault_rows,
        }
        save("metrics.json", canonical_bytes(metrics) + b"\n")
        save("report.md", _report(rows, fault_rows).encode())
        code = sha256()
        for name in (
            "score_events",
            "score_artifacts",
            "event_benchmark",
            "evaluation",
            "model_artifacts",
            "training_artifacts",
        ):
            code.update(
                name.encode() + b"\0" + files("autocan").joinpath(f"{name}.py").read_bytes()
            )
        manifest = _finish(
            staging,
            {
                "format": "autocan-saved-event-evaluation",
                "format_version": 1,
                "status": "complete",
                "evaluator_version": EVENT_EVALUATOR_VERSION,
                "inputs": inputs,
                "lineage": data.manifest["lineage"],
                "configuration": asdict(data.configuration),
                "id_scores_semantic_sha256": frozen.manifest["semantic_sha256"],
                "ood_scores_semantic_sha256": ood_meta["semantic_sha256"],
                "models_trained": [],
                "thresholds_selected": [],
                "scores_computed": 0,
                "seed": frozen.seed,
                "implementation_sha256": code.hexdigest(),
                "package_versions": _packages(),
                **_git_provenance(),
                "semantic_sha256": sha256(
                    canonical_bytes(
                        {"metrics": metrics, "runs_semantic_sha256": digest.hexdigest()}
                    )
                ).hexdigest(),
            },
            {},
            max_bytes,
        )
    return manifest
