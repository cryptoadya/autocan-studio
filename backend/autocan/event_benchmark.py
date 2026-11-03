"""Evaluate saved ID/OOD window decisions as temporal fault/alert episodes."""

from collections import Counter
from dataclasses import asdict
from hashlib import sha256
from importlib.resources import files
from pathlib import Path

from autocan.evaluation import SPLITS, summarize
from autocan.ood import DOMAINS
from autocan.parquet import _finish, _new_output
from autocan.saved_evaluation import load_saved_evaluation
from autocan.score_events import EVENT_EVALUATOR_VERSION, MATCHING_POLICY, RunEvents
from autocan.traces import TraceError, _git_provenance, _packages
from autocan.validation import canonical_bytes, integer, object_value
from autocan.window_benchmark import DEFAULT_OUTPUT_BYTES, MAX_OUTPUT_BYTES


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
        saved = load_saved_evaluation(
            prepared,
            source=source,
            experiment=experiment,
            ood_results=ood_results,
            suite=suite,
            reference=reference,
        )
        data, frozen, ood_meta = saved.data, saved.frozen, saved.ood_meta
        policies, inputs = saved.policies, saved.inputs
        window_ns = data.configuration.window_ns
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
        with (staging / "runs.jsonl").open("wb") as ledger:
            for run in saved.runs:
                for name, (threshold, comparison) in policies.items():
                    result = run.events[name]
                    pooled[run.split, name].append(result)
                    raw = (
                        canonical_bytes(
                            {
                                "run_id": run.run_id,
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
            "saved_evaluation",
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
