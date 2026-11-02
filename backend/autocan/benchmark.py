"""Reproducible, bounded evaluation of fixed rules over verified M7 collections."""

from dataclasses import asdict, replace
from hashlib import sha256
from importlib.metadata import version
from importlib.resources import files
from pathlib import Path

from autocan.clock import SimulationClock
from autocan.evaluation import (
    EVALUATOR_VERSION,
    RULE_IDS,
    SPLITS,
    AlertCollector,
    FaultEpisode,
    RuleResult,
    evaluate_rule,
    make_split,
    summarize,
    validate_split,
)
from autocan.factory import RunSummary, _read_json, verify_factory
from autocan.features import (
    DETECTOR_ID,
    FEATURE_SCHEMA_VERSION,
    BaselineDetector,
    FeatureConfig,
    FeaturePipeline,
    FeatureWindow,
    replay_observations,
)
from autocan.network_profile import MESSAGE_PLANS
from autocan.parquet import _file_hash, _new_output, open_observer
from autocan.traces import TraceError, _artifact_records, _git_provenance, _packages
from autocan.validation import canonical_bytes, integer

DEFAULT_BENCHMARK_BYTES = 16 * 1024 * 1024
MAX_BENCHMARK_BYTES = 64 * 1024 * 1024


def _fault_episodes(trace: Path, end_ns: int) -> list[FaultEpisode]:
    """Read physically separate truth only after feature/detector replay ends."""
    active: dict[str, FaultEpisode] = {}
    episodes = []
    for record in _artifact_records(trace, "faults.jsonl"):
        kind = record["event_kind"]
        if kind == "FaultActivated":
            target = record["target_message_id"]
            can_ids = (
                (integer(target, "target CAN ID"),)
                if target is not None
                else tuple(p.can_id for p in MESSAGE_PLANS if p.source_ecu == record["target_ecu"])
            )
            episode = FaultEpisode(
                str(record["fault_id"]),
                str(record["type"]),
                integer(record["start_at_ns"], "fault start"),
                end_ns,
                can_ids,
                True,
            )
            active[episode.fault_id] = episode
        elif kind == "FaultCleared":
            episode = active.pop(str(record["fault_id"]))
            episodes.append(
                replace(episode, end_ns=integer(record["end_at_ns"], "fault end"), censored=False)
            )
    episodes.extend(active.values())
    return sorted(episodes, key=lambda e: (e.start_ns, e.fault_id))


def _analyze_run(
    root: Path, row: RunSummary, config: FeatureConfig
) -> tuple[dict[str, object], dict[str, RuleResult]]:
    detector = BaselineDetector(config)
    collector = AlertCollector()

    def on_window(window: FeatureWindow) -> None:
        collector.consume(window.end_ns, detector.evaluate(window))

    with open_observer(root / "observer") as source:
        clock = SimulationClock()
        pipeline = FeaturePipeline(
            clock,
            profile_id=source.profile_id,
            receiver_id=source.receiver_id,
            end_ns=source.end_ns,
            config=config,
            on_window=on_window,
        )
        replay_observations(source.observations, clock, pipeline, end_ns=source.end_ns)
        cutoff = source.end_ns // config.window_ns * config.window_ns
    alerts = collector.finish()
    truth = _fault_episodes(root / "trace", row["duration_ns"])
    scored_truth = [
        replace(
            fault,
            end_ns=min(fault.end_ns, cutoff),
            censored=fault.censored or fault.end_ns > cutoff,
        )
        for fault in truth
        if fault.start_ns <= cutoff and fault.end_ns > fault.start_ns
    ]
    exposure = cutoff if not truth else 0
    results = {
        rule: evaluate_rule(
            scored_truth, alerts[rule], grace_ns=config.window_ns, normal_duration_ns=exposure
        )
        for rule in RULE_IDS
    }
    return {
        "index": row["index"],
        "run_id": row["run_id"],
        "duration_ns": row["duration_ns"],
        "evaluated_ns": cutoff,
        "discarded_tail_ns": row["duration_ns"] - cutoff,
        "normal_run": not truth,
        "normal_duration_ns": exposure,
        "unscored_fault_episodes": len(truth) - len(scored_truth),
        "faults": [asdict(fault) for fault in scored_truth],
        "rules": {
            rule: {
                "alerts": [asdict(alert) for alert in result.alerts],
                "matches": [asdict(match) for match in result.matches],
                "metrics": summarize([result]),
            }
            for rule, result in results.items()
        },
    }, results


def _display(value: object) -> str:
    return "n/a" if value is None else f"{value:.4g}" if isinstance(value, float) else str(value)


