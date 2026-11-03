"""Reproduce and consolidate the frozen M9 experiment without fitting or inference."""

from collections.abc import Callable
from dataclasses import asdict
from functools import partial
from hashlib import sha256
from importlib.resources import files
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from autocan.acceptance_artifacts import compare_scientific as compare_scientific
from autocan.acceptance_artifacts import digest_json, published
from autocan.acceptance_artifacts import fingerprints as fingerprints
from autocan.acceptance_runtime import verify_runtime
from autocan.bootstrap_benchmark import bootstrap_saved_metrics
from autocan.evaluation import RULE_IDS, SPLITS
from autocan.event_benchmark import evaluate_saved_events as evaluate_saved_events
from autocan.factory import _read_json
from autocan.model_artifacts import MODEL_IDS
from autocan.ood import DOMAINS
from autocan.parquet import _file_hash, _finish, _new_output
from autocan.saved_evaluation import SavedEvaluation, load_saved_evaluation
from autocan.traces import TraceError, _git_provenance
from autocan.validation import canonical_bytes, integer, json_object, object_value
from autocan.window_benchmark import DEFAULT_OUTPUT_BYTES, MAX_OUTPUT_BYTES
from autocan.window_evaluation import WindowScore, WindowTruth, evaluate_windows

ACCEPTANCE_VERSION = "frozen-m9-acceptance-1"
EVENT_FILES = {"metrics.json", "protocol.json", "report.md", "runs.jsonl"}
BOOTSTRAP_FILES = {"metrics.json", "protocol.json", "report.md"}


def _keyed(rows: list[dict[str, Any]], fields: tuple[str, ...]) -> dict[tuple[str, ...], Any]:
    result = {}
    for row in rows:
        key = tuple(row[f] for f in fields)
        if key in result:
            raise TraceError("duplicate acceptance metric row")
        result[key] = row
    return result


def _windows(saved: SavedEvaluation, experiment: Path, ood_results: Path) -> list[dict[str, Any]]:
    if set(saved.policies) != set((*RULE_IDS, *MODEL_IDS)):
        raise TraceError("acceptance requires all three rules and both frozen forests")
    id_metrics: dict[str, Any] = _read_json(experiment / "metrics.json", MAX_OUTPUT_BYTES)
    ood_metrics: dict[str, Any] = _read_json(ood_results / "metrics.json", MAX_OUTPUT_BYTES)
    archived = _keyed([*id_metrics["rows"], *ood_metrics["rows"]], ("split", "model_id"))
    actual = []
    for parts in (SPLITS, DOMAINS):
        runs = [r for r in saved.runs if r.split in parts]
        truths = [
            WindowTruth(k, label) for r in runs for k, label in zip(r.keys, r.labels, strict=True)
        ]
        for model, (threshold, comparison) in saved.policies.items():
            scores = [
                WindowScore(k, score)
                for r in runs
                for k, score in zip(r.keys, r.scores[model], strict=True)
            ]
            for part in parts:
                row = evaluate_windows(
                    truths,
                    scores,
                    model_id=model,
                    split=part,
                    threshold=threshold,
                    comparison=comparison,
                )
                actual.append(dict(row))
    compare_scientific(_keyed(actual, ("split", "model_id")), archived, "window metrics")
    fault_rows = []
    for kind in sorted(
        {r.fault_type for r in saved.runs if r.split == "ood_fault" and r.fault_type}
    ):
        runs = [r for r in saved.runs if r.split == "ood_fault" and r.fault_type == kind]
        truth = [
            WindowTruth(k, label) for r in runs for k, label in zip(r.keys, r.labels, strict=True)
        ]
        for model, (threshold, comparison) in saved.policies.items():
            prediction = [
                WindowScore(k, score)
                for r in runs
                for k, score in zip(r.keys, r.scores[model], strict=True)
            ]
            fault_rows.append(
                {
                    "fault_type": kind,
                    **evaluate_windows(
                        truth,
                        prediction,
                        model_id=model,
                        split="ood_fault",
                        threshold=threshold,
                        comparison=comparison,
                    ),
                }
            )
    compare_scientific(
        _keyed(fault_rows, ("fault_type", "model_id")),
        _keyed(ood_metrics["fault_type_rows"], ("fault_type", "model_id")),
        "OOD per-fault windows",
    )
    return actual


