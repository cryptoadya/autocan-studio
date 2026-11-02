"""Evaluate fixed rules on validated, saved M9 windows and frozen splits."""

from contextlib import closing
from dataclasses import asdict
from hashlib import sha256
from importlib.metadata import version
from importlib.resources import files
from pathlib import Path

from autocan.evaluation import RULE_IDS, SPLITS
from autocan.features import DETECTOR_ID, FEATURE_SCHEMA_VERSION
from autocan.parquet import _file_hash, _new_output
from autocan.traces import TraceError, _git_provenance, _packages
from autocan.training_artifacts import load_training_data
from autocan.validation import canonical_bytes, integer
from autocan.window_evaluation import (
    WINDOW_EVALUATOR_VERSION,
    WindowMetrics,
    WindowScore,
    WindowTruth,
    baseline_window_scores,
    evaluate_windows,
)

WINDOW_BENCHMARK_VERSION = "saved-window-rules-1"
DEFAULT_OUTPUT_BYTES = 16 * 1024 * 1024
MAX_OUTPUT_BYTES = 64 * 1024 * 1024


def _report(
    rows: list[WindowMetrics],
    prepared_hash: str,
    *,
    model_note: str | None = None,
    title: str | None = None,
) -> str:
    lines = [
        "# "
        + (
            title
            or (
                "IsolationForest and fixed-rule comparison"
                if model_note
                else "Fixed-rule window evaluation"
            )
        ),
        "",
        f"Prepared dataset semantic SHA-256: `{prepared_hash}`.",
        f"Evaluator `{WINDOW_EVALUATOR_VERSION}`; detector `{DETECTOR_ID}`.",
        model_note or "Saved splits/configuration; no fitting or threshold selection.",
        "",
        "| Split | Rule | Threshold | Decision | Windows | Positive | "
        "Precision | Recall | F1 | AP |",
        "| --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        cells = [
            row["split"],
            row["model_id"],
            row["threshold"],
            row["comparison"],
            row["evaluated_windows"],
            row["positive_windows"],
            row["precision"],
            row["recall"],
            row["f1"],
            row["average_precision"],
        ]
        lines.append(
            "| "
            + " | ".join(
                "n/a" if v is None else f"{v:.6g}" if isinstance(v, float) else str(v)
                for v in cells
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "`ge` means >=; `gt` means >. Undefined denominators are n/a.",
            "Warm-up is excluded; all eligible windows remain. Missing rule evidence scores zero.",
            "Confusion matrices and full PR curves are in metrics.json. Matrices use true rows and",
            "predicted columns, both [normal, anomaly]: [[TN, FP], [FN, TP]].",
            "These are intervention window metrics, distinct from M8 alert episode metrics.",
            "No test/OOD tuning or real-vehicle quality claim is made."
            if model_note
            else "No training, test/OOD tuning, or real-vehicle quality claim is made.",
            "",
        ]
    )
    return "\n".join(lines)


def evaluate_saved_rules(
    prepared: Path, target: Path, *, source: Path, max_bytes: int = DEFAULT_OUTPUT_BYTES
) -> dict[str, object]:
    if integer(max_bytes, "window evaluation output quota", minimum=4096) > MAX_OUTPUT_BYTES:
        raise TraceError("window evaluation output quota exceeds 64 MiB")
    with _new_output(target) as staging:
        data = load_training_data(prepared, source=source)
        targets = []
        predictions: dict[str, list[WindowScore]] = {rule: [] for rule in RULE_IDS}
        policy = None
        with closing(data.iter_windows()) as windows:
            for window in windows:
                raw = dict(zip(data.columns, window.features, strict=True))
                evidence = baseline_window_scores(raw, data.configuration)
                if policy is None:
                    policy = evidence
                targets.append(WindowTruth(window.key, window.label))
                for rule in RULE_IDS:
                    predictions[rule].append(WindowScore(window.key, evidence[rule].score))
        assert policy is not None  # validated preparation requires nonempty coverage
        rows = [
            evaluate_windows(
                targets,
                predictions[rule],
                model_id=rule,
                split=part,
                threshold=policy[rule].threshold,
                comparison=policy[rule].comparison,
            )
            for part in SPLITS
            for rule in RULE_IDS
        ]
        metrics = {
            "format": "autocan-window-rule-metrics",
            "format_version": 1,
            "benchmark_version": WINDOW_BENCHMARK_VERSION,
            "evaluator_version": WINDOW_EVALUATOR_VERSION,
            "detector_id": DETECTOR_ID,
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "prepared_semantic_sha256": data.manifest["semantic_sha256"],
            "lineage": data.manifest["lineage"],
            "configuration": asdict(data.configuration),
            "missing_evidence_policy": "zero score; retain every eligible window",
            "rows": rows,
        }
        used = 0

        def save(name: str, raw: bytes) -> None:
            nonlocal used
            used += len(raw)
            if used > max_bytes:
                raise TraceError("window evaluation output quota exceeded")
            (staging / name).write_bytes(raw)

        save("metrics.json", canonical_bytes(metrics) + b"\n")
        save("split.json", (prepared / "split.json").read_bytes())
        save("report.md", _report(rows, str(data.manifest["semantic_sha256"])).encode())
        implementation = sha256()
        for name in ("training_artifacts", "window_evaluation", "window_benchmark"):
            implementation.update(
                name.encode() + b"\0" + files("autocan").joinpath(f"{name}.py").read_bytes()
            )
        manifest: dict[str, object] = {
            "format": "autocan-window-rule-evaluation",
            "format_version": 1,
            "status": "complete",
            "benchmark_version": WINDOW_BENCHMARK_VERSION,
            "evaluator_version": WINDOW_EVALUATOR_VERSION,
            "detector_id": DETECTOR_ID,
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "prepared_manifest_sha256": _file_hash(prepared / "manifest.json"),
            "prepared_semantic_sha256": data.manifest["semantic_sha256"],
            "source_manifest_sha256": data.manifest["source_manifest_sha256"],
            "lineage": data.manifest["lineage"],
            "configuration": asdict(data.configuration),
            "implementation_sha256": implementation.hexdigest(),
            "package_versions": {**_packages(), "pyarrow": version("pyarrow")},
            **_git_provenance(),
            "models_trained": [],
            "semantic_sha256": sha256(canonical_bytes(metrics)).hexdigest(),
            "artifacts": {
                path.name: {"bytes": path.stat().st_size, "sha256": _file_hash(path)}
                for path in sorted(staging.iterdir())
            },
        }
        save("manifest.json", canonical_bytes(manifest) + b"\n")
    return manifest