def _report(rows: list[dict[str, object]], dataset_hash: str, config: FeatureConfig) -> str:
    lines = [
        "# Tier 0 episode benchmark",
        "",
        f"Dataset semantic SHA-256: `{dataset_hash}`.",
        f"Evaluator `{EVALUATOR_VERSION}`; detector `{DETECTOR_ID}`; "
        f"feature schema {FEATURE_SCHEMA_VERSION}.",
        "Fixed demonstration thresholds; no fitting or test-based parameter selection.",
        f"Configuration: `{canonical_bytes(asdict(config)).decode()}`.",
        "",
        "| Split | Rule | Faults | Detected | Alerts | Detection rate | "
        "Episode precision | FA/h normal | Normal seconds |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                _display(row[key])
                for key in (
                    "split",
                    "rule_id",
                    "fault_episodes",
                    "detected_episodes",
                    "alert_episodes",
                    "detection_rate",
                    "episode_precision",
                    "false_alarms_per_hour",
                )
            )
            + f" | {integer(row['normal_duration_ns'], 'normal exposure') / 1e9:g} |"
        )
    lines.extend(
        [
            "",
            "Metrics are pooled counts/exposure, not averages of per-run rates. "
            "`n/a` means no denominator.",
            "Latency mean/nearest-rank p50/p95 and per-fault-type results are in `metrics.json`.",
            "Alarm onsets match one fault each, with affected-CAN agreement "
            "and one window of post-clear grace.",
            "Preexisting alarms cannot detect later faults. "
            "False alarms/hour uses only entirely fault-free runs.",
            "Partial trailing windows are excluded. See `runs.jsonl` for exposure, "
            "labels, alarms and match evidence.",
            "Run/group splits are fixed in `split.json`; no OOD or ML quality claim is made.",
            "",
        ]
    )
    return "\n".join(lines)


def run_benchmark(
    source: Path,
    target: Path,
    *,
    config: FeatureConfig,
    split_seed: int = 42,
    split_path: Path | None = None,
    max_bytes: int = DEFAULT_BENCHMARK_BYTES,
) -> dict[str, object]:
    """Freeze splits before features; stage results and publish the manifest last."""
    if integer(max_bytes, "benchmark quota", minimum=4096) > MAX_BENCHMARK_BYTES:
        raise ValueError("benchmark quota must be <= 64 MiB")
    with _new_output(target) as staging:
        meta = verify_factory(source)
        split = (
            validate_split(meta, _read_json(split_path))
            if split_path is not None
            else make_split(meta, seed=split_seed)
        )
        used = 0

        def reserve(raw: bytes) -> None:
            nonlocal used
            used += len(raw)
            if used > max_bytes:
                raise TraceError("benchmark output quota exceeded")

        def save(name: str, value: bytes) -> None:
            reserve(value)
            (staging / name).write_bytes(value)

        save("split.json", canonical_bytes(split) + b"\n")
        membership = {row["index"]: row["split"] for row in split["assignments"]}
        pooled: dict[tuple[str, str], list[RuleResult]] = {
            (s, r): [] for s in SPLITS for r in RULE_IDS
        }
        semantic_split = {
            **split,
            "assignments": [
                {key: value for key, value in assignment.items() if key != "run_id"}
                for assignment in split["assignments"]
            ],
        }
        digest = sha256(
            canonical_bytes(
                {
                    "evaluator_version": EVALUATOR_VERSION,
                    "split": semantic_split,
                    "configuration": asdict(config),
                    "detector_id": DETECTOR_ID,
                    "feature_schema_version": FEATURE_SCHEMA_VERSION,
                }
            )
        )
        with (staging / "runs.jsonl").open("wb") as stream:
            for run in meta["runs"]:
                result, rules = _analyze_run(source / run["path"], run, config)
                partition = membership[run["index"]]
                result["split"] = partition
                raw = canonical_bytes(result) + b"\n"
                reserve(raw)
                stream.write(raw)
                digest.update(
                    canonical_bytes(
                        {key: value for key, value in result.items() if key != "run_id"}
                    )
                    + b"\n"
                )
                for rule, rule_result in rules.items():
                    pooled[partition, rule].append(rule_result)
        rows = [
            {"split": split_name, "rule_id": rule, **summarize(pooled[split_name, rule])}
            for split_name in SPLITS
            for rule in RULE_IDS
        ]
        metrics = {
            "format": "autocan-tier0-metrics",
            "format_version": 1,
            "evaluator_version": EVALUATOR_VERSION,
            "detector_id": DETECTOR_ID,
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "dataset_semantic_sha256": meta["semantic_sha256"],
            "configuration": asdict(config),
            "split_seed": split["seed"],
            "runs": len(meta["runs"]),
            "rows": rows,
        }
        save("metrics.json", canonical_bytes(metrics) + b"\n")
        digest.update(canonical_bytes(metrics))
        save("report.md", _report(rows, meta["semantic_sha256"], config).encode())
        implementation = sha256()
        for name in ("features", "evaluation", "benchmark"):
            implementation.update(
                name.encode() + b"\0" + files("autocan").joinpath(f"{name}.py").read_bytes()
            )
        manifest: dict[str, object] = {
            "format": "autocan-tier0-benchmark",
            "format_version": 1,
            "status": "complete",
            "evaluator_version": EVALUATOR_VERSION,
            "detector_id": DETECTOR_ID,
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "configuration": asdict(config),
            "dataset_semantic_sha256": meta["semantic_sha256"],
            "source_manifest_sha256": _file_hash(source / "manifest.json"),
            "implementation_sha256": implementation.hexdigest(),
            "package_versions": {**_packages(), "pyarrow": version("pyarrow")},
            **_git_provenance(),
            "semantic_sha256": digest.hexdigest(),
            "artifacts": {
                path.name: {"bytes": path.stat().st_size, "sha256": _file_hash(path)}
                for path in sorted(staging.iterdir())
            },
        }
        save("manifest.json", canonical_bytes(manifest) + b"\n")
    return manifest