def _replay(
    role: str,
    original: Path,
    archived: dict[str, Any],
    build: Callable[[Path], dict[str, object]],
    staging: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    with TemporaryDirectory(prefix=f"{role}-", dir=staging) as temporary:
        a, b = Path(temporary) / "one", Path(temporary) / "two"
        fresh = build(a)
        build(b)
        for name in archived["artifacts"]:
            if (a / name).read_bytes() != (b / name).read_bytes():
                raise TraceError(f"{role} repeatability check failed: {name}")
            if name.endswith(".json"):
                compare_scientific(
                    _read_json(a / name, MAX_OUTPUT_BYTES),
                    _read_json(original / name, MAX_OUTPUT_BYTES),
                    f"{role}.{name}",
                )
            elif name.endswith(".jsonl"):
                with (a / name).open() as first, (original / name).open() as previous:
                    from itertools import zip_longest

                    for line, old in zip_longest(first, previous):
                        if line is None or old is None:
                            raise TraceError("event ledger coverage mismatch")
                        compare_scientific(json_object(line), json_object(old), "event ledger")
        metadata_fields: tuple[str, ...] = (
            "format",
            "format_version",
            "inputs",
            "lineage",
            "id_scores_semantic_sha256",
            "ood_scores_semantic_sha256",
            "models_trained",
            "thresholds_selected",
            "scores_computed",
            "package_versions",
        )
        metadata_fields += (
            ("seed", "configuration", "evaluator_version")
            if role == "events"
            else ("experiment_seed", "bootstrap_seed", "replicates", "bootstrap_version")
        )
        for field in metadata_fields:
            compare_scientific(archived[field], fresh[field], f"{role}.manifest.{field}")
        metrics = _read_json(a / "metrics.json", MAX_OUTPUT_BYTES)
        previous_metrics = _read_json(original / "metrics.json", MAX_OUTPUT_BYTES)
        content = (
            {
                "metrics": previous_metrics,
                "runs_semantic_sha256": _file_hash(original / "runs.jsonl"),
            }
            if role == "events"
            else previous_metrics
        )
        if archived["semantic_sha256"] != digest_json(content):
            raise TraceError(f"{role} semantic checksum mismatch")
        return metrics, {
            "runs": 2,
            "scientific_artifacts_byte_identical": True,
            "archived_numeric_absolute_tolerance": 1e-12,
            "relative_tolerance": 0,
            "scientific_artifact_sha256": {
                name: _file_hash(a / name) for name in archived["artifacts"]
            },
            "archived_semantic_sha256": archived["semantic_sha256"],
            "recomputed_semantic_sha256": fresh["semantic_sha256"],
            "archived_implementation_sha256": archived["implementation_sha256"],
            "recomputed_implementation_sha256": fresh["implementation_sha256"],
        }


def verify_normal_alarms(rows: list[dict[str, Any]], archived: list[dict[str, Any]]) -> None:
    actual = []
    for row in rows:
        if row["split"] != "ood_normal":
            continue
        event = row["event"]
        actual.append(
            {
                "split": "ood_normal",
                "model_id": row["model_id"],
                "false_positive_windows": row["window"]["fp"]["estimate"],
                "false_alarm_episodes": event["normal_false_alarm_episodes"]["estimate"],
                "normal_duration_ns": event["normal_duration_ns"]["estimate"],
                "false_alarms_per_hour": event["false_alarms_per_hour"]["estimate"],
            }
        )
    compare_scientific(
        _keyed(actual, ("model_id",)), _keyed(archived, ("model_id",)), "OOD normal false alarms"
    )


def _cell(value: dict[str, Any], scale: float = 1) -> str:
    def fmt(v: Any) -> str:
        return "n/a" if v is None else f"{v / scale:.6g}"

    return f"{fmt(value['estimate'])} [{fmt(value['lower'])}, {fmt(value['upper'])}]"


def _table(cells: list[object]) -> str:
    return "| " + " | ".join(str(cell) for cell in cells) + " |"


def _report(result: dict[str, Any]) -> str:
    lines = [
        "# M9 frozen-artifact acceptance",
        "",
        f"Verdict: **{result['verdict']}**.",
        "Acceptance is restricted to the verified frozen synthetic experiment pipeline.",
        "It establishes no operational generalization or real-vehicle diagnostic quality.",
        "",
        "Training rows are fit data; validation is in-selection. Test and OOD scores are reused,",
        "with no fitting, threshold selection, model selection, new scores or datasets.",
        "Both event/bootstrap replays are byte-identical across two independent evaluations.",
        (
            "Archived scientific JSON agrees with exact counts/nulls and "
            "absolute float tolerance 1e-12."
        ),
        "Input file fingerprints were unchanged before/after verification.",
        f"Seeds: dataset {result['dataset_seed']}; models {result['experiment_seed']}; "
        f"bootstrap {result['bootstrap_protocol']['bootstrap_seed']}.",
        "",
        "## Evidence identities",
        "",
    ]
    for name, value in result["dataset_versions"].items():
        lines.append(f"- {name} manifest SHA-256: `{value}`.")
    bridge = result["runtime"]["identity_bridge"]
    lines.extend(
        [
            "- Quality experiment semantic SHA-256: "
            f"`{bridge['quality_experiment_semantic_sha256']}`.",
            "- Runtime experiment semantic SHA-256: "
            f"`{bridge['capture_experiment_semantic_sha256']}`.",
            (
                "- Runtime model pickles, preprocessing, thresholds, numeric "
                "features/labels/scaled tables"
            ),
            (
                "  and saved scores match by content. Experiment, split and run "
                "identities remain distinct."
            ),
            "- Different runtime/quality metadata: "
            + ", ".join(bridge["different_metadata"] or ["none"])
            + ".",
            "",
            "## Coverage",
            "",
            (
                "| Partition | Runs | Entirely normal runs | Eligible windows | "
                "Positive windows | Normal seconds |"
            ),
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in result["coverage"]:
        lines.append(
            _table(
                [
                    row["split"],
                    row["runs"],
                    row["normal_runs"],
                    row["eligible_windows"],
                    row["positive_windows"],
                    f"{row['normal_duration_ns'] / 1e9:.6g}",
                ]
            )
        )
    lines.extend(
        [
            "",
            "## Window comparison",
            "",
            "Dataset versions are the manifest hashes above: prepared for ID, suite for OOD.",
            "Cells show point estimate [95% run-bootstrap interval]. `ge` means >=; `gt` means >.",
            (
                "| Dataset | Split | Detector | Threshold | Decision | Precision "
                "| Recall | F1 | AP | TN/FP/FN/TP |"
            ),
            "| --- | --- | --- | ---: | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in result["rows"]:
        window = row["window"]
        confusion = "/".join(str(window[k]["estimate"]) for k in ("tn", "fp", "fn", "tp"))
        cells = " | ".join(
            _cell(window[k]) for k in ("precision", "recall", "f1", "average_precision")
        )
        lines.append(
            f"| {row['dataset']} | {row['split']} | {row['model_id']} | {row['threshold']:.6g} | "
            f"{row['comparison']} | {cells} | {confusion} |"
        )
    lines.extend(
        [
            "",
            "## Fault events and false alarms",
            "",
            (
                "Latency is conditional on detection, in **virtual ms**, not "
                "wall-clock inference time."
            ),
            "Normal exposure includes only entirely normal runs. FA/hour is exposure-normalized.",
            (
                "| Split | Detector | Detection rate | Latency p50 ms | Latency "
                "p95 ms | Normal alarms | Normal FA/hour |"
            ),
            "| --- | --- | --- | --- | --- | ---: | --- |",
        ]
    )
    for row in result["rows"]:
        e = row["event"]
        lines.append(
            _table(
                [
                    row["split"],
                    row["model_id"],
                    _cell(e["detection_rate"]),
                    _cell(e["latency_p50_ns"], 1e6),
                    _cell(e["latency_p95_ns"], 1e6),
                    e["normal_false_alarm_episodes"]["estimate"],
                    _cell(e["false_alarms_per_hour"]),
                ]
            )
        )
    lines.extend(
        [
            "",
            "## Per-fault event evidence",
            "",
            (
                "Intervals require at least two contributing runs. JSON retains "
                "every valid/undefined count."
            ),
            "| Split | Type | Detector | Contributing runs | Detection rate | p50 ms | p95 ms |",
            "| --- | --- | --- | ---: | --- | --- | --- |",
        ]
    )
    for row in result["fault_type_rows"]:
        e = row["event"]
        lines.append(
            _table(
                [
                    row["split"],
                    row["fault_type"],
                    row["model_id"],
                    row["contributing_runs"],
                    _cell(e["detection_rate"]),
                    _cell(e["latency_p50_ns"], 1e6),
                    _cell(e["latency_p95_ns"], 1e6),
                ]
            )
        )
    runtime = result["runtime"]
    lines.extend(
        [
            "",
            "## Historical runtime observation (separate evidence)",
            "",
            f"Local Apple M1 evidence: **{runtime['local_m1_observation']}**; "
            f"CI: **{runtime['environment']['ci']}**. OS: {runtime['environment']['os']}.",
            (
                "No performance measurement was rerun. The original "
                "implementation/configuration is retained in JSON."
            ),
            (
                "These are batch-call **wall-clock ms** and full worker "
                "high-water RSS; no cross-machine timing comparison."
            ),
            (
                "Rule calls include the joint three-rule helper. Rule "
                "estimator-only timing is not applicable."
            ),
            "| Detector | Batch | Scope | p50 ms | p95 ms | Windows/s | Peak RSS MiB |",
            "| --- | ---: | --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in runtime["measurements"]:
        m = row["metrics"]
        lines.append(
            f"| {row['detector']} | {row['batch_size']} | {row['mode']} | "
            f"{m['batch_latency_ms_p50']:.6g} | {m['batch_latency_ms_p95']:.6g} | "
            f"{m['windows_per_second']:.6g} | {row['peak_process_rss_bytes'] / 2**20:.3f} |"
        )
    lines.extend(["", "## Acceptance boundary and limitations", "", *result["limitations"], ""])
    return "\n".join(lines)


def accept_m9(
    prepared: Path,
    target: Path,
    *,
    source: Path,
    experiment: Path,
    ood_results: Path,
    suite: Path,
    reference: Path,
    events: Path,
    bootstrap: Path,
    inference: Path,
    max_bytes: int = DEFAULT_OUTPUT_BYTES,
) -> dict[str, object]:
    if integer(max_bytes, "acceptance output quota", minimum=4096) > MAX_OUTPUT_BYTES:
        raise TraceError("acceptance quota exceeds 64 MiB")
    roots = dict(
        prepared=prepared,
        source=source,
        experiment=experiment,
        ood_results=ood_results,
        suite=suite,
        reference=reference,
        events=events,
        bootstrap=bootstrap,
        inference=inference,
    )
    for root in roots.values():
        a, b = target.resolve(), root.resolve()
        if a == b or a in b.parents or b in a.parents:
            raise TraceError("acceptance output must not overlap an input directory")
    with _new_output(target) as staging:
        try:
            return _accept(roots, staging, max_bytes)
        except (KeyError, TypeError, AttributeError, IndexError) as exc:
            raise TraceError("invalid acceptance evidence schema") from exc


def _accept(roots: dict[str, Path], staging: Path, max_bytes: int) -> dict[str, object]:
    before = fingerprints(roots)
    old_events = published(roots["events"], "autocan-saved-event-evaluation", EVENT_FILES)
    old_bootstrap = published(roots["bootstrap"], "autocan-run-bootstrap", BOOTSTRAP_FILES)
    if old_events.get("scores_computed") != 0 or old_bootstrap.get("scores_computed") != 0:
        raise TraceError("acceptance requires saved-score evidence")
    options = {
        name: roots[name] for name in ("source", "experiment", "ood_results", "suite", "reference")
    }
    saved = load_saved_evaluation(roots["prepared"], **options)
    compare_scientific(old_events["seed"], saved.frozen.seed, "events model seed")
    compare_scientific(old_bootstrap["experiment_seed"], saved.frozen.seed, "bootstrap model seed")
    source_configuration = object_value(
        _read_json(roots["source"] / "manifest.json")["configuration"],
        "source configuration",
    )
    source_seed = integer(source_configuration.get("seed"), "dataset seed")
    runtime = verify_runtime(roots["inference"], saved.data.manifest, saved.frozen.manifest)
    window_rows = _windows(saved, roots["experiment"], roots["ood_results"])
    _, event_repeat = _replay(
        "events",
        roots["events"],
        old_events,
        partial(evaluate_saved_events, roots["prepared"], **options, max_bytes=max_bytes),
        staging,
    )
    boot, bootstrap_repeat = _replay(
        "bootstrap",
        roots["bootstrap"],
        old_bootstrap,
        partial(
            bootstrap_saved_metrics,
            roots["prepared"],
            **options,
            max_bytes=max_bytes,
            replicates=integer(old_bootstrap["replicates"], "frozen replicates", minimum=2),
            seed=integer(old_bootstrap["bootstrap_seed"], "frozen bootstrap seed"),
        ),
        staging,
    )
    ood_metrics: dict[str, Any] = _read_json(
        roots["ood_results"] / "metrics.json", MAX_OUTPUT_BYTES
    )
    verify_normal_alarms(boot["rows"], ood_metrics["normal_false_alarms"])
    point_rows = _keyed(window_rows, ("split", "model_id"))
    for row in boot["rows"]:
        point = point_rows[row["split"], row["model_id"]]
        for name in (
            "precision",
            "recall",
            "f1",
            "average_precision",
            "evaluated_windows",
            "positive_windows",
            "negative_windows",
        ):
            compare_scientific(
                row["window"][name]["estimate"], point[name], f"bootstrap point {name}"
            )
        compare_scientific(
            [row["window"][k]["estimate"] for k in ("tn", "fp", "fn", "tp")],
            [n for pair in point["confusion_matrix"]["counts"] for n in pair],
        )
        row["dataset"] = "prepared" if row["split"] in SPLITS else "suite"
    coverage = []
    for part in (*SPLITS, *DOMAINS):
        runs = [r for r in saved.runs if r.split == part]
        normal = [r for r in runs if r.fault_type is None]
        coverage.append(
            {
                "split": part,
                "runs": len(runs),
                "normal_runs": len(normal),
                "eligible_windows": sum(k.eligible for r in runs for k in r.keys),
                "positive_windows": sum(v is True for r in runs for v in r.labels),
                "normal_duration_ns": sum(
                    k.end_ns - k.start_ns for r in normal for k in r.keys if k.eligible
                ),
            }
        )
    if fingerprints(roots) != before:
        raise TraceError("acceptance inputs changed during verification")
    result = {
        "format": "autocan-m9-frozen-acceptance",
        "format_version": 1,
        "acceptance_version": ACCEPTANCE_VERSION,
        "verdict": "accepted_frozen_synthetic_pipeline"
        if runtime["local_m1_observation"]
        else "verified_without_local_m1_evidence",
        "scope": (
            "frozen synthetic experiment software; no generalization or real-vehicle acceptance"
        ),
        "dataset_versions": saved.inputs,
        "dataset_seed": source_seed,
        "experiment_seed": saved.frozen.seed,
        "source_configuration": source_configuration,
        "feature_configuration": asdict(saved.data.configuration),
        "lineage": saved.data.manifest["lineage"],
        "coverage": coverage,
        "inputs_preserved": True,
        "frozen_policies": saved.policies,
        "bootstrap_protocol": boot["protocol"],
        "repeatability": {"events": event_repeat, "bootstrap": bootstrap_repeat},
        "rows": boot["rows"],
        "fault_type_rows": boot["fault_type_rows"],
        "runtime": runtime,
        "limitations": [
            (
                "Models, preprocessing, cutoffs, ID/OOD memberships, bootstrap "
                "seed and repetitions were reused without selection."
            ),
            (
                "Hashes verify integrity/compatibility, not authenticity or "
                "historical proof that test was observed only once."
            ),
            (
                "The finite synthetic collection provides no broad "
                "operating-regime or real-CAN generalization guarantee."
            ),
            (
                "Normal OOD false alarms are reported alongside detection; "
                "weak/negative results are valid scientific outcomes."
            ),
            (
                "Few contributing runs cause undefined/suppressed or degenerate "
                "confidence intervals; resampling adds no new data."
            ),
            (
                "Intervals condition on fitted models and selected validation "
                "cutoffs; training/selection uncertainty is excluded."
            ),
            (
                "Latency conditions on detected events; inspect misses and "
                "censoring with every latency summary."
            ),
            (
                "Runtime evidence is historical and machine/session specific; it "
                "is not CAN-to-alert latency or a timing regression gate."
            ),
            (
                "Live inference, UI, affected-message attribution and final V2 "
                "acceptance belong to later work. No release is implied."
            ),
        ],
    }
    for name, raw in (
        ("acceptance.json", canonical_bytes(result) + b"\n"),
        ("report.md", _report(result).encode()),
    ):
        if len(raw) > max_bytes:
            raise TraceError("acceptance output quota exceeded")
        (staging / name).write_bytes(raw)
    implementation = sha256()
    for path in sorted(files("autocan").iterdir(), key=lambda p: p.name):
        if path.name.endswith(".py"):
            implementation.update(path.name.encode() + b"\0" + path.read_bytes())
    return _finish(
        staging,
        {
            "format": "autocan-m9-acceptance-manifest",
            "format_version": 1,
            "status": "complete",
            "acceptance_version": ACCEPTANCE_VERSION,
            "inputs": before,
            "input_locations": {name: str(path) for name, path in roots.items()},
            "models_trained": [],
            "thresholds_selected": [],
            "scores_computed": 0,
            "package_versions": saved.frozen.manifest["package_versions"],
            "implementation_sha256": implementation.hexdigest(),
            **_git_provenance(),
            "semantic_sha256": digest_json(result),
            "runtime_rerun": False,
            "dataset_seed": source_seed,
            "experiment_seed": saved.frozen.seed,
            "bootstrap_seed": old_bootstrap["bootstrap_seed"],
        },
        {},
        max_bytes,
    )
